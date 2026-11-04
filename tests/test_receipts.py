# tests/test_receipts.py - Connect durable infrastructure evidence to a separate application worker.


import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from deploy_ops.api import create_app
from deploy_ops.config import Settings, Target
from deploy_ops.delivery.engine import Engine
from deploy_ops.delivery.gate import RemoteGate
from deploy_ops.delivery.process import Process
from deploy_ops.delivery.scaffold import create
from deploy_ops.demo import sample_proposal, sample_source, sample_target
from deploy_ops.errors import Refused
from deploy_ops.models import Claim, Completion, Decision, Proposal
from deploy_ops.receipts import extract_state
from deploy_ops.service import Controller
from deploy_ops.store import Store

# Keep the fixture output contract synthetic and internally consistent
OUTPUTS = {
    "cluster": "arn:aws:ecs:us-east-1:111111111111:cluster/apps",
    "service": "arn:aws:ecs:us-east-1:111111111111:service/apps/web",
    "container": "web", "image_repository": "111111111111.dkr.ecr.us-east-1.amazonaws.com/web",
}
IMAGE = OUTPUTS["image_repository"] + "@sha256:" + "a" * 64


# Include a private state field to verify it never reaches the receipt
def state(serial=1):
    return {"lineage": "11111111-1111-1111-1111-111111111111", "serial": serial,
            "outputs": {name: {"sensitive": False, "type": "string", "value": value} for name, value in OUTPUTS.items()},
            "resources": [{"private": "must-never-leave-state"}]}


# Bind the synthetic operator decision to exact submitted evidence
def approve(controller, request):
    controller.decide(request["id"], Decision(digest=request["digest"], decision="approve"), "operator")


# Exercise the producer lifecycle before issuing any output receipt
def producer_execution(controller, run="1001", serial=1, complete=True):
    target = controller.target("infrastructure")
    proposal = sample_proposal().model_copy(update={"target": "infrastructure"})
    proposal.preview["deploy_ops_backend"] = target.receipt_outputs.backend.model_dump()
    request = controller.submit(proposal, sample_source(target, run=run))
    approve(controller, request)
    lease = controller.claim(request["id"], Claim(digest=request["digest"], artifact_digest=proposal.artifact_digest),
                             sample_source(target, "execute", run))
    evidence = extract_state(json.dumps(state(serial)).encode())
    completion = Completion(outcome="succeeded", exit_code=0, infrastructure=evidence)
    if complete:
        request = controller.complete(request["id"], lease["token"], completion)
    return request, lease, completion


# Register separate infrastructure and application identities against one controller
@pytest.fixture
def setup(tmp_path):
    path = create(tmp_path / "workspace")
    path.write_text('''name = "hello"
[repositories.infrastructure]
path = "infrastructure"
allow_uncommitted = true
[repositories.application]
path = "application"
allow_uncommitted = true
[environments.development]
provider = "aws"
[components.foundation]
repository = "infrastructure"
kind = "infrastructure"
adapter = "opentofu"
[components.web]
repository = "application"
kind = "application"
adapter = "ecs"
needs = ["foundation"]
[components.web.options]
region = "${outputs.foundation.region}"
cluster = "${outputs.foundation.cluster}"
service = "${outputs.foundation.service}"
container = "${outputs.foundation.container}"
image_repository = "${outputs.foundation.image_repository}"
''')
    engine = Engine(path, controller="https://controller.example")
    producer = sample_target().model_dump()
    producer.update(repository="example/infrastructure", repository_id="201", environment="development",
                    planner_environment="development-plan", engines=["opentofu"],
                    receipt_outputs={"backend": {"bucket": "fixture-state", "key": "development.tfstate", "region": "us-east-1"}})
    consumer = sample_target().model_dump()
    consumer.update(environment="development", planner_environment="development-plan", engines=["delivery"],
                    delivery_digest=engine.spec.digest, receipt_dependencies={"foundation": "infrastructure"})
    settings = Settings(targets={"infrastructure": Target.model_validate(producer),
                                 "hello-development-web": Target.model_validate(consumer)},
                        database=str(tmp_path / "controller.db"))
    clock = [1800000000]
    controller = Controller(settings, Store(settings.database), lambda: clock[0])
    return controller, engine, clock


# Reference the exact producer receipt from the consumer's delivery proposal
def application_proposal(controller, receipt):
    return Proposal(target="hello-development-web", engine="delivery", artifact_digest="a" * 64,
                    preview={"config_digest": controller.target("hello-development-web").delivery_digest,
                             "preview": {"changes": [], "manual_only": True}}, tool_version="0.5.0",
                    receipts={"foundation": {"id": receipt["id"], "digest": receipt["digest"]}})


