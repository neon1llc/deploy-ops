# tests/test_gcp_receipts.py - Bind separate GCP infrastructure and Cloud Run workers to durable public evidence.


import copy
import json
import subprocess

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from deploy_ops.api import create_app
from deploy_ops.config import Settings, Target, fingerprint
from deploy_ops.delivery.engine import Engine
from deploy_ops.delivery.process import Process
from deploy_ops.delivery.scaffold import create
from deploy_ops.demo import sample_proposal, sample_source, sample_target
from deploy_ops.errors import Refused
from deploy_ops.models import Claim, Completion, Decision, Proposal, Resolution
from deploy_ops.receipts import controlled_backend, extract_state, verify_backend
from deploy_ops.service import Controller
from deploy_ops.store import Store


# Keep all project, identity and state values synthetic
OUTPUTS = {"project": "fixture-project", "region": "us-central1", "name": "web",
           "image_repository": "us-central1-docker.pkg.dev/fixture-project/apps/web"}
IMAGE = OUTPUTS["image_repository"] + "@sha256:" + "a" * 64
BACKEND = {"bucket": "fixture-state", "prefix": "development/cloud-run"}
FORMAT = "gcp-cloud-run-v1"
VERSIONS = {"opentofu": "1.10.0", "terraform": "1.12.2"}


# Include unrelated private state to ensure extraction does not export it
def state(serial=1):
    return {"lineage": "11111111-1111-1111-1111-111111111111", "serial": serial,
            "outputs": {name: {"sensitive": False, "type": "string", "value": value} for name, value in OUTPUTS.items()},
            "resources": [{"private": "private-state-fixture-marker"}]}


# Register the same protected source boundary for each native saved-plan engine
@pytest.fixture(params=["opentofu", "terraform"])
def setup(tmp_path, request):
    path = create(tmp_path / "workspace")
    path.write_text('''name = "hello"
[repositories.infrastructure]
path = "infrastructure"
allow_uncommitted = true
[repositories.application]
path = "application"
allow_uncommitted = true
[environments.development]
provider = "gcp"
[components.foundation]
repository = "infrastructure"
kind = "infrastructure"
adapter = "opentofu"
[components.web]
repository = "application"
kind = "application"
adapter = "cloud-run"
needs = ["foundation"]
[components.web.options]
project_id = "${outputs.foundation.project}"
region = "${outputs.foundation.region}"
name = "${outputs.foundation.name}"
image_repository = "${outputs.foundation.image_repository}"
''')
    engine = Engine(path, controller="https://controller.example")
    producer = sample_target().model_dump()
    identity = {"workload_identity_provider": "projects/123456789/locations/global/workloadIdentityPools/fixture/providers/github",
                "service_account": "infra-executor@fixture-project.iam.gserviceaccount.com", "project_id": OUTPUTS["project"]}
    producer.update(cloud="gcp", repository="example/infrastructure", repository_id="201", environment="development",
                    planner_environment="development-plan", engines=[request.param], receipt_region=OUTPUTS["region"],
                    identity=identity, planner_identity={**identity, "service_account": "infra-reader@fixture-project.iam.gserviceaccount.com"},
                    receipt_outputs={"format": FORMAT, "backend": BACKEND},
                    policy={"require_cost_estimate": False})
    consumer = copy.deepcopy(producer)
    consumer.update(repository="example/application", repository_id="101", engines=["delivery"],
                    receipt_outputs=None, delivery_digest=engine.spec.digest,
                    receipt_dependencies={"foundation": "infrastructure"})
    consumer["identity"]["service_account"] = "app-executor@fixture-project.iam.gserviceaccount.com"
    consumer["planner_identity"]["service_account"] = "app-reader@fixture-project.iam.gserviceaccount.com"
    settings = Settings(targets={"infrastructure": Target.model_validate(producer),
                                 "hello-development-web": Target.model_validate(consumer)},
                        database=str(tmp_path / "controller.db"))
    clock = [1800000000]
    return Controller(settings, Store(settings.database), lambda: clock[0]), engine, clock, request.param


