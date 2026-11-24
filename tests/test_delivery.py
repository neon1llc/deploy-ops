# tests/test_delivery.py - Check complete local paths and shared deployment boundaries.


import json
import os
import shutil
import subprocess
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
from deploy_ops.delivery.process import AZURE_OPERATIONS, Process, command_metadata
from deploy_ops.delivery.scaffold import create
from deploy_ops.delivery.spec import Repository, Workspace
from deploy_ops.errors import Refused


# Create a disposable two-repository local workspace
@pytest.fixture
def engine(tmp_path):
    return Engine(create(tmp_path / "workspace"))


# Retained dependency discovery is independent of the operator's recent-history page
def test_dependency_outputs_survive_long_unrelated_history(engine):
    first = finish(engine, engine.create("development", "foundation"))
    for _ in range(205):
        engine.create("staging", "foundation")
    assert first["id"] not in {item["id"] for item in engine.ledger.list()}
    release = engine.advance(engine.create("development", "web", False)["id"])
    assert release["status"] == "waiting"
    assert release["nodes"]["web"]["dependency_receipts"] == {"foundation": first["id"]}
    assert engine.ledger.latest_outputs("staging", "foundation", engine.spec.digest) is None
    assert engine.ledger.latest_outputs("development", "foundation", "changed-contract") is None


# Display clipping cannot remove context needed to recognize a synthetic credential
def test_approved_local_inputs_cannot_silently_change_producer(engine):
    first = finish(engine, engine.create('development', 'foundation'))
    app = engine.advance(engine.create('development', 'web', False)['id'])
    node = app['nodes']['web']
    path = engine.state / 'releases' / app['id'] / 'web' / 'prepared.json'
    original = path.read_bytes()
    assert json.loads(original)['local_dependencies']['foundation']['release_id'] == first['id']
    newer = finish(engine, engine.create('development', 'foundation'))
    assert newer['id'] != first['id']
    engine.approve(app['id'], 'web', node['request_digest'])
    refused = engine.advance(app['id'])
    assert refused['status'] == 'failed'
    assert 'dependency changed' in refused['reason']
    assert refused['nodes']['web']['dependency_receipts']['foundation'] == first['id']
    assert path.read_bytes() == original
    assert engine.gate.status(node['request_id'])['status'] == 'approved'


# A later producer claim invalidates older outputs even when the attempted mutation fails
def test_output_generation_follows_claim_order_instead_of_creation_order(engine):
    older = engine.create('development', 'foundation')
    newer = finish(engine, engine.create('development', 'foundation'))
    applied_last = finish(engine, older)
    receipt = engine.ledger.latest_outputs('development', 'foundation', engine.spec.digest)
    assert receipt['receipt'] == applied_last['id']
    assert receipt['receipt'] != newer['id']


# A later producer claim invalidates older outputs even when the attempted mutation fails
def test_interrupted_producer_blocks_predecessor_reuse(engine, monkeypatch):
    from deploy_ops.delivery.adapters import local
    finish(engine, engine.create('development', 'foundation'))
    producer = engine.advance(engine.create('development', 'foundation')['id'])
    node = producer['nodes']['foundation']
    engine.approve(producer['id'], 'foundation', node['request_digest'])

    # A lost result cannot prove that the provider made no change
    def lost_result(*args):
        raise Refused('Synthetic lost apply result')

    monkeypatch.setattr(local, 'apply', lost_result)
    assert engine.advance(producer['id'])['status'] == 'uncertain'
    assert engine.ledger.latest_outputs('development', 'foundation', engine.spec.digest) is None
    app = engine.advance(engine.create('development', 'web', False)['id'])
    assert app['status'] == 'failed'
    assert 'Deploy dependency foundation' in app['reason']
    assert not app['nodes']['web'].get('request_id')


# Components from one repository share a single frozen capture despite later working-file edits
def test_release_freezes_each_repository_once(engine, monkeypatch):
    from deploy_ops.delivery import engine as engine_module
    from deploy_ops.delivery.configuration import add
    source = add(engine.path.read_text(), 'components', 'other', {
        'repository': 'application', 'kind': 'application', 'adapter': 'static',
        'options': {'directory': '.'}})['toml']
    engine.path.write_text(source)
    engine = Engine(engine.path)
    snapshot = engine_module.snapshot
    calls = []

    # Move the original input after capture; both component copies must retain the captured bytes
    def capture(repository, base, destination):
        calls.append(repository.path)
        result = snapshot(repository, base, destination)
        if repository.path == 'application':
            (base / 'application' / 'later.txt').write_text('After the repository freeze')
        return result

    monkeypatch.setattr(engine_module, 'snapshot', capture)
    release = engine.create('development')
    assert calls.count('application') == 1
    assert release['nodes']['web']['source'] == release['nodes']['other']['source']
    assert not (engine.state / 'releases' / release['id'] / 'other' / 'source' / 'later.txt').exists()


