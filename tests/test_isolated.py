# tests/test_isolated.py - Exercise local isolated planning, apply and receipt authority without cloud credentials.


import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest
from pydantic import ValidationError

from deploy_ops.config import Target
from deploy_ops.delivery.engine import Engine
from deploy_ops.delivery.process import Process
from deploy_ops.delivery.scaffold import create
from deploy_ops.errors import Refused
from deploy_ops.isolated import IsolatedRoute, LimaRunner, load_grant
from deploy_ops.isolated_recipe import IsolatedGrant, Recipe
from deploy_ops.models import Proposal, Resolution

# Keep all credential and cloud values synthetic
TOKEN = "synthetic-temporary-token-never-a-real-key"
OUTPUTS = {"project": "fixture-project", "region": "us-central1", "name": "web",
           "image_repository": "us-central1-docker.pkg.dev/fixture-project/apps/web"}


# Record stage identities while preserving real controller heartbeat checks
class Broker:
    # Retain only account names rather than token material
    def __init__(self):
        self.accounts = []

    # Refuse an expired lease before a synthetic identity is issued
    def token(self, grant, account, heartbeat):
        heartbeat()
        self.accounts.append(account)
        return TOKEN


# Emulate only the native result boundary while exercising real authorization
class Runner:
    # Keep operation history separate from provider fixtures
    def __init__(self):
        self.stages = []
        self.fail = False

    # Return a native-shaped saved plan or typed public state for the fixed recipe
    def run(self, packet, mint, heartbeat):
        heartbeat()
        assert mint() == TOKEN
        self.stages.append(packet["stage"])
        if self.fail:
            raise Refused("Synthetic disconnected guest")
        if packet["stage"] == "plan":
            content = b"synthetic saved native plan"
            return {"plan": base64.b64encode(content).decode(), "artifact_digest": hashlib.sha256(content).hexdigest(),
                    "preview": {"format_version": "1.2", "planned_values": {}, "resource_changes": [
                        {"address": "google_cloud_run_v2_service.web", "type": "google_cloud_run_v2_service",
                         "change": {"actions": ["create"]}}]}}
        state = {"lineage": "11111111-1111-1111-1111-111111111111", "serial": 1,
                 "outputs": {key: {"type": "string", "sensitive": False, "value": value} for key, value in OUTPUTS.items()}}
        return {"state": base64.b64encode(json.dumps(state).encode()).decode()}


# Build a private synthetic operator registration and an ordinary application workspace
@pytest.fixture(params=["terraform", "opentofu"])
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
    engine = Engine(path)
    lock = tmp_path / "provider.lock"
    lock.write_text("fixture provider lock")
    lock.chmod(0o600)
    account = lambda name: name + "@fixture-project.iam.gserviceaccount.com"
    data = dict(name="fixture", environment="development", expires_at=int(time.time()) + 1800,
        authorization="Synthetic bounded test authority", engine=request.param,
        recipe={**OUTPUTS, "runtime_service_account": account("runtime"), "image_digest": "sha256:" + "a" * 64,
                "backend": {"bucket": "fixture-state", "prefix": "development/cloud-run"}},
        planner_service_account=account("planner"), executor_service_account=account("executor"),
        credentials_file=str(tmp_path / "never-read-key.json"), broker_service_account=account("broker"), gcloud="/opt/native/gcloud", limactl="/opt/native/limactl",
        vm="fixture-vm", vm_config_digest="1" * 64, tool_digest="2" * 64, provider_digest="3" * 64,
        lock_file=str(lock), lock_digest=hashlib.sha256(lock.read_bytes()).hexdigest(),
        workspace_digest=engine.spec.digest, app_component="web", app_dependency="foundation",
        app_planner_service_account=account("app-reader"), app_executor_service_account=account("app-writer"))
    grant = tmp_path / "grant.json"
    grant.write_text(json.dumps(data))
    grant.chmod(0o600)
    route = IsolatedRoute(grant, engine.state, Broker(), Runner(), engine.spec)
    return route, path, data


# Complete separate exact planning and apply approvals through the shared controller
def produce(route):
    planning = route.prepare()
    assert planning["manifest"]["source"]["kind"] == "local"
    assert planning["manifest"]["artifact_digest"] == route.grant.packet_digest
    with pytest.raises(Refused):
        route.execute(planning["id"])
    assert not route.broker.accounts
    route.approve(planning["id"], planning["digest"])
    apply = route.execute(planning["id"])
    assert apply["manifest"]["planning_request"] == planning["id"]
    with pytest.raises(Refused):
        route.execute(apply["id"])
    route.approve(apply["id"], apply["digest"])
    return route.execute(apply["id"])


