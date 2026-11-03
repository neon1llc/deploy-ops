# tests/test_delivery.py - Check complete local paths and shared deployment boundaries.

import json
import os
import sys
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from deploy_ops.console import create_app
from deploy_ops.delivery.demo import finish, run
from deploy_ops.delivery.engine import Engine
from deploy_ops.delivery.files import digest_tree
from deploy_ops.delivery.notify import flush
from deploy_ops.delivery.process import Process
from deploy_ops.delivery.scaffold import create
from deploy_ops.delivery.spec import Repository, Workspace
from deploy_ops.errors import Refused


@pytest.fixture
def engine(tmp_path):
    return Engine(create(tmp_path / "workspace"))


def test_complete_delivery_and_promotion(tmp_path):
    result = run(tmp_path / "complete")
    assert result["status"] == "succeeded"
    assert result["artifact_preserved"] is True
    assert result["http_check"].startswith("200")


def test_bounded_delegation_and_independent_app_release(engine):
    engine.gate.delegate("development", 1, 1)
    first = engine.advance(engine.create("development")["id"])
    assert first["status"] == "succeeded"
    second = engine.advance(engine.create("development", "web", False)["id"])
    assert second["status"] == "waiting"
    assert second["nodes"]["web"]["dependency_receipts"]["foundation"] == first["id"]
    assert engine.ledger.events(first["id"])


@pytest.mark.parametrize("area", ["source", "artifacts", "prepared.json", "artifacts/site/node_modules", "source/.venv"])
def test_changed_evidence_cannot_execute(engine, area):
    first = finish(engine, engine.create("development", "foundation"))
    release = engine.advance(engine.create("development", "web", False)["id"])
    node = release["nodes"]["web"]
    engine.approve(release["id"], "web", node["request_digest"])
    folder = engine.state / "releases" / release["id"] / "web"
    if area == "prepared.json":
        (folder / area).write_text("{}")
    else:
        (folder / area).mkdir(parents=True, exist_ok=True)
        (folder / area / "unexpected.txt").write_text("not reviewed")
    result = engine.advance(release["id"])
    assert result["status"] == "failed"
    assert not list(engine.deployments.glob("**/current.json"))


@pytest.mark.parametrize("approved", [False, True])
def test_cancel_prepared_release_withdraws_authority(engine, approved):
    release = engine.advance(engine.create("development", "foundation")["id"])
    node = release["nodes"]["foundation"]
    if approved:
        engine.approve(release["id"], "foundation", node["request_digest"])
    assert engine.cancel(release["id"])["status"] == "cancelled"
    request = engine.gate.status(node["request_id"])
    with pytest.raises(Refused):
        engine.gate.claim(request, release)
    with pytest.raises(Refused, match="cannot resume"):
        engine.advance(release["id"])
    assert not list(engine.deployments.rglob("resource.json"))


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group supervision")
def test_command_cleans_descendant_after_parent_exits(tmp_path):
    child = "import signal,time,pathlib; signal.signal(signal.SIGTERM,signal.SIG_IGN); pathlib.Path('ready').touch(); time.sleep(1); pathlib.Path('escaped').touch()"
    parent = (
        "import subprocess,sys,time,pathlib; "
        f"subprocess.Popen([sys.executable,'-c',{child!r}]); "
        "exec(\"while not pathlib.Path('ready').exists(): time.sleep(0.01)\")"
    )
    Process(tmp_path, lambda *_: None).run([sys.executable, "-c", parent], tmp_path)
    time.sleep(1.1)
    assert (tmp_path / "ready").exists()
    assert not (tmp_path / "escaped").exists()


def test_stale_local_pointer_needs_reconciliation(engine):
    finish(engine, engine.create("development"))
    first = engine.advance(engine.create("development", "web", False)["id"])
    finish(engine, engine.create("development", "web", False))
    engine.approve(first["id"], "web", first["nodes"]["web"]["request_digest"])
    result = engine.advance(first["id"])
    assert result["status"] == "uncertain"
    recovered = engine.recover(first["id"], "Inspected current pointer. The newer deployment is healthy.")
    assert recovered["status"] == "failed"


def test_local_authority_cannot_write_cloud(engine):
    with pytest.raises(Refused, match="remote OIDC"):
        engine.gate.context("unregistered-cloud-target")
    with pytest.raises(Refused):
        engine.gate.delegate("production", 1, 1)


@pytest.mark.parametrize("change", ["cycle", "missing", "escape", "revision"])
def test_invalid_project_contracts(engine, change):
    config = engine.spec.model_dump()
    if change == "cycle":
        config["components"]["foundation"]["needs"] = ["web"]
    elif change == "missing":
        config["components"]["web"]["needs"] = ["other"]
    elif change == "escape":
        config["components"]["web"]["path"] = "../outside"
    else:
        config["repositories"]["application"]["revision"] = "--help"
    with pytest.raises(ValidationError):
        Workspace.model_validate(config)