# Display clipping cannot remove context needed to recognize a synthetic credential
@pytest.mark.parametrize("kind", ["registered", "assignment", "multibyte"])
def test_command_redacts_before_display_clipping(engine, kind):
    release = engine.create("development", "foundation")
    secret = "synthetic-boundary-value-0123456789"
    prefix = "password=" if kind == "assignment" else ""
    suffix = "界" * 3980 if kind == "multibyte" else "x" * 3980
    text = prefix + secret + " " + suffix
    events = []

    # Use the normal ledger so persisted diagnostics have the same privacy boundary
    def emit(name, detail):
        events.append((name, detail))
        engine.ledger.event(release["id"], name, detail)

    process = Process(engine.state, emit, {} if kind == "assignment" else {"TEST_TOKEN": secret})
    returned = process.run([sys.executable, "-c", "print(" + repr(text) + ", end='')"], engine.state)
    assert returned == text
    display = next(detail["text"] for name, detail in events if name == "command.output")
    assert len(display) <= 4000
    assert "0123456789" not in display
    headers = {"Authorization": "Bearer " + "t" * 40}
    client = TestClient(create_app(engine, "t" * 40), base_url="http://127.0.0.1:8765")
    response = client.get("/api/releases/" + release["id"], headers=headers)
    assert response.status_code == 200
    assert "0123456789" not in response.text
    assert "0123456789" not in json.dumps(engine.ledger.store.audit())
    assert engine.ledger.store.verify()["events"] > 0


# Deliver and promote one artifact through the full local lifecycle
def test_retained_history_needs_no_current_engine(engine, monkeypatch):
    from deploy_ops.cli import dispatch, parser
    from deploy_ops.delivery.ledger import Ledger
    release = finish(engine, engine.create('development', 'foundation'))
    audit = engine.ledger.store.audit()
    engine.path.write_text('invalid = [ workspace')
    monkeypatch.setenv('DEPLOY_OPS_ISOLATED_GRANT_FILE', '/missing/registration')

    # Read construction must never consult current execution or grant configuration
    def forbidden(*args, **kwargs):
        raise AssertionError('Engine construction during retained read')

    monkeypatch.setattr(Engine, '__init__', forbidden)
    args = ['--project', str(engine.path), '--state', str(engine.state)]
    assert dispatch(parser().parse_args([*args, 'status', release['id']])) == release
    assert dispatch(parser().parse_args([*args, 'events', release['id']]))
    reader = Ledger.open_existing(engine.state)
    assert reader.store.audit() == audit
    with pytest.raises(Refused, match='cannot mutate'):
        reader.save(release)


# Mistyped locations and unsupported versions never create or modify retained state
def test_retained_inspection_refuses_missing_and_unknown_state(engine, tmp_path):
    from deploy_ops.delivery.ledger import Ledger
    missing = tmp_path / 'missing' / 'state'
    with pytest.raises(Refused, match='not found'):
        Ledger.open_existing(missing)
    assert not missing.parent.exists()
    with engine.ledger.store.connect() as connection:
        connection.execute('PRAGMA user_version=99')
    database = engine.ledger.store.path
    original = database.read_bytes()
    with pytest.raises(Refused, match='Unsupported database version'):
        Ledger.open_existing(engine.state)
    assert database.read_bytes() == original


# Read-only inspection includes committed live WAL data without checkpointing it away
def test_retained_reader_sees_live_wal(engine):
    from deploy_ops.delivery.ledger import Ledger
    with engine.ledger.store.connect() as keeper:
        keeper.execute('SELECT COUNT(*) FROM releases').fetchone()
        release = engine.create('development', 'foundation')
        assert engine.ledger.store.path.with_name('state.db-wal').stat().st_size > 0
        assert Ledger.open_existing(engine.state).get(release['id']) == release


# Deliver and promote one artifact through the full local lifecycle
def test_recovery_preserves_controller_completion_after_process_death(engine):
    release = engine.advance(engine.create('development', 'foundation')['id'])
    node = release['nodes']['foundation']
    engine.approve(release['id'], 'foundation', node['request_digest'])
    script = '''
import os, sys
from pathlib import Path
from deploy_ops.delivery.engine import Engine
engine = Engine(Path(sys.argv[1]))
save = engine.ledger.save
# Exit after controller completion and before the release journal can retain outputs
def cut(release, event=None, detail=None):
    if event == 'component.succeeded':
        os._exit(73)
    return save(release, event, detail)
engine.ledger.save = cut
engine.advance(sys.argv[2])
'''
    result = subprocess.run([sys.executable, '-c', script, str(engine.path), release['id']],
                            capture_output=True, timeout=20)
    assert result.returncode == 73
    assert engine.gate.status(node['request_id'])['status'] == 'succeeded'
    assert engine.ledger.get(release['id'])['nodes']['foundation']['status'] == 'running'
    audit = engine.ledger.store.audit()
    recovered = Engine(engine.path).recover(release['id'], 'Inspected completed local directory after the stopped worker')
    assert recovered['nodes']['foundation']['status'] == 'unverified'
    assert recovered['nodes']['foundation']['controller_outcome'] == 'succeeded'
    assert not recovered['nodes']['foundation'].get('outputs')
    assert engine.gate.status(node['request_id'])['status'] == 'succeeded'
    assert engine.ledger.store.audit()[:len(audit)] == audit
    events = engine.ledger.events(release['id'])
    assert Engine(engine.path).recover(release['id'], 'Repeated inspection of the same completed local directory') == recovered
    assert engine.ledger.events(release['id']) == events
    assert len(list(engine.deployments.rglob('resource.json'))) == 1