# A separate app worker must reuse persisted infrastructure evidence without reapplying it
def test_two_workers_reuse_controller_receipt_without_reapplying_infrastructure(setup, monkeypatch):
    controller, engine, clock = setup
    original, lease, completion = producer_execution(controller)
    receipt = original["output_receipt"]
    assert "must-never-leave-state" not in json.dumps(original)
    assert receipt["producer"]["repository"] == "example/infrastructure"
    assert receipt["producer"]["environment"] == "development"
    assert controller.complete(original["id"], lease["token"], completion)["output_receipt"] == receipt

    # A new service instance reads persisted evidence; the app holds no producer execution token
    controller = Controller(controller.settings, Store(str(controller.store.path)), lambda: clock[0])
    source = sample_source(controller.target("hello-development-web"), "execute", "app-run")


    # Model the authenticated application workflow separately from its transport
    class Identity:

        # Accept only the synthetic application token expected by this fixture
        def verify(self, token):
            assert token == "application-oidc-fixture"
            return source
    api = create_app(controller.settings, "x" * 43, Identity())
    api.state.controller.clock = lambda: clock[0]
    http = TestClient(api)


    # Exercise real API routes through an in-process authenticated client
    class Transport:

        # Keep operator and execution credentials distinct in API contract calls
        def call(self, method, path, body=None, operator=False, execution_token=None):
            token = execution_token or ("x" * 43 if operator else "application-oidc-fixture")
            response = http.request(method, path, json=body, headers={"Authorization": "Bearer " + token})
            if response.status_code >= 400:
                raise Refused(response.json()["detail"], response.status_code)
            return response.json()
    engine.gate.client = Transport()
    engine.gate.credentials = lambda _: {}
    calls = []
    current = ["old"]


    # Simulate provider revision changes while retaining the shared heartbeat boundary
    def command(self, argv, cwd, timeout=600, capture=False, input_text=None):
        self.heartbeat()
        calls.append(argv)
        if "describe-services" in argv:
            return json.dumps({"services": [{"taskDefinition": current[0], "deployments": [{"rolloutState": "COMPLETED"}]}]})
        if "describe-task-definition" in argv:
            return json.dumps({"taskDefinition": {"family": "web", "containerDefinitions": [{"name": "web", "image": "old"}]}})
        if "register-task-definition" in argv:
            return json.dumps({"taskDefinition": {"taskDefinitionArn": "new"}})
        if "update-service" in argv:
            current[0] = "new"
        return "{}"
    monkeypatch.setattr(Process, "run", command)
    for run in ("app-run", "next-app-run"):
        source.run_id = run
        release = engine.advance(engine.create("development", "web", False, image=IMAGE,
                                               receipts={"foundation": receipt["id"]})["id"])
        assert release["status"] == "waiting", release.get("reason")
        node = release["nodes"]["web"]
        engine.approve(release["id"], "web", node["request_digest"])
        result = engine.advance(release["id"])
        assert result["status"] == "succeeded", result.get("reason")
        assert result["nodes"]["web"]["outputs"]["image"] == IMAGE
        assert result["nodes"]["web"]["infrastructure_receipts"]["foundation"]["digest"] == receipt["digest"]
    assert all(call[0] == "aws" for call in calls)
    assert len([r for r in controller.list_requests() if r["manifest"]["target"] == "infrastructure"]) == 1


# Receipt validity must be checked again when approval is consumed
@pytest.mark.parametrize("change", ["revoked", "expired", "newer", "interrupted", "failed-apply", "policy", "wrong-digest"])
def test_receipt_rechecked_between_submission_and_claim(setup, change):
    controller, _, clock = setup
    original, _, _ = producer_execution(controller)
    receipt = original["output_receipt"]
    proposal = application_proposal(controller, receipt)
    target = controller.target(proposal.target)
    source = sample_source(target, "execute", "app-run")
    request = controller.submit(proposal, source)
    approve(controller, request)
    if change == "revoked":
        controller.revoke_receipt(receipt["id"], "operator")
    elif change == "expired":
        clock[0] += 3601
    elif change == "newer":
        producer_execution(controller, run="1002", serial=2)
    elif change == "interrupted":
        producer_execution(controller, run="1002", complete=False)
    elif change == "failed-apply":
        next_request, lease, _ = producer_execution(controller, run="1002", complete=False)
        controller.complete(next_request["id"], lease["token"], Completion(outcome="failed", exit_code=1))
    elif change == "policy":
        controller.target("infrastructure").receipt_outputs.backend.key = "different.tfstate"
    else:
        with controller.store.transaction() as connection:
            changed = controller.store.request(connection, receipt["id"])
            changed["output_receipt"]["outputs"]["container"] = "changed"
            controller.store.save_request(connection, changed)
    with pytest.raises(Refused):
        controller.claim(request["id"], Claim(digest=request["digest"], artifact_digest=proposal.artifact_digest), source)
    assert controller.get(request["id"])["execution"] is None