# Submit a native GCP plan bound to the registered GCS backend
def proposal(controller, engine):
    value = sample_proposal().model_dump()
    value.update(target="infrastructure", engine=engine, tool_version=VERSIONS[engine])
    value["preview"]["terraform_version"] = VERSIONS[engine]
    value["preview"]["deploy_ops_backend"] = BACKEND.copy()
    value["preview"]["resource_changes"] = [{"address": "google_cloud_run_v2_service.web",
        "type": "google_cloud_run_v2_service", "change": {"actions": ["update"]}}]
    return Proposal.model_validate(value)


# Consume an exact manual approval before any synthetic writer can report outputs
def claim(controller, value, run="1001"):
    target = controller.target(value.target)
    request = controller.submit(value, sample_source(target, run=run))
    controller.decide(request["id"], Decision(digest=request["digest"], decision="approve"), "operator")
    lease = controller.claim(request["id"], Claim(digest=request["digest"], artifact_digest=value.artifact_digest),
                             sample_source(target, "execute", run))
    return request, lease


# Issue only the public state schema after successful saved-plan execution
def produce(setup, run="1001", complete=True, serial=1):
    controller, _, _, engine = setup
    request, lease = claim(controller, proposal(controller, engine), run)
    if complete:
        evidence = extract_state(json.dumps(state(serial)).encode(), FORMAT)
        request = controller.complete(request["id"], lease["token"],
            Completion(outcome="succeeded", exit_code=0, infrastructure=evidence))
    return request, lease


# Run repeated app-only deliveries through real controller routes and the Cloud Run adapter
def test_two_native_engines_feed_app_only_cloud_run_without_reapplying(setup, monkeypatch):
    controller, engine, clock, _ = setup
    receipt = produce(setup)[0]["output_receipt"]
    assert receipt["project"] == OUTPUTS["project"]
    assert receipt["format"] == FORMAT
    assert "private-state-fixture-marker" not in json.dumps(receipt)
    controller = Controller(controller.settings, Store(str(controller.store.path)), lambda: clock[0])
    source = sample_source(controller.target("hello-development-web"), "execute", "app-run")

    # Verify app identity separately from the already completed producer
    class Identity:

        # Accept only the fixture's app workflow assertion
        def verify(self, token):
            assert token == "application-oidc-fixture"
            return source

    api = create_app(controller.settings, "x" * 43, Identity())
    api.state.controller.clock = lambda: clock[0]
    http = TestClient(api)

    # Preserve the real HTTP and authorization contract without cloud authentication
    class Transport:

        # Distinguish operator decisions from workflow and execution identity
        def call(self, method, path, body=None, operator=False, execution_token=None):
            token = execution_token or ("x" * 43 if operator else "application-oidc-fixture")
            response = http.request(method, path, json=body, headers={"Authorization": "Bearer " + token})
            if response.status_code >= 400:
                raise Refused(response.json()["detail"], response.status_code)
            return response.json()

    engine.gate.client = Transport()
    engine.gate.credentials = lambda _: {}
    calls, current = [], ["web-old"]

    # Model a ready revision and traffic convergence without invoking gcloud
    def command(self, argv, cwd, timeout=600, capture=False, input_text=None):
        self.heartbeat()
        calls.append(argv)
        assert argv[0] == "gcloud"
        assert argv[argv.index("--project") + 1] == OUTPUTS["project"]
        assert argv[argv.index("--region") + 1] == OUTPUTS["region"]
        if "deploy" in argv:
            current[0] = "web-new-" + str(len(calls))
        return json.dumps({"status": {"latestReadyRevisionName": current[0], "latestCreatedRevisionName": current[0],
            "conditions": [{"type": "Ready", "status": "True"}],
            "traffic": [{"revisionName": current[0], "percent": 100}], "url": "https://fixture.example.invalid"}})

    monkeypatch.setattr(Process, "run", command)
    for run in ("app-run", "app-only-run"):
        source.run_id = run
        release = engine.advance(engine.create("development", "web", False, image=IMAGE,
                                 receipts={"foundation": receipt["id"]})["id"])
        assert release["status"] == "waiting", release.get("reason")
        engine.approve(release["id"], "web", release["nodes"]["web"]["request_digest"])
        result = engine.advance(release["id"])
        assert result["status"] == "succeeded", result.get("reason")
        assert result["nodes"]["web"]["outputs"]["image"] == IMAGE
    assert len([r for r in controller.list_requests() if r["manifest"]["target"] == "infrastructure"]) == 1
    assert len([argv for argv in calls if "deploy" in argv]) == 2