# Deliver and promote one artifact through the full local lifecycle
def test_overlapping_registered_values_preserve_no_display_fragments(tmp_path):
    text = 'abcdefghijklmnopqrstuv abcdefghijklmnopqrstuv'
    events = []
    process = Process(tmp_path, lambda name, detail: events.append((name, detail)),
                      {'FIRST_TOKEN': 'abcdefghijklmno', 'SECOND_TOKEN': 'klmnopqrstuv'})
    assert process.run([sys.executable, '-c', 'print(' + repr(text) + ', end="")'], tmp_path) == text
    display = next(detail['text'] for name, detail in events if name == 'command.output')
    assert display == '[redacted] [redacted]'


# Deliver and promote one artifact through the full local lifecycle
def test_complete_delivery_and_promotion(tmp_path):
    result = run(tmp_path / "complete")
    assert result["status"] == "succeeded"
    assert result["artifact_preserved"] is True
    assert result["http_check"].startswith("200")


# Reuse infrastructure while consuming only the granted application authority
def test_bounded_delegation_and_independent_app_release(engine):
    engine.gate.delegate("development", 1, 1)
    first = engine.advance(engine.create("development")["id"])
    assert first["status"] == "succeeded"
    second = engine.advance(engine.create("development", "web", False)["id"])
    assert second["status"] == "waiting"
    assert second["nodes"]["web"]["dependency_receipts"]["foundation"] == first["id"]
    assert engine.ledger.events(first["id"])


# Refuse changed source and artifacts even after approval
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


# Cancellation invalidates pending and already approved local authority
@pytest.mark.parametrize('last_component', [False, True])
def test_cancellation_survives_component_completion(engine, monkeypatch, last_component):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    engine.gate.delegate('development', 1, 1)
    release = engine.create('development', 'foundation' if last_component else None)
    entered, proceed = threading.Event(), threading.Event()
    save = engine.ledger.save

    # Stop with the controller finished but the stale release copy still waiting to be saved
    def completion_barrier(current, event=None, detail=None):
        if event == 'component.succeeded' and detail['component'] == 'foundation':
            entered.set()
            assert proceed.wait(10)
        return save(current, event, detail)

    monkeypatch.setattr(engine.ledger, 'save', completion_barrier)
    with ThreadPoolExecutor(max_workers=1) as pool:
        worker = pool.submit(engine.advance, release['id'])
        assert entered.wait(10)
        try:
            assert engine.cancel(release['id'])['status'] == 'cancel_requested'
        finally:
            proceed.set()
        result = worker.result(timeout=10)
    assert result['status'] == 'cancelled'
    assert result['nodes']['foundation']['status'] == 'succeeded'
    assert not any(event['name'] == 'component.preparing' and event['detail'].get('component') == 'web'
                   for event in engine.ledger.events(release['id']))
    assert not list(engine.deployments.rglob('current.json'))


# Cancellation invalidates pending and already approved local authority
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


# Retain a real admitted local claim while deliberately omitting adapter application
def claim_only(engine, release, identifier):
    scope = 'release:' + release['id']
    engine.ledger.lock(scope, release['id'], kind='worker')
    try:
        return engine.gate.claim(engine.gate.status(identifier), release,
            local_input_check=lambda: engine.ledger.worker_binding(scope, release['id']))
    finally:
        engine.ledger.unlock(scope, release['id'])



# Controller reconciliation can close delivery tracking without rewriting a final decision
@pytest.mark.parametrize("outcome", ["failed", "succeeded", None])
def test_cancel_after_controller_inspection(engine, outcome):
    from deploy_ops.delivery.operator import Operator
    operator = Operator(engine)
    release = engine.advance(engine.create("development", "foundation")["id"])
    node = release["nodes"]["foundation"]
    engine.approve(release["id"], "foundation", node["request_digest"])
    claim_only(engine, release, node["request_id"])
    operator.call("pause")
    if outcome:
        operator.call("resolve", node["request_id"], {"outcome": outcome,
                      "note": "Inspected the disposable claim-only fixture; no apply command was invoked"})
    client = TestClient(create_app(engine, "t" * 40), base_url="http://127.0.0.1:8765")
    response = client.post("/api/releases/" + release["id"] + "/cancel",
                           headers={"Authorization": "Bearer " + "t" * 40}, json={})
    assert response.status_code == (200 if outcome == "failed" else 409)
    assert engine.ledger.get(release["id"])["status"] == ("cancelled" if outcome == "failed" else "waiting")
    assert engine.gate.status(node["request_id"])["status"] == (outcome or "uncertain")
    assert not list(engine.deployments.rglob("resource.json"))


# Descendants cannot outlive command supervision after their parent exits
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


# A newer deployment prevents an older approval from replacing its pointer
def test_stale_local_pointer_needs_reconciliation(engine):
    finish(engine, engine.create("development"))
    first = engine.advance(engine.create("development", "web", False)["id"])
    finish(engine, engine.create("development", "web", False))
    engine.approve(first["id"], "web", first["nodes"]["web"]["request_digest"])
    result = engine.advance(first["id"])
    assert result["status"] == "uncertain"
    recovered = engine.recover(first["id"], "Inspected current pointer. The newer deployment is healthy.")
    assert recovered["status"] == "failed"