# Preserve public receipt provenance across two independent native application releases
def test_planning_apply_and_app_only_use_real_local_authority(setup, monkeypatch):
    route, path, _ = setup
    receipt = produce(route)["output_receipt"]
    monkeypatch.setenv("DEPLOY_OPS_ISOLATED_GRANT_FILE", str(route.path))
    engine = Engine(path)
    engine.gate.route.broker = route.broker
    calls = []
    current = ["web-old"]

    # Return ready native Cloud Run observations while invoking real lease callbacks
    def command(self, argv, cwd, timeout=600, capture=False, input_text=None):
        self.heartbeat()
        assert self.env["CLOUDSDK_AUTH_ACCESS_TOKEN"] == TOKEN
        assert "GOOGLE_APPLICATION_CREDENTIALS" not in self.env
        calls.append(argv)
        if "deploy" in argv:
            current[0] = "web-" + str(len(calls))
        revision = current[0]
        return json.dumps({"status": {"latestReadyRevisionName": revision, "latestCreatedRevisionName": revision,
            "conditions": [{"type": "Ready", "status": "True"}], "traffic": [{"revisionName": revision, "percent": 100}],
            "url": "https://fixture.example.invalid"}})

    monkeypatch.setattr(Process, "run", command)
    for digest in ("a", "b"):
        release = engine.create("development", "web", False, image=OUTPUTS["image_repository"] + "@sha256:" + digest * 64,
                                receipts={"foundation": receipt["id"]})
        waiting = engine.advance(release["id"])
        assert waiting["status"] == "waiting", waiting.get("reason")
        engine.approve(release["id"], "web", waiting["nodes"]["web"]["request_digest"])
        result = engine.advance(release["id"])
        assert result["status"] == "succeeded", result.get("reason")
    assert route.runner.stages == ["plan", "apply"]
    assert len([command for command in calls if "deploy" in command]) == 2
    assert TOKEN not in json.dumps(route.controller.store.audit())
    assert all(request["manifest"]["source"]["kind"] == "local" for request in route.controller.list_requests())


# Keep revoked grants and retained plan tampering ahead of writer-token minting
@pytest.mark.parametrize("change", ["expired", "edited", "lock", "plan"])
def test_changed_authority_never_mints_writer_token(setup, change):
    route, _, data = setup
    planning = route.prepare()
    route.approve(planning["id"], planning["digest"])
    apply = route.execute(planning["id"])
    route.approve(apply["id"], apply["digest"])
    if change == "expired":
        data["expires_at"] = int(time.time()) - 1
        route.path.write_text(json.dumps(data))
    elif change == "edited":
        data["authorization"] = "Changed operator registration"
        route.path.write_text(json.dumps(data))
    elif change == "lock":
        Path(data["lock_file"]).write_text("changed lock")
    else:
        (route.state / (apply["id"] + ".plan")).write_bytes(b"changed plan")
    with pytest.raises(Refused):
        route.execute(apply["id"])
    assert route.runner.stages == ["plan"]
    assert route.broker.accounts == [route.grant.planner_service_account]


# Reject source extensions before the fixed compiler can produce native input
@pytest.mark.parametrize("field", ["provisioner", "module", "provider", "credentials", "endpoint", "command"])
def test_recipe_rejects_executable_or_credential_extensions(setup, field):
    _, _, data = setup
    data["recipe"][field] = "untrusted fixture"
    with pytest.raises(ValidationError):
        IsolatedGrant.model_validate(data)


# A new claimed apply invalidates the previous receipt even when its guest disconnects
def test_interrupted_apply_never_revives_previous_receipt(setup):
    route, _, _ = setup
    receipt = produce(route)["output_receipt"]
    planning = route.prepare()
    route.approve(planning["id"], planning["digest"])
    apply = route.execute(planning["id"])
    route.approve(apply["id"], apply["digest"])
    route.runner.fail = True
    with pytest.raises(Refused):
        route.execute(apply["id"])
    assert route.controller.get(apply["id"])["status"] == "uncertain"
    route.controller.resolve(apply["id"], Resolution(outcome="failed", note="Inspected synthetic interruption"), "operator")
    with pytest.raises(Refused):
        route.controller.output_receipt(receipt["id"], "hello-development-web",
                                        route.source("hello-development-web", "new-app"))