# A later write invalidates the old receipt even when its apply fails and is reconciled
@pytest.mark.parametrize("change", ["revoked", "expired", "scope", "failed-apply", "reconciled", "tampered"])
def test_gcp_receipt_cannot_survive_changed_authority(setup, change):
    controller, _, clock, _ = setup
    receipt = produce(setup)[0]["output_receipt"]
    target = controller.target("hello-development-web")
    source = sample_source(target, "execute")
    if change == "revoked":
        controller.revoke_receipt(receipt["id"], "operator")
    elif change == "expired":
        clock[0] += 3601
    elif change == "scope":
        target.receipt_region = "europe-west1"
    elif change in {"failed-apply", "reconciled"}:
        later, lease = produce(setup, run="1002", complete=False)
        if change == "failed-apply":
            controller.complete(later["id"], lease["token"], Completion(outcome="failed", exit_code=1))
        else:
            controller.resolve(later["id"], Resolution(outcome="succeeded", note="Inspected synthetic interrupted apply"), "operator")
    else:
        with controller.store.transaction() as connection:
            original = controller.store.request(connection, receipt["id"])
            original["output_receipt"]["outputs"]["name"] = "another-service"
            controller.store.save_request(connection, original)
    with pytest.raises(Refused):
        controller.output_receipt(receipt["id"], "hello-development-web", source)


# Secret fields, malformed typed outputs and different projects cannot issue a receipt
@pytest.mark.parametrize("change", ["sensitive", "missing", "wrong-type", "project", "region", "registry", "wrong-format"])
def test_gcp_public_output_contract_rejects_unsafe_evidence(setup, change):
    controller, _, _, engine = setup
    raw = state()
    if change == "sensitive":
        raw["outputs"]["name"]["sensitive"] = True
    elif change == "missing":
        del raw["outputs"]["name"]
    elif change == "wrong-type":
        raw["outputs"]["name"]["type"] = "map"
    elif change in {"project", "region"}:
        raw["outputs"][change]["value"] = "other-project" if change == "project" else "europe-west1"
    elif change == "registry":
        raw["outputs"]["image_repository"]["value"] = "us-central1-docker.pkg.dev/other-project/apps/web"
    with pytest.raises(Refused):
        evidence = extract_state(json.dumps(raw).encode(), "aws-ecs-v1" if change == "wrong-format" else FORMAT)
        request, lease = claim(controller, proposal(controller, engine))
        controller.complete(request["id"], lease["token"], Completion(outcome="succeeded", exit_code=0, infrastructure=evidence))
    assert "private-state-fixture-marker" not in json.dumps(controller.store.audit())


# Match the real native GCS metadata shape while refusing endpoint and credential overrides
@pytest.mark.parametrize("change", [None, "bucket", "prefix", "workspace", "version", "credentials", "access_token",
    "impersonate_service_account", "impersonate_service_account_delegates", "storage_custom_endpoint",
    "encryption_key", "kms_encryption_key", "unknown"])