# Local sandbox authority cannot impersonate cloud access
def test_local_authority_cannot_write_cloud(engine):
    with pytest.raises(Refused, match="remote OIDC"):
        engine.gate.context("unregistered-cloud-target")
    with pytest.raises(Refused):
        engine.gate.delegate("production", 1, 1)


# Reject graph, source and revision errors before execution
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


# Build subprocesses do not inherit ambient provider or workflow tokens
def test_subprocess_does_not_inherit_personal_tokens(tmp_path, monkeypatch):
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "not-for-builds")
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "not-for-builds")
    events = []
    process = Process(tmp_path, lambda event, detail: events.append((event, detail)))
    text = process.run([sys.executable, "-c", "import os,json; print(json.dumps(dict(os.environ)))"], tmp_path, capture=True)
    assert "not-for-builds" not in text
    assert [event for event, _ in events] == ["command.started", "command.launched", "command.finished", "command.stopped"]


# All public operation labels come from fixed native prefixes rather than arbitrary arguments
@pytest.mark.parametrize("operation", AZURE_OPERATIONS)
def test_native_azure_operation_labels_are_allowlisted(operation):
    argv = ["/native/bin/az", *operation, "--secret", "argument-secret-sentinel"]
    assert command_metadata(argv) == {"tool": "az", "operation": "azure." + ".".join(operation)}
    assert command_metadata(["other", *operation]) == {"tool": "other"}
    assert command_metadata(["az", "unknown-secret-sentinel", *operation]) == {"tool": "az"}


# Real subprocess failure keeps raw stdout, stderr and argument secrets out of events and errors
@pytest.mark.parametrize("known", [True, False])
def test_native_azure_failure_labels_do_not_expose_diagnostics(tmp_path, known):
    executable = tmp_path / "az"
    executable.write_text("#!/bin/sh\nprintf 'stdout-secret-sentinel'\nprintf 'stderr-secret-sentinel' >&2\nexit 7\n")
    executable.chmod(0o700)
    events = []
    process = Process(tmp_path, lambda event, detail: events.append((event, detail)))
    operation = ["deployment", "group", "what-if"] if known else ["unknown-secret-sentinel"]
    with pytest.raises(Refused) as failure:
        process.run([str(executable), *operation, "--secret", "argument-secret-sentinel"], tmp_path, capture=True)
    evidence = json.dumps(events) + str(failure.value)
    assert "secret-sentinel" not in evidence
    assert [event for event, _ in events] == ["command.started", "command.launched", "command.failed", "command.stopped"]
    outcome = next(detail for event, detail in events if event == "command.failed")
    assert outcome["exit_code"] == 7
    assert outcome["elapsed_ms"] >= 0
    assert outcome["outcome"] == "failed"
    assert all(detail["command_id"] == outcome["command_id"] for _, detail in events)
    assert ("azure.deployment.group.what-if" in evidence) is known


# Successful captured commands share the same safe label without emitting their response body
def test_native_azure_success_labels_keep_captured_output_private(tmp_path):
    executable = tmp_path / "az"
    executable.write_text("#!/bin/sh\nprintf 'captured-secret-sentinel'\n")
    executable.chmod(0o700)
    events = []
    process = Process(tmp_path, lambda event, detail: events.append((event, detail)))
    assert process.run([str(executable), "account", "show"], tmp_path, capture=True) == "captured-secret-sentinel"
    assert events[0][0] == "command.started"
    identifier = events[0][1]["command_id"]
    assert len(identifier) == 32
    assert events[0][1] == {"tool": "az", "operation": "azure.account.show", "command_id": identifier}
    assert [event for event, _ in events] == ["command.started", "command.launched", "command.finished", "command.stopped"]
    assert next(detail for event, detail in events if event == "command.finished")["elapsed_ms"] >= 0
    assert all(detail["command_id"] == identifier for _, detail in events)
    assert "captured-secret-sentinel" not in json.dumps(events)


# An interrupted command leaves a durable local outcome without suggesting external rollback
def test_command_deadline_records_interruption(tmp_path):
    events = []
    process = Process(tmp_path, lambda event, detail: events.append((event, detail)))
    with pytest.raises(Refused, match="deadline"):
        process.run([sys.executable, "-c", "import time; time.sleep(30)"], tmp_path, timeout=0)
    terminal = [detail for event, detail in events if event == "command.failed"]
    assert len(terminal) == 1
    assert terminal[0]["outcome"] == "interrupted"
    assert terminal[0]["elapsed_ms"] >= 0
    assert terminal[0]["exit_code"] is None


# Failed startup records a safe actionable result without leaking the host working directory
def test_command_launch_failure_records_no_execution(tmp_path):
    events = []
    process = Process(tmp_path, lambda event, detail: events.append((event, detail)))
    with pytest.raises(Refused, match="could not start") as failure:
        process.run([sys.executable, "argument-secret-sentinel"], tmp_path / "missing-secret-sentinel")
    assert events[-1][1]["outcome"] == "not-started"
    assert events[-1][1]["exit_code"] is None
    assert events[-1][1]["elapsed_ms"] >= 0
    assert "secret-sentinel" not in json.dumps(events) + str(failure.value)