# Receipt revocation stops authority without claiming remote rollback
def test_revocation_interrupts_running_consumer_without_undoing_operation(setup):
    controller, _, _ = setup
    receipt = producer_execution(controller)[0]["output_receipt"]
    proposal = application_proposal(controller, receipt)
    source = sample_source(controller.target(proposal.target), "execute", "app-run")
    request = controller.submit(proposal, source)
    approve(controller, request)
    lease = controller.claim(request["id"], Claim(digest=request["digest"], artifact_digest=proposal.artifact_digest), source)
    controller.revoke_receipt(receipt["id"], "operator")
    with pytest.raises(Refused, match="revoked"):
        controller.heartbeat(request["id"], lease["token"])
    assert controller.get(request["id"])["status"] == "uncertain"


# Reject secret or malformed outputs without repeating their contents
@pytest.mark.parametrize("change", ["sensitive", "wrong-type", "missing", "secret-value", "malformed"])
def test_state_extraction_never_exports_secret_or_untyped_values(change):
    raw = state()
    if change == "sensitive":
        raw["outputs"]["cluster"]["sensitive"] = True
    elif change == "wrong-type":
        raw["outputs"]["cluster"]["type"] = "map"
    elif change == "missing":
        del raw["outputs"]["cluster"]["sensitive"]
    elif change == "malformed":
        raw["outputs"]["cluster"] = ["private-secret-value"]
    else:
        raw["outputs"]["cluster"]["value"] = "private-secret-value"
    with pytest.raises(Refused) as error:
        extract_state(json.dumps(raw).encode())
    assert "private-secret-value" not in str(error.value)


# State evidence must come from the exact registered backend and workspace
@pytest.mark.parametrize("change", [None, "bucket", "key", "region", "type", "workspace", "version", "endpoint", "profile"])
def test_backend_selection_is_checked_before_receipt_execution(tmp_path, change):
    from deploy_ops.receipts import verify_backend
    expected = {"bucket": "fixture-state", "key": "development.tfstate", "region": "us-east-1"}
    folder = tmp_path / ".terraform"
    folder.mkdir()
    metadata = {"version": 3, "backend": {"type": "s3", "config": dict(expected)}}
    if change in expected:
        metadata["backend"]["config"][change] = "different"
    elif change == "type":
        metadata["backend"]["type"] = "local"
    elif change == "workspace":
        (folder / "environment").write_text("another")
    elif change == "version":
        metadata["version"] = 99
    elif change:
        metadata["backend"]["config"][change] = "unregistered"
    (folder / "terraform.tfstate").write_text(json.dumps(metadata))
    if change:
        with pytest.raises(Refused, match="registered S3 backend"):
            verify_backend(tmp_path, expected)
    else:
        verify_backend(tmp_path, expected)


# Adding optional receipts must not invalidate unrelated saved authority
def test_nonreceipt_target_keeps_legacy_policy_digest():
    from deploy_ops.config import fingerprint
    target = sample_target()
    original = target.model_dump(exclude={"receipt_outputs", "receipt_dependencies", "local_cloud", "receipt_region"})
    assert target.digest == fingerprint(original)


# Legacy requests remain deduplicated when receipt fields are absent
def test_nonreceipt_resubmission_finds_legacy_request(system, proposal, source):
    from deploy_ops.config import fingerprint
    controller, _, _ = system
    request = controller.submit(proposal, source)
    with controller.store.transaction() as connection:
        original = controller.store.request(connection, request["id"])
        original["manifest"].pop("receipts", None)
        original["digest"] = fingerprint(original["manifest"])
        connection.execute("DELETE FROM requests WHERE id=?", (original["id"],))
        controller.store.save_request(connection, original)
    assert controller.submit(proposal, source)["id"] == original["id"]
    assert len(controller.list_requests()) == 1