def test_gcs_backend_validation_is_closed_to_hidden_overrides(tmp_path, change):
    directory = tmp_path / ".terraform"
    directory.mkdir()
    config = {**BACKEND, "credentials": None, "access_token": None, "encryption_key": None,
              "impersonate_service_account": None, "impersonate_service_account_delegates": None,
              "kms_encryption_key": None, "storage_custom_endpoint": None}
    metadata = {"version": 3, "backend": {"type": "gcs", "config": config}}
    if change == "workspace":
        (directory / "environment").write_text("other")
    elif change == "version":
        metadata["version"] = 99
    elif change:
        config[change] = "unregistered"
    (directory / "terraform.tfstate").write_text(json.dumps(metadata))
    if change:
        with pytest.raises(Refused, match="registered GCS backend"):
            verify_backend(tmp_path, BACKEND)
    else:
        verify_backend(tmp_path, BACKEND)


# Remove source-selected backend endpoints before the runner obtains credentials
def test_controlled_backend_is_public_exclusive_and_removed_on_failure(tmp_path):
    source = tmp_path / "main.tf.json"
    source.write_text(json.dumps({"terraform": {"backend": {"gcs": {"storage_custom_endpoint": "https://fixture.invalid"}}}}))
    original = source.read_bytes()
    override = tmp_path / "deploy_ops_backend_override.tf.json"
    with pytest.raises(RuntimeError):
        with controlled_backend(tmp_path, {"format": FORMAT, "backend": BACKEND}) as initialization:
            assert initialization == ["-reconfigure"]
            assert json.loads(override.read_text()) == {"terraform": {"backend": {"gcs": BACKEND}}}
            assert override.stat().st_mode & 0o777 == 0o600
            raise RuntimeError("Synthetic interrupted initialization")
    assert not override.exists()
    assert source.read_bytes() == original


# Refuse existing override files without overwriting user content or obtaining credentials
@pytest.mark.parametrize("name", ["override.tf", "override.tf.json", "z_override.tf", "z_override.tf.json"])
def test_controlled_backend_rejects_competing_overrides(tmp_path, name):
    existing = tmp_path / name
    existing.write_text("# Existing operator source\n")
    with pytest.raises(Refused, match="override files"):
        with controlled_backend(tmp_path, {"format": FORMAT, "backend": BACKEND}):
            pytest.fail("An existing override must be refused before credential acquisition")
    assert existing.read_text() == "# Existing operator source\n"


# Do not let initialization touch another workspace before checking its cached selection
def test_controlled_backend_refuses_nondefault_workspace_before_credentials(tmp_path):
    metadata = tmp_path / ".terraform"
    metadata.mkdir()
    (metadata / "environment").write_text("unregistered-workspace")
    with pytest.raises(Refused, match="default workspace before initialization"):
        with controlled_backend(tmp_path, {"format": FORMAT, "backend": BACKEND}):
            pytest.fail("Workspace must be checked before initialization")
    assert not (tmp_path / "deploy_ops_backend_override.tf.json").exists()


# Keep existing AWS receipt policy identities byte-compatible with their prior model
def test_existing_aws_receipt_digest_is_unchanged():
    data = sample_target().model_dump()
    data.update(engines=["opentofu"], receipt_outputs={"backend": {
        "bucket": "fixture-state", "key": "development.tfstate", "region": "us-east-1"}})
    target = Target.model_validate(data)
    assert target.digest == fingerprint(target.model_dump(exclude={"receipt_region", "local_cloud", "local_isolated_iac"}))


# Native version changes need a newly qualified contract before approval
@pytest.mark.parametrize("version", ["1.10.1", "1.12.1", "1.13.0"])
def test_unqualified_gcp_tool_version_is_refused(setup, version):
    controller, _, _, engine = setup
    value = proposal(controller, engine)
    value.tool_version = version
    with pytest.raises(Refused, match="version"):
        controller.submit(value, sample_source(controller.target("infrastructure")))