# Require loopback and token boundaries while reusing the shared graph
def test_console_auth_origin_and_same_core(engine):
    token = "t" * 40
    client = TestClient(create_app(engine, token), base_url="http://127.0.0.1:8765")
    assert client.get("/").status_code == 200
    for asset in ("app.js", "shell.js", "operator.js", "paging.js", "isolated.js", "style.css", "appearance.mjs", "theme.mjs"):
        assert client.get("/assets/" + asset).status_code == 200
        assert client.get("/" + asset).status_code == 200
    assert client.get("/assets/operator.token").status_code == 404
    assert client.get("/operator.token").status_code == 404
    assert client.get("/api/workspace").status_code == 401
    headers = {"Authorization": "Bearer " + token}
    assert client.get("/api/workspace", headers=headers).status_code == 200
    assert client.get("/api/workspace", headers={**headers, "Origin": "https://outside.example"}).status_code == 403
    assert client.get("/", headers={"Host": "attacker.example"}).status_code == 403
    result = client.post("/api/plan", headers=headers, json={"environment": "development"})
    assert [item["name"] for item in result.json()["components"]] == ["foundation", "web"]
    assert "frame-ancestors 'none'" in result.headers["Content-Security-Policy"]


# A retry preserves the receiver deduplication identity
def test_notification_retry_uses_same_id(engine):
    engine.advance(engine.create("development")["id"])
    attempts = []


    # Simulate one transient receiver failure followed by acceptance
    def handle(request):
        attempts.append(request.headers["Idempotency-Key"])
        return httpx.Response(503 if len(attempts) == 1 else 204)
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        assert flush(engine.ledger, "https://notify.example/", client=client)["failed"] == 1
        assert flush(engine.ledger, "https://notify.example/", client=client)["sent"] == 1
    assert attempts[0] == attempts[1]


# Rollback reuses reviewed bytes rather than rebuilding old source
def test_rollback_reuses_a_previous_local_artifact(engine):
    first = finish(engine, engine.create("development"))
    page = engine.path.parent / "application" / "index.html"
    page.write_text(page.read_text().replace("One traceable release.", "Another reviewed release."))
    second = finish(engine, engine.create("development", "web", False))
    assert second["nodes"]["web"]["outputs"]["digest"] != first["nodes"]["web"]["outputs"]["digest"]
    rollback = finish(engine, engine.create("development", "web", False, promote=first["id"], rollback=True))
    assert rollback["nodes"]["web"]["outputs"]["digest"] == first["nodes"]["web"]["outputs"]["digest"]


# Uncommitted edits cannot change a release frozen from Git history
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


# Rewriting both artifact and local metadata cannot forge reuse authority
@pytest.mark.parametrize("before_creation", [True, False])
@pytest.mark.parametrize("rollback", [True, False])
def test_artifact_reuse_rejects_rewritten_origin_evidence(engine, before_creation, rollback):
    original = finish(engine, engine.create("development"))
    finish(engine, engine.create("staging", "foundation"))
    environment = "development" if rollback else "staging"


    # Request either promotion or rollback from the same reviewed origin
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


# Console actions preserve artifact identity through new approvals
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
        deadline = time.monotonic() + 30
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


# Configuration saves stop while releases still depend on the old contract
def test_console_config_validation_and_active_release_boundary(engine):
    token = "t" * 40
    client = TestClient(create_app(engine, token), base_url="http://127.0.0.1:8765")
    headers = {"Authorization": "Bearer " + token}
    config = engine.path.read_text()
    from deploy_ops.delivery.configuration import source_hash
    save = {"configuration": config, "source_hash": source_hash(config)}
    assert client.post("/api/config/validate", headers=headers, json={"configuration": config}).status_code == 200
    assert client.post("/api/config/save", headers=headers, json=save).status_code == 200
    assert (engine.state / "previous-workspace.toml").exists()
    engine.create("development")
    assert client.post("/api/config/save", headers=headers, json=save).status_code == 409
    assert client.post("/api/config/validate", headers=headers, json={"configuration": "invalid = ["}).status_code == 422


# Environment overrides and release images retain their separate identities
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


# Credentialed remote workers cannot execute repository build hooks
def test_remote_delivery_refuses_repository_build_hooks(engine):
    from deploy_ops.delivery.gate import RemoteGate
    release = engine.create("development", "web", False)
    engine.gate = object.__new__(RemoteGate)
    with pytest.raises(Refused, match="unprivileged CI"):
        engine._prepare(release, "web")


# Promotion requires a destination that understands the retained artifact kind
@pytest.mark.parametrize("target_adapter", ["workflow", "compose", "terraform"])
def test_image_promotion_requires_a_destination_that_consumes_the_artifact(engine, target_adapter):
    config = engine.spec.model_dump()
    config["components"]["web"]["adapter"] = "ecs"
    config["components"]["web"]["options"] = {}
    config["components"]["web"]["targets"]["staging"] = {"adapter": target_adapter}
    if target_adapter == 'compose':
        config["components"]["web"]["targets"]["staging"]["file"] = 'compose.yaml'
    engine.spec = Workspace.model_validate(config)
    origin = {"config_digest": engine.spec.digest, "environment": "development",
              "nodes": {"web": {"adapter": "ecs", "status": "succeeded"}}}
    with pytest.raises(Refused, match="own reviewed promotion"):
        engine._check_promotion(origin, "staging", "web", False)
    assert not any(action["environment"] == "staging" for action in engine.artifact_actions(origin))