# The local mode cannot be reached by federated identities or an unleased saved plan
@pytest.mark.parametrize("change", ["github", "no-planning-lease", "production", "delegation", "same-identity"])
def test_local_isolated_authority_is_explicit(setup, change):
    route, _, _ = setup
    if change in {"production", "delegation", "same-identity"}:
        value = route.controller.target(route.apply_target).model_dump()
        if change == "production":
            value["production"] = True
        elif change == "delegation":
            value["policy"]["allow_delegation"] = True
        else:
            value["identity"] = value["planner_identity"]
        with pytest.raises(ValidationError):
            Target.model_validate(value)
        return
    if change == "github":
        source = route.source(route.plan_target, "run", True).model_copy(update={"kind": "github"})
        with pytest.raises(Refused):
            route.controller.check_source(route.controller.target(route.plan_target), source, "plan")
    else:
        with pytest.raises(Refused, match="planning lease"):
            route.controller.submit(Proposal(target=route.apply_target, engine=route.grant.engine,
                tool_version={"terraform": "1.12.2", "opentofu": "1.10.0"}[route.grant.engine],
                artifact_digest="a" * 64, lock_digest=route.grant.lock_digest,
                preview={"format_version": "1.2", "planned_values": {},
                         "deploy_ops_backend": route.grant.recipe.backend.model_dump()}),
                route.source(route.apply_target, "run", True))


# Changing the registered VM isolation stops before the native broker can be invoked
@pytest.mark.parametrize("change", ["mounts", "forwardAgent", "proxy", "config", "stopped"])
def test_lima_isolation_is_rechecked_before_transport(setup, monkeypatch, change):
    from deploy_ops.config import fingerprint
    route, _, _ = setup
    config = {"mounts": [], "ssh": {"forwardAgent": False, "forwardX11": False},
              "propagateProxyEnv": False, "portForwards": [{"ignore": True}]}
    grant = route.grant.model_copy(update={"vm_config_digest": fingerprint(config)})
    info = {"name": grant.vm, "status": "Running", "arch": "x86_64", "config": config}
    if change == "mounts":
        config["mounts"] = [{"location": "/host"}]
    elif change == "forwardAgent":
        config["ssh"]["forwardAgent"] = True
    elif change == "proxy":
        config["propagateProxyEnv"] = True
    elif change == "config":
        config["cpus"] = 99
    else:
        info["status"] = "Stopped"
    monkeypatch.setattr("deploy_ops.isolated.native_tool", lambda path: path)
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, json.dumps(info).encode()))
    with pytest.raises(Refused, match="isolation"):
        LimaRunner(grant).verify()
    assert not route.broker.accounts


# Never let native gcloud consume a key for a different broker or endpoint
@pytest.mark.parametrize("change", ["project_id", "client_email", "token_uri", "type", "account"])
def test_broker_checks_metadata_before_starting_native_tool(setup, monkeypatch, change):
    from deploy_ops.isolated import NativeBroker
    route, _, _ = setup
    grant = route.grant
    metadata = {"type": "service_account", "project_id": grant.recipe.project,
                "client_email": grant.broker_service_account, "token_uri": "https://oauth2.googleapis.com/token"}
    if change != "account":
        metadata[change] = "wrong-fixture-value"
    key = Path(grant.credentials_file)
    key.write_text(json.dumps(metadata))
    key.chmod(0o600)
    calls = []
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: calls.append(args))
    with pytest.raises(Refused, match="metadata"):
        NativeBroker().token(grant, "unregistered" if change == "account" else grant.planner_service_account, lambda: None)
    assert not calls