# A receipt cannot move between accounts, environments or unregistered consumers
@pytest.mark.parametrize("change", ["account", "environment", "source", "missing", "digest"])
def test_wrong_receipt_consumer_or_reference_is_refused(setup, change):
    controller, _, _ = setup
    receipt = producer_execution(controller)[0]["output_receipt"]
    proposal = application_proposal(controller, receipt)
    target = controller.target(proposal.target)
    source = sample_source(target, "execute", "app-run")
    if change == "account":
        target.identity["role_arn"] = "arn:aws:iam::222222222222:role/consumer"
    elif change == "environment":
        target.environment = source.environment = "production"
    elif change == "source":
        source.repository_id = "999"
    elif change == "missing":
        proposal.receipts = {}
    else:
        proposal.receipts["foundation"].digest = "0" * 64
    with pytest.raises(Refused):
        controller.submit(proposal, source)


# Issue outputs only after the supervised saved-plan execution succeeds
@pytest.mark.parametrize("expired_completion", [False, True])
def test_trusted_runner_extracts_receipt_after_successful_apply(setup, tmp_path, monkeypatch, expired_completion):
    import subprocess
    from deploy_ops import runner
    from deploy_ops.client import file_digest
    controller, _, clock = setup
    directory = tmp_path / "infra-source"
    directory.mkdir()
    lock = directory / ".terraform.lock.hcl"
    lock.write_text("# Fixture provider lock\n")
    for argv in (["git", "init", "-b", "main"], ["git", "add", ".terraform.lock.hcl"],
                 ["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-m", "fixture"]):
        subprocess.run(argv, cwd=directory, check=True, capture_output=True)
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=directory, text=True).strip()
    artifact = tmp_path / "plan.bin"
    artifact.write_bytes(b"immutable saved plan fixture")
    target = controller.target("infrastructure")
    target.workdir = "."
    metadata = {"version": 3, "backend": {"type": "s3", "config": target.receipt_outputs.backend.model_dump()}}
    if expired_completion:
        target.receipt_outputs = None
    binary = tmp_path / "tofu-fixture"
    binary.write_text(f'''#!/usr/bin/env python3
import json,pathlib,sys
command=sys.argv[1]
if command=="version": print(json.dumps({{"terraform_version":"1.10.0"}}))
if command=="init":
 pathlib.Path(".terraform").mkdir(exist_ok=True)
 pathlib.Path(".terraform/terraform.tfstate").write_text({json.dumps(metadata)!r})
if command=="state": print({json.dumps(state())!r})
''')
    binary.chmod(0o700)
    monkeypatch.setattr(runner, "executable", lambda _: str(binary))
    monkeypatch.setattr(runner, "environment", lambda *args: {})
    proposal = sample_proposal().model_copy(update={"target": "infrastructure", "artifact_digest": file_digest(artifact),
                                                    "lock_digest": file_digest(lock)})
    if target.receipt_outputs:
        proposal.preview["deploy_ops_backend"] = target.receipt_outputs.backend.model_dump()
    source = sample_source(target)
    source.sha = sha
    request = controller.submit(proposal, source)
    approve(controller, request)
    source.environment = target.environment
    lease = controller.claim(request["id"], Claim(digest=request["digest"], artifact_digest=proposal.artifact_digest), source)
    # Use real subprocess supervision with a synthetic executable, never a cloud CLI
    monkeypatch.setattr(runner.time, "time", lambda: clock[0])
    import deploy_ops.delivery.process as process_module
    monkeypatch.setattr(process_module.time, "time", lambda: clock[0])


    # Keep the synthetic runner connected to the real controller state machine
    class Client:

        # Model a late completion separately from successful local execution
        def call(self, method, path, body=None, execution_token=None):
            if path.endswith("heartbeat"):
                return controller.heartbeat(request["id"], execution_token)
            if expired_completion:
                clock[0] = lease["deadline"] + 1
            return controller.complete(request["id"], execution_token, Completion.model_validate(body))
    if expired_completion:
        with pytest.raises(Refused, match="requires reconciliation"):
            runner.execute(Client(), lease, artifact, directory)
        assert controller.get(request["id"])["status"] == "uncertain"
        return
    assert runner.execute(Client(), lease, artifact, directory) == 0
    completed = controller.get(request["id"])
    assert completed["status"] == "succeeded"
    assert completed["output_receipt"]["outputs"] == OUTPUTS
    assert "must-never-leave-state" not in json.dumps(completed)
    assert "must-never-leave-state" not in json.dumps(controller.store.audit())