# Keep approval observations current without starting execution or returning controller secrets
def test_console_separates_current_approval_from_execution(engine, monkeypatch):
    release = engine.advance(engine.create("development")["id"])
    name = next(name for name, node in release["nodes"].items() if node["status"] == "waiting")
    node = release["nodes"][name]
    token = "t" * 40
    client = TestClient(create_app(engine, token), base_url="http://127.0.0.1:8765",
                        headers={"Authorization": "Bearer " + token})
    route = "/api/releases/" + release["id"]
    pending = client.get(route).json()["request_statuses"][name]
    assert pending["status"] == "pending" and pending["digest"] == node["request_digest"]
    assert pending["reason"] and pending["timing"]["approval_expires_at"] is None
    response = client.post(route + "/approve", json={"component": name, "digest": node["request_digest"]})
    assert response.status_code == 200
    observed = client.get(route).json()
    approved = observed["request_statuses"][name]
    assert approved["status"] == "approved" and approved["digest"] == node["request_digest"]
    assert approved["timing"]["approval_expires_at"] is not None
    assert observed["release"]["status"] == "waiting"
    assert observed["release"]["nodes"][name]["status"] == "waiting"
    assert not any(event["name"] == "component.succeeded" for event in observed["events"])

    # Provider-independent expiry stays authoritative even when the release has not advanced
    request = engine.gate.status(node["request_id"])
    monkeypatch.setattr(engine.gate.controller, "now", lambda: request["expires_at"] + 10000)
    assert client.get(route).json()["request_statuses"][name]["status"] == "expired"

    # Read failures preserve the release view without echoing native errors or guessing authority
    def unavailable(request_id):
        raise RuntimeError("private-credential-sentinel")
    monkeypatch.setattr(engine.gate, "status", unavailable)
    failed = client.get(route)
    assert failed.status_code == 200
    assert failed.json()["request_statuses"][name] == {"status": "unavailable", "digest": None}
    assert "private-credential-sentinel" not in failed.text


# Check exact approval and native readback buttons through the actual console render functions
def test_console_release_action_contract():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for the dependency-free console DOM contract")
    root = Path(__file__).resolve().parents[1]
    subprocess.run([node, str(root / "tests" / "release_ui.cjs")], cwd=root,
                   check=True, capture_output=True, text=True, timeout=20)


# Stop existing authority, inspect uncertain work and reconcile without replaying a mutation
def test_local_operator_stop_and_recovery(engine):
    from deploy_ops.delivery.operator import Operator
    operator = Operator(engine)
    client = TestClient(create_app(engine, 'fixture-' + 'x' * 40), base_url='http://127.0.0.1:8765')
    headers = {'Authorization': 'Bearer fixture-' + 'x' * 40}
    release = engine.advance(engine.create('development', 'foundation')['id'])
    node = release['nodes']['foundation']
    identifier = node['request_id']
    base = '/api/operator/'
    assert client.get(base + 'status').status_code == 401
    assert client.post(base + 'pause', headers={**headers, 'Origin': 'https://outside.example'}, json={}).status_code == 403
    stale = client.post(base + 'decision', headers=headers, json={'identifier': identifier,
        'values': {'digest': '0' * 64, 'decision': 'approve'}})
    assert stale.status_code == 409
    operator.call('decision', identifier, {'digest': node['request_digest'], 'decision': 'approve'})
    assert engine.ledger.get(release['id'])['status'] == 'waiting'
    lease = claim_only(engine, release, identifier)
    grants = engine.gate.delegate('development', 1, 1)
    assert client.post(base + 'pause', headers=headers, json={}).json() == {'paused': True}
    observed = client.get(base + 'request', params={'identifier': identifier}, headers=headers).json()
    assert observed['status'] == 'uncertain'
    assert 'execution' not in observed and lease['token'] not in json.dumps(observed)
    with pytest.raises(Refused):
        engine.gate.heartbeat(lease)
    assert client.post(base + 'resolve', headers=headers, json={'identifier': identifier,
        'values': {'outcome': 'failed', 'note': 'Inspected local destination; no mutation was executed'}}).json()['status'] == 'failed'
    assert client.post(base + 'resume', headers=headers, json={}).json() == {'paused': False}
    assert not any(grant['active'] for grant in operator.call('delegations'))
    assert not list(engine.deployments.rglob('resource.json'))
    fresh = engine.advance(engine.create('development', 'foundation')['id'])
    assert fresh['status'] == 'waiting'
    assert operator.call('audit')['events'] > 0
    operator.call('revoke', grants[0]['id'])
    invalid = client.post(base + 'resolve', headers=headers, json={'identifier': identifier,
        'values': {'outcome': 'failed', 'note': 'credential-sentinel', 'token': 'secret-sentinel'}})
    assert invalid.status_code == 422 and 'secret-sentinel' not in invalid.text