# Transport loss terminates real child processes without placing tokens in arguments or files
def test_guest_process_group_is_stopped_on_authority_loss(tmp_path, monkeypatch):
    from deploy_ops import isolated_guest
    original = subprocess.Popen

    # Exercise actual process supervision under the current test user rather than requiring host root
    def spawn(*args, **kwargs):
        for key in ("user", "group", "extra_groups"):
            kwargs.pop(key, None)
        return original(*args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", spawn)
    marker = tmp_path / "child.pid"
    code = "import os,time,pathlib; p=os.fork(); pathlib.Path('child.pid').write_text(str(p)) if p else None; time.sleep(60)"
    started = time.monotonic()

    # Simulate loss of the authority channel while the native child remains blocked
    def authority():
        if marker.exists() and time.monotonic() - started > 0.2:
            raise RuntimeError("Host disconnected")

    with pytest.raises(RuntimeError, match="disconnected"):
        isolated_guest.run([sys.executable, "-c", code], tmp_path, {"PATH": "/usr/bin:/bin"},
                           os.getuid(), os.getgid(), authority)
    child = int(marker.read_text())
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        observed = subprocess.run(["ps", "-o", "stat=", "-p", str(child)], capture_output=True, text=True)
        if not observed.stdout.strip() or observed.stdout.lstrip().startswith("Z"):
            break
        time.sleep(0.05)
    else:
        pytest.fail("Native descendant survived authority loss")


# Local provenance does not borrow synthetic GitHub identifiers to satisfy validation
@pytest.mark.parametrize("field", ["workflow_ref", "repository_id", "repository_owner_id"])
def test_local_registration_refuses_federated_placeholders(setup, field):
    route, _, _ = setup
    source = route.source(route.plan_target, "run", True)
    assert source.job_workflow_ref is None and source.job_workflow_sha is None
    assert source.repository_id is None and source.repository_owner_id is None
    target = route.controller.target(route.plan_target).model_dump()
    target[field] = "1" if field != "workflow_ref" else "example/repo/.github/workflows/local.yml@" + "0" * 40
    with pytest.raises(ValidationError):
        Target.model_validate(target)


# Reviewable planning packets must match the registered generated source exactly
@pytest.mark.parametrize("change", ["module", "lock_digest", "missing"])
def test_planning_packet_cannot_change_behind_its_displayed_digest(setup, change):
    from deploy_ops.models import IsolatedPacket
    route, _, _ = setup
    public = {"module": route.grant.recipe.module(route.grant.engine), "lock_digest": route.grant.lock_digest,
              "tool_digest": route.grant.tool_digest, "provider_digest": route.grant.provider_digest}
    if change == "module":
        public["module"]["provider"]["google"]["project"] = "other-project"
    elif change == "lock_digest":
        public["lock_digest"] = "f" * 64
    proposal = Proposal(target=route.plan_target, engine="manifest", tool_version="isolated-v1",
                        artifact_digest=route.grant.packet_digest, preview={"changes": []},
                        isolated_packet=None if change == "missing" else IsolatedPacket.model_validate(public))
    with pytest.raises(Refused, match="public source packet"):
        route.controller.submit(proposal, route.source(route.plan_target, "run", True))
    assert not route.broker.accounts


# Authenticated console controls use the same controller while exposing no broker references
@pytest.mark.parametrize("failure", [False, True])
def test_console_isolated_planning_apply_and_private_status(setup, monkeypatch, failure):
    from fastapi.testclient import TestClient
    from deploy_ops.console import create_app
    route, path, data = setup
    monkeypatch.setenv("DEPLOY_OPS_ISOLATED_GRANT_FILE", str(route.path))
    engine = Engine(path)
    shared = engine.gate.route
    shared.broker, shared.runner = route.broker, route.runner
    client = TestClient(create_app(engine, "x" * 40), base_url="http://127.0.0.1:8765")
    assert client.get("/api/isolated").status_code == 401
    assert client.post("/api/isolated/prepare", json={}).status_code == 401
    client.headers["Authorization"] = "Bearer " + "x" * 40
    response = client.get("/api/isolated")
    assert response.status_code == 200
    status = response.json()
    assert status["available"] and status["recipe"] == data["recipe"]
    assert status["application"]["dependency"] == "foundation"
    for forbidden in ("credentials_file", "broker_service_account", "gcloud", "limactl", "lock_file", "authorization", TOKEN):
        assert forbidden not in response.text
    request = client.post("/api/isolated/prepare", json={}).json()
    base = "/api/isolated/requests/" + request["id"]
    assert client.post(base + "/execute", json={"digest": request["digest"]}).status_code == 409
    assert not shared.broker.accounts
    assert client.post(base + "/approve", json={"digest": "a" * 64}).status_code == 409
    assert client.post(base + "/approve", json={"digest": request["digest"]}).status_code == 200
    if failure:
        shared.runner.fail = True
    started = client.post(base + "/execute", json={"digest": request["digest"]})
    assert started.status_code == 200 and started.json()["started"]
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        status = client.get("/api/isolated").json()
        if not status["active"]:
            break
        time.sleep(.02)
    else:
        pytest.fail("Isolated console worker did not finish")
    assert TOKEN not in json.dumps(status)
    planning = next(item for item in status["requests"] if item["id"] == request["id"])
    if failure:
        assert planning["status"] == "uncertain"
        assert planning["worker_error"] == "Synthetic disconnected guest"
        return
    assert planning["status"] == "succeeded"
    apply = next(item for item in status["requests"] if item["manifest"]["engine"] in {"terraform", "opentofu"})
    base = "/api/isolated/requests/" + apply["id"]
    assert client.post(base + "/approve", json={"digest": apply["digest"]}).status_code == 200
    assert client.post(base + "/execute", json={"digest": apply["digest"]}).status_code == 200
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        status = client.get("/api/isolated").json()
        if not status["active"]:
            break
        time.sleep(.02)
    completed = next(item for item in status["requests"] if item["id"] == apply["id"])
    assert completed["output_receipt"]["outputs"] == OUTPUTS
    assert completed["receipt_status"]["valid"] is True
    shared.controller.revoke_receipt(completed["id"], "operator")
    refreshed = client.get("/api/isolated").json()
    revoked = next(item for item in refreshed["requests"] if item["id"] == completed["id"])
    assert revoked["receipt_status"]["valid"] is False
    assert shared.runner.stages == ["plan", "apply"]


# The main CLI and standalone module share the same route and exact approval methods
def test_main_cli_isolated_dispatches_without_a_second_store(setup, monkeypatch):
    from deploy_ops.cli import parser, dispatch
    route, _, _ = setup
    arguments = ["--state", str(route.state), "isolated", "--grant", str(route.path)]
    prepared = dispatch(parser().parse_args(arguments + ["prepare"]))
    assert route.controller.get(prepared["id"])["digest"] == prepared["digest"]
    result = dispatch(parser().parse_args(arguments + ["approve", prepared["id"], "--digest", prepared["digest"]]))
    assert result["status"] == "approved"
    status = dispatch(parser().parse_args(arguments + ["status"]))
    assert status["requests"][0]["id"] == prepared["id"]
    assert "credentials_file" not in json.dumps(status)


# Grant expiry leaves history and inspected-failure recovery accessible without renewing authority
def test_expired_grant_can_read_and_reconcile_but_never_execute(setup, monkeypatch):
    from fastapi.testclient import TestClient
    from deploy_ops.console import create_app
    from deploy_ops.cli import parser, dispatch
    route, path, data = setup
    planning = route.prepare()
    route.approve(planning["id"], planning["digest"])
    route.runner.fail = True
    with pytest.raises(Refused):
        route.execute(planning["id"])
    assert route.controller.get(planning["id"])["status"] == "uncertain"
    monkeypatch.setattr("deploy_ops.isolated.time.time", lambda: data["expires_at"] + 1)
    expired = IsolatedRoute(route.path, route.state, route.broker, route.runner)
    assert expired.grant.digest == route.grant.digest
    assert not expired.status()["available"]
    for operation in (expired.prepare, lambda: expired.approve(planning["id"], planning["digest"]),
                      lambda: expired.execute(planning["id"])):
        with pytest.raises(Refused, match="expired"):
            operation()
    assert len(route.broker.accounts) == 1
    arguments = ["--state", str(route.state), "isolated", "--grant", str(route.path)]
    assert dispatch(parser().parse_args(arguments + ["status", planning["id"]]))["status"] == "uncertain"
    monkeypatch.setenv("DEPLOY_OPS_ISOLATED_GRANT_FILE", str(route.path))
    engine = Engine(path)
    client = TestClient(create_app(engine, "x" * 40), base_url="http://127.0.0.1:8765",
                        headers={"Authorization": "Bearer " + "x" * 40})
    assert client.get("/api/isolated").json()["available"] is False
    assert client.post("/api/isolated/prepare", json={}).status_code == 403
    action = "/api/isolated/requests/" + planning["id"] + "/reconcile"
    assert client.post(action, json={"digest": "a" * 64, "note": "Inspected synthetic interruption"}).status_code == 409
    assert client.post(action, json={"digest": planning["digest"], "note": "short"}).status_code == 422
    result = client.post(action, json={"digest": planning["digest"], "note": "Inspected synthetic interruption"})
    assert result.status_code == 200 and result.json()["status"] == "failed"
    assert not result.json().get("output_receipt")
    assert len(route.broker.accounts) == 1