def test_subprocess_does_not_inherit_personal_tokens(tmp_path, monkeypatch):
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "not-for-builds")
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "not-for-builds")
    events = []
    process = Process(tmp_path, lambda event, detail: events.append((event, detail)))
    text = process.run([sys.executable, "-c", "import os,json; print(json.dumps(dict(os.environ)))"], tmp_path, capture=True)
    assert "not-for-builds" not in text
    assert events[-1][0] == "command.finished"


def test_console_auth_origin_and_same_core(engine):
    token = "t" * 40
    client = TestClient(create_app(engine, token), base_url="http://127.0.0.1:8765")
    assert client.get("/").status_code == 200
    assert client.get("/api/workspace").status_code == 401
    headers = {"Authorization": "Bearer " + token}
    assert client.get("/api/workspace", headers=headers).status_code == 200
    assert client.get("/api/workspace", headers={**headers, "Origin": "https://outside.example"}).status_code == 403
    assert client.get("/", headers={"Host": "attacker.example"}).status_code == 403
    result = client.post("/api/plan", headers=headers, json={"environment": "development"})
    assert [item["name"] for item in result.json()["components"]] == ["foundation", "web"]
    assert "frame-ancestors 'none'" in result.headers["Content-Security-Policy"]


def test_notification_retry_uses_same_id(engine):
    engine.advance(engine.create("development")["id"])
    attempts = []
    def handle(request):
        attempts.append(request.headers["Idempotency-Key"])
        return httpx.Response(503 if len(attempts) == 1 else 204)
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        assert flush(engine.ledger, "https://notify.example/", client=client)["failed"] == 1
        assert flush(engine.ledger, "https://notify.example/", client=client)["sent"] == 1
    assert attempts[0] == attempts[1]


def test_rollback_reuses_a_previous_local_artifact(engine):
    first = finish(engine, engine.create("development"))
    page = engine.path.parent / "application" / "index.html"
    page.write_text(page.read_text().replace("One traceable release.", "Another reviewed release."))
    second = finish(engine, engine.create("development", "web", False))
    assert second["nodes"]["web"]["outputs"]["digest"] != first["nodes"]["web"]["outputs"]["digest"]
    rollback = finish(engine, engine.create("development", "web", False, promote=first["id"], rollback=True))
    assert rollback["nodes"]["web"]["outputs"]["digest"] == first["nodes"]["web"]["outputs"]["digest"]


def test_committed_git_sources_are_frozen(engine):
    import subprocess
    source = engine.path.parent / "application"
    for name in ("infrastructure", "application"):
        for argv in (["git", "init", "-b", "main"], ["git", "add", "."],
                     ["git", "-c", "user.name=Test Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-m", "fixture"]):
            subprocess.run(argv, cwd=engine.path.parent / name, capture_output=True, check=True)
    engine.path.write_text(engine.path.read_text().replace('allow_uncommitted = true', 'allow_uncommitted = false'))
    engine = Engine(engine.path)
    release = engine.create("development")
    snapshot = engine.state / "releases" / release["id"] / "web" / "source" / "index.html"
    before = snapshot.read_bytes()
    (source / "index.html").write_text("dirty uncommitted work")
    assert snapshot.read_bytes() == before
    assert all(len(node["source"]["revision"]) == 40 for node in release["nodes"].values())
    first = finish(engine, release)
    second = finish(engine, engine.create("development", "web", False))
    assert first["status"] == second["status"] == "succeeded"
    assert second["nodes"]["web"]["dependency_receipts"]["foundation"] == first["id"]
    assert second["nodes"]["web"]["outputs"]["digest"] == first["nodes"]["web"]["outputs"]["digest"]


@pytest.mark.parametrize("before_creation", [True, False])
@pytest.mark.parametrize("rollback", [True, False])
def test_artifact_reuse_rejects_rewritten_origin_evidence(engine, before_creation, rollback):
    original = finish(engine, engine.create("development"))
    finish(engine, engine.create("staging", "foundation"))
    environment = "development" if rollback else "staging"

    def reuse():
        return engine.create(environment, "web", False, promote=original["id"], rollback=rollback)

    pending = None if before_creation else reuse()
    folder = engine.state / "releases" / original["id"] / "web"
    (folder / "artifacts" / "site" / "index.html").write_text("A substituted artifact")
    path = folder / "prepared.json"
    envelope = json.loads(path.read_text())
    envelope["artifact_tree"] = digest_tree(folder / "artifacts")
    path.write_text(json.dumps(envelope))
    if before_creation:
        with pytest.raises(Refused, match="Reviewed deployment artifact changed"):
            reuse()
    else:
        result = engine.advance(pending["id"])
        assert result["status"] == "uncertain"
        assert "Reviewed deployment artifact changed" in result["reason"]
        assert "request_id" not in result["nodes"]["web"]