# GCP scope and backend types are mandatory operator-owned settings
@pytest.mark.parametrize("change", ["region", "format", "backend", "prefix", "engine"])
def test_invalid_gcp_registration_is_refused(setup, change):
    controller, _, _, _ = setup
    data = controller.target("infrastructure").model_dump()
    if change == "region":
        data["receipt_region"] = None
    elif change == "format":
        data["receipt_outputs"]["format"] = "aws-ecs-v1"
    elif change == "backend":
        data["receipt_outputs"]["backend"] = {"bucket": "fixture-state", "key": "state", "region": "us-east-1"}
    elif change == "prefix":
        data["receipt_outputs"]["backend"]["prefix"] = "development/../another"
    else:
        data["engines"] = ["manifest"]
    with pytest.raises(ValidationError):
        Target.model_validate(data)


# Exercise the real supervised runner and completion boundary with synthetic native output
def test_native_runner_extracts_gcp_receipt_after_supervised_apply(setup, tmp_path, monkeypatch):
    from deploy_ops import runner
    from deploy_ops.client import file_digest
    import deploy_ops.delivery.process as process_module

    controller, _, clock, engine = setup
    directory = tmp_path / "infra-source"
    directory.mkdir()
    lock = directory / ".terraform.lock.hcl"
    lock.write_text("# Synthetic provider lock\n")
    for argv in (["git", "init", "-b", "main"], ["git", "add", ".terraform.lock.hcl"],
                 ["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-m", "fixture"]):
        subprocess.run(argv, cwd=directory, check=True, capture_output=True)
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=directory, text=True).strip()
    artifact = tmp_path / "plan.bin"
    artifact.write_bytes(b"synthetic immutable native plan")
    target = controller.target("infrastructure")
    target.workdir = "."
    metadata = {"version": 3, "backend": {"type": "gcs", "config": BACKEND}}
    binary = tmp_path / "native-fixture"
    binary.write_text(f'''#!/usr/bin/env python3

# Model the native process boundary without provider credentials.


import json
import pathlib
import sys

# Emit only the synthetic native command result requested by the real runner
command = sys.argv[1]
if command == "version":
    print(json.dumps({{"terraform_version": {VERSIONS[engine]!r}}}))
if command == "init":
    pathlib.Path(".terraform").mkdir(exist_ok=True)
    pathlib.Path(".terraform/terraform.tfstate").write_text({json.dumps(metadata)!r})
if command == "state":
    print({json.dumps(state())!r})
''')
    binary.chmod(0o700)
    monkeypatch.setattr(runner, "executable", lambda _: str(binary))

    # The registered backend must already replace source selectors before credentials are issued
    def credentials(*args):
        override = directory / "deploy_ops_backend_override.tf.json"
        assert json.loads(override.read_text()) == {"terraform": {"backend": {"gcs": BACKEND}}}
        return {}

    monkeypatch.setattr(runner, "environment", credentials)
    value = proposal(controller, engine)
    value.artifact_digest, value.lock_digest = file_digest(artifact), file_digest(lock)
    source = sample_source(target)
    source.sha = sha
    request = controller.submit(value, source)
    controller.decide(request["id"], Decision(digest=request["digest"], decision="approve"), "operator")
    source.environment = target.environment
    lease = controller.claim(request["id"], Claim(digest=request["digest"], artifact_digest=value.artifact_digest), source)
    monkeypatch.setattr(runner.time, "time", lambda: clock[0])
    monkeypatch.setattr(process_module.time, "time", lambda: clock[0])

    # Keep each real subprocess supervised by the actual controller lease
    class Client:

        # Submit only authenticated heartbeat and completion messages
        def call(self, method, path, body=None, execution_token=None):
            if path.endswith("heartbeat"):
                return controller.heartbeat(request["id"], execution_token)
            return controller.complete(request["id"], execution_token, Completion.model_validate(body))

    assert runner.execute(Client(), lease, artifact, directory) == 0
    completed = controller.get(request["id"])
    assert completed["status"] == "succeeded"
    assert completed["output_receipt"]["outputs"] == OUTPUTS
    assert not (directory / "deploy_ops_backend_override.tf.json").exists()
    assert "private-state-fixture-marker" not in json.dumps(completed)
    assert "private-state-fixture-marker" not in json.dumps(controller.store.audit())