# Do not convert a console token into remote or isolated administrator authority
def test_operator_unregistered_mode_and_invalid_body(engine, monkeypatch):
    from deploy_ops.delivery.operator import Operator
    client = TestClient(create_app(engine, 'fixture-' + 'x' * 40), base_url='http://127.0.0.1:8765')
    headers = {'Authorization': 'Bearer fixture-' + 'x' * 40}
    response = client.post('/api/operator/pause', headers=headers, json=['private-token-sentinel'])
    assert response.status_code == 422 and 'private-token-sentinel' not in response.text
    monkeypatch.setattr(engine, 'gate', object())
    assert Operator(engine).status()['available'] is False
    assert client.post('/api/operator/pause', headers=headers, json={}).status_code == 403


# Serve only an immutable reviewed revision and stop process-owned listeners independently
def test_static_serving_owned_lifecycle(engine):
    import socket
    from deploy_ops.delivery.serving import Sessions
    release = finish(engine, engine.create('development'))
    serving = Sessions(8765)
    occupied = socket.socket(); occupied.bind(('127.0.0.1', 0)); occupied.listen()
    try:
        first = serving.start(engine, release['id'], 'web')
        assert first == serving.start(engine, release['id'], 'web')
        response = httpx.get(first['url'])
        assert response.status_code == 200
        assert 'access-control-allow-origin' not in response.headers and 'set-cookie' not in response.headers
        assert response.headers['cache-control'] == 'no-store'
        assert httpx.head(first['url']).content == b''
        assert httpx.get(first['url'], headers={'Host': 'outside.example'}).status_code == 403
        assert httpx.get(first['url'] + '/prepared.json').status_code == 404
        assert httpx.get(first['url'] + '/%2e%2e/secret').status_code == 403
        assert httpx.get(first['url'] + '/missing/').status_code == 404
        with pytest.raises(Refused):
            serving.start(engine, release['id'], 'foundation')
        second_release = finish(engine, engine.create('development', 'web', False))
        with pytest.raises(Refused):
            serving.start(engine, second_release['id'], 'web', occupied.getsockname()[1])
        assert len(serving.list()) == 1
        second = serving.start(engine, second_release['id'], 'web')
        assert second['id'] != first['id']
        files = Path(release['nodes']['web']['outputs']['path'])
        original = (files / 'index.html').read_bytes()
        (files / 'index.html').write_text('changed')
        assert httpx.get(first['url']).status_code == 410
        (files / 'index.html').unlink()
        outside = files.parent / 'private.txt'; outside.write_text('private-sentinel')
        (files / 'index.html').symlink_to(outside)
        refused = httpx.get(first['url'])
        assert refused.status_code == 410 and 'private-sentinel' not in refused.text
        (files / 'index.html').unlink(); (files / 'index.html').write_bytes(original)
        evidence = engine.state / 'releases' / release['id'] / 'web' / 'prepared.json'
        saved = evidence.read_bytes(); evidence.write_text('{}')
        assert httpx.get(first['url']).status_code == 410
        evidence.write_bytes(saved)
        serving.stop(first['id']); serving.stop(first['id']); serving.stop('not-owned')
        assert files.is_dir() and httpx.get(second['url']).status_code == 200
        (files / 'index.html').write_text('changed')
        with pytest.raises(Refused):
            serving.start(engine, release['id'], 'web')
        assert occupied.getsockname()[1] > 0
    finally:
        serving.close(); occupied.close()
    with pytest.raises(httpx.ConnectError):
        httpx.get(second['url'], timeout=1)


# Console lifecycle preserves authentication and closes owned sessions on shutdown
def test_console_static_serving_shutdown_and_auth(engine):
    release = finish(engine, engine.create('development'))
    headers = {'Authorization': 'Bearer ' + 'x' * 43}
    app = create_app(engine, 'x' * 43)
    with TestClient(app, base_url='http://127.0.0.1:8765') as client:
        assert client.get('/api/serving').status_code == 401
        assert client.post('/api/serving/start', headers={**headers, 'Origin': 'https://outside.example'}, json={}).status_code == 403
        session = client.post('/api/serving/start', headers=headers, json={'release': release['id'], 'component': 'web'}).json()
        assert httpx.get(session['url']).status_code == 200
        assert client.get('/api/serving', headers=headers).json()[0]['id'] == session['id']
        assert client.post('/api/serving/stop', headers=headers, json={'id': 'f' * 32}).json()['status'] == 'not-owned-or-stopped'
        assert httpx.get(session['url']).status_code == 200
        assert client.post('/api/serving/start', headers=headers, json={'release': release['id'], 'component': 'web', 'port': 8765}).status_code == 422
        assert client.post('/api/serving/start', headers=headers, json={'release': release['id'], 'component': 'web', 'path': '/tmp'}).status_code == 422
    with pytest.raises(httpx.ConnectError):
        httpx.get(session['url'], timeout=1)
    with TestClient(create_app(engine, 'x' * 43), base_url='http://127.0.0.1:8765') as client:
        assert client.get('/api/serving', headers=headers).json() == []