def test_console_promotes_and_rolls_back_the_same_artifact(engine):
    original = finish(engine, engine.create("development"))
    token = "t" * 40
    client = TestClient(create_app(engine, token), base_url="http://127.0.0.1:8765")
    headers = {"Authorization": "Bearer " + token}
    path = "/api/releases/" + original["id"]
    actions = client.get(path, headers=headers).json()["artifact_actions"]
    staging = next(action for action in actions if action["environment"] == "staging")
    assert not staging["available"] and "foundation" in staging["reason"]
    finish(engine, engine.create("staging", "foundation"))
    actions = client.get(path, headers=headers).json()["artifact_actions"]
    assert all(action["available"] for action in actions)
    for action in actions:
        response = client.post("/api/releases", headers=headers, json={
            "origin": original["id"], "component": action["component"],
            "environment": action["environment"], "rollback": action["rollback"], "only": True,
        })
        assert response.status_code == 200
        release_id = response.json()["id"]
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            release = client.get("/api/releases/" + release_id, headers=headers).json()["release"]
            if release["status"] == "waiting":
                break
            time.sleep(0.02)
        assert release["status"] == "waiting"
        node = release["nodes"]["web"]
        assert client.post("/api/releases/" + release_id + "/approve", headers=headers,
                           json={"component": "web", "digest": node["request_digest"]}).status_code == 200
        result = engine.advance(release_id)
        assert result["status"] == "succeeded"
        assert result["origin_release"] == original["id"]
        assert result["nodes"]["web"]["outputs"]["digest"] == original["nodes"]["web"]["outputs"]["digest"]
    engine.path.write_text(engine.path.read_text().replace('name = "hello"', 'name = "changed"'))
    changed = Engine(engine.path)
    assert changed.artifact_actions(original) == []


def test_console_config_validation_and_active_release_boundary(engine):
    token = "t" * 40
    client = TestClient(create_app(engine, token), base_url="http://127.0.0.1:8765")
    headers = {"Authorization": "Bearer " + token}
    config = engine.path.read_text()
    assert client.post("/api/config/validate", headers=headers, json={"configuration": config}).status_code == 200
    assert client.post("/api/config/save", headers=headers, json={"configuration": config}).status_code == 200
    assert (engine.state / "previous-workspace.toml").exists()
    engine.create("development")
    assert client.post("/api/config/save", headers=headers, json={"configuration": config}).status_code == 409
    assert client.post("/api/config/validate", headers=headers, json={"configuration": "invalid = ["}).status_code == 422


def test_environment_adapter_and_release_image_share_one_contract(engine):
    config = engine.path.read_text() + """
[components.web.targets.staging]
adapter = "ecs"
image_repository = "registry.example/neon1/web"
region = "us-east-1"
cluster = "staging"
service = "web"
container = "web"
"""
    engine.path.write_text(config)
    engine = Engine(engine.path)
    digest = engine.spec.digest
    image = "registry.example/neon1/web@sha256:" + "a" * 64
    release = engine.create("staging", "web", False, image=image)
    assert release["nodes"]["web"]["adapter"] == "ecs"
    assert release["artifact_inputs"]["web"]["image"] == image
    assert engine.spec.digest == digest
    assert engine.describe("development", "web", False)["components"][0]["adapter"] == "static"
    for invalid in ("registry.example/other/web@sha256:" + "a" * 64, "registry.example/neon1/web:latest"):
        with pytest.raises(Refused, match="immutable digest"):
            engine.create("staging", "web", False, image=invalid)


def test_remote_delivery_refuses_repository_build_hooks(engine):
    from deploy_ops.delivery.gate import RemoteGate
    release = engine.create("development", "web", False)
    engine.gate = object.__new__(RemoteGate)
    with pytest.raises(Refused, match="unprivileged CI"):
        engine._prepare(release, "web")


@pytest.mark.parametrize("target_adapter", ["workflow", "compose", "terraform"])
def test_image_promotion_requires_a_destination_that_consumes_the_artifact(engine, target_adapter):
    config = engine.spec.model_dump()
    config["components"]["web"]["adapter"] = "ecs"
    config["components"]["web"]["targets"]["staging"] = {"adapter": target_adapter}
    engine.spec = Workspace.model_validate(config)
    origin = {"config_digest": engine.spec.digest, "environment": "development",
              "nodes": {"web": {"adapter": "ecs", "status": "succeeded"}}}
    with pytest.raises(Refused, match="own reviewed promotion"):
        engine._check_promotion(origin, "staging", "web", False)
    assert not any(action["environment"] == "staging" for action in engine.artifact_actions(origin))