# A component timeout cannot expand the exact authority admitted by the real controller
def test_release_timing_projects_short_lease_and_lost_contact(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from deploy_ops.console import create_app
    from deploy_ops.delivery.operator import Operator
    from deploy_ops.delivery.adapters import local
    path = create(tmp_path / 'timed-workspace')
    engine = Engine(path)
    controller = engine.gate.controller
    controller.settings.execution_seconds = 60
    clock = [controller.now()]
    controller.clock = lambda: clock[0]
    release = engine.advance(engine.create('development', 'foundation', False)['id'])
    node = release['nodes']['foundation']
    engine.approve(release['id'], 'foundation', node['request_digest'])
    observed = []
    apply = local.apply

    # Inspect the supervisor that actually receives the admitted lease, then perform the local mutation
    def bounded(context, prepared):
        observed.append((context.component.timeout_seconds, context.process.deadline))
        return apply(context, prepared)

    monkeypatch.setattr(local, 'apply', bounded)
    result = engine.advance(release['id'])
    assert result['status'] == 'succeeded'
    assert observed == [(600, clock[0] + 60)]
    projected = Operator(engine).call('request', node['request_id'])
    assert projected['timing']['execution_deadline'] == clock[0] + 60
    assert projected['timing']['execution_started_at'] == clock[0]
    assert 'execution' not in projected

    # A different real claim loses contact; neither request expiry nor a long command timeout clears it
    next_release = engine.advance(engine.create('staging', 'foundation', False)['id'])
    pending = next_release['nodes']['foundation']
    engine.approve(next_release['id'], 'foundation', pending['request_digest'])
    request = engine.gate.status(pending['request_id'])
    lease = engine.gate.claim(request, next_release)
    clock[0] += 61
    with pytest.raises(Refused):
        engine.gate.heartbeat(lease)
    client = TestClient(create_app(engine, 't' * 40), base_url='http://127.0.0.1:8765')
    detail = client.get('/api/releases/' + next_release['id'], headers={'Authorization': 'Bearer ' + 't' * 40}).json()
    current = detail['request_statuses']['foundation']
    assert current['status'] == 'uncertain'
    assert current['reason']
    assert current['timing']['last_contact_age_seconds'] == 61
    assert current['timing']['execution_remaining_seconds'] == 0
    assert current['timing']['last_seen'] == clock[0] - 61
    assert current['timing']['execution_deadline'] == clock[0] - 1
    assert current['timing']['observed_at'] == clock[0]
    assert lease['token'] not in json.dumps(detail)
    assert 'token_hash' not in json.dumps(detail)


# A real child crosses the exit barrier before the first poll, so terminal accounting cannot borrow the running check
@pytest.mark.parametrize('stdout_bytes,stderr_bytes,refused', [
    (11, 10_000_000, True), (6_000_000, 4_000_001, True), (11, 9_999_989, False)])
def test_completed_command_accounts_for_both_private_streams(tmp_path, monkeypatch, stdout_bytes, stderr_bytes, refused):
    native = subprocess.Popen
    calls, streams, events = [], [], []
    marker = tmp_path / 'effect-completed'

    # Retain a real completed process and its actual private file handles without changing its output
    def completed(*args, **kwargs):
        child = native(*args, **kwargs)
        child.wait(timeout=10)
        calls.append(child)
        streams.extend([kwargs['stdout'], kwargs['stderr'], kwargs['stdin']])
        return child

    monkeypatch.setattr(subprocess, 'Popen', completed)
    code = ('import pathlib,sys;pathlib.Path(' + repr(str(marker)) + ').write_text("effect completed");'
            'sys.stdout.buffer.write(b"o"*' + str(stdout_bytes) + ');'
            'sys.stderr.buffer.write(b"private-diagnostic-sentinel"+b"d"*' + str(stderr_bytes-27) + ')')
    process = Process(tmp_path, lambda event, detail: events.append((event, detail)))
    if refused:
        with pytest.raises(Refused, match='output exceeded'):
            process.run([sys.executable, '-c', code], tmp_path, capture=True)
        terminal = next(detail for event, detail in events if event == 'command.failed')
        assert terminal['exit_code'] == 0 and terminal['outcome'] == 'failed'
        assert not any(event == 'command.finished' for event, _ in events)
    else:
        assert process.run([sys.executable, '-c', code], tmp_path, capture=True) == 'o'*stdout_bytes
        assert any(event == 'command.finished' for event, _ in events)
    assert marker.read_text() == 'effect completed' and len(calls) == 1
    assert 'private-diagnostic-sentinel' not in json.dumps(events)
    assert events[-1][0] == 'command.stopped' and all(stream.closed for stream in streams)


# Byte accounting must not charge UTF-8 replacement encoding as additional child output
# Captured machine text keeps the existing replacement decoding without emitting its body
def test_command_byte_limit_precedes_replacement_decoding(tmp_path, monkeypatch):
    import deploy_ops.delivery.process as module
    monkeypatch.setattr(module, 'MAX_OUTPUT_BYTES', 64)
    events = []
    result = Process(tmp_path, lambda event, detail: events.append((event, detail))).run(
        [sys.executable, '-c', 'import sys;sys.stdout.buffer.write(bytes([255])*32);sys.stderr.buffer.write(b"d"*16)'],
        tmp_path, capture=True)
    assert result == '\ufffd'*32
    assert not any(event == 'command.output' for event, _ in events)
    assert any(event == 'command.finished' for event, _ in events)
