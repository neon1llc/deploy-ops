# tests/test_incidents.py - Exercise durable operator incidents and ordered notification behavior.


import concurrent.futures
import json
import threading

import httpx
import pytest
from fastapi.testclient import TestClient

from deploy_ops.cli import dispatch, parser
from deploy_ops.console import create_app
from deploy_ops.delivery.engine import Engine
from deploy_ops.delivery.incidents import list_incidents, open_incident, tick, transition
from deploy_ops.delivery.notify import flush
from deploy_ops.delivery.scaffold import create
from deploy_ops.errors import Refused


# Create one uncertain local release under a controllable incident clock
@pytest.fixture
def incident_system(tmp_path, monkeypatch):
    engine = Engine(create(tmp_path / "workspace"))
    release = engine.create("development")
    release["status"] = "uncertain"
    engine.ledger.save(release)
    clock = [1800000000]
    monkeypatch.setattr("deploy_ops.delivery.incidents.time.time", lambda: clock[0])
    return engine, release, clock


# Reuse one fixed incident policy across concurrency checks
def opened(system):
    engine, release, _ = system
    return open_incident(engine.ledger, release["id"], "operations", "Inspect stopped worker", 60, 2)


# Restart preserves incident versions and keeps deployment outcome independent
def test_incident_durable_lifecycle_and_stale_decisions(incident_system):
    engine, release, clock = incident_system
    incident = opened(incident_system)
    restarted = Engine(engine.path)
    assert list_incidents(restarted.ledger) == [incident]
    assert opened(incident_system) == incident
    clock[0] += 600
    escalation = tick(restarted.ledger)["escalated"][0]
    assert escalation["level"] == 1
    assert tick(engine.ledger)["escalated"] == []
    with pytest.raises(Refused, match="changed"):
        transition(engine.ledger, incident["id"], "acknowledge", 1, "Stale view")
    acknowledged = transition(engine.ledger, incident["id"], "acknowledge", 2, "Investigating")
    clock[0] += 600
    assert tick(engine.ledger)["escalated"] == []
    resolved = transition(engine.ledger, incident["id"], "resolve", acknowledged["version"], "Checked target and fixed cause")
    assert resolved["status"] == "resolved" and resolved["next_escalation_at"] is None
    assert engine.ledger.get(release["id"])["status"] == "uncertain"
    with pytest.raises(Refused, match="resolved"):
        transition(engine.ledger, incident["id"], "acknowledge", resolved["version"], "Reopen")
    # Normal release writes cannot overwrite the separate incident record
    engine.ledger.save(release)
    assert list_incidents(engine.ledger) == [resolved]
    assert [event["name"] for event in engine.ledger.events(release["id"])][-4:] == [
        "incident.opened", "incident.escalated", "incident.acknowledged", "incident.resolved"]


# Concurrent schedulers cannot duplicate incidents or exceed escalation limits
def test_concurrent_open_and_tick_are_bounded(incident_system):
    engine, _, clock = incident_system
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        records = list(pool.map(lambda _: opened(incident_system), range(4)))
    assert len({record["id"] for record in records}) == 1
    for _ in range(3):
        clock[0] += 600
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda _: tick(engine.ledger), range(4)))
    incident = list_incidents(engine.ledger)[0]
    assert incident["level"] == incident["limit"] == 2
    assert incident["version"] == 3 and incident["next_escalation_at"] is None


# Invalid operator settings must leave no incident record
@pytest.mark.parametrize("owner,interval,limit", [("",60,2),("ops",0,2),("ops",60,6),("ops",True,2)])
def test_invalid_incident_policy_is_nonmutating(incident_system, owner, interval, limit):
    engine, release, _ = incident_system
    with pytest.raises(Refused):
        open_incident(engine.ledger, release["id"], owner, "Inspect", interval, limit)
    assert list_incidents(engine.ledger) == []


# Only failed work can open an incident with a fixed initial policy
def test_release_eligibility_and_immutable_incident_policy(incident_system):
    engine, release, _ = incident_system
    queued = engine.create("staging")
    with pytest.raises(Refused, match="failed or uncertain"):
        open_incident(engine.ledger, queued["id"], "ops", "Inspect")
    incident = opened(incident_system)
    with pytest.raises(Refused, match="different owner"):
        open_incident(engine.ledger, release["id"], "other", "Inspect", 60, 2)
    assert transition(engine.ledger, incident["id"], "resolve", 1, "Already fixed")["status"] == "resolved"


# Deliver only the latest incident version and keep private notes local
def test_notify_only_current_snapshot_without_private_notes(incident_system):
    engine, _, clock = incident_system
    incident = opened(incident_system)
    clock[0] += 61
    tick(engine.ledger)
    transition(engine.ledger, incident["id"], "resolve", 2, "PRIVATE-NOTE")
    received = []


    # Capture the actual transport body for privacy assertions
    def receive(request):
        received.append(json.loads(request.content))
        return httpx.Response(204)
    with httpx.Client(transport=httpx.MockTransport(receive)) as client:
        result = flush(engine.ledger, "https://receiver.example/events", client=client)
    assert result["superseded"] == 2 and result["sent"] == 1
    assert received[0]["status"] == "resolved" and received[0]["version"] == 3
    assert received[0]["interval_seconds"] == 60 and received[0]["limit"] == 2
    assert "PRIVATE-NOTE" not in json.dumps(received)
    assert list_incidents(engine.ledger)[0]["status"] == "resolved"


# A transport success is not a responder decision
def test_notification_delivery_does_not_acknowledge(incident_system):
    engine, _, _ = incident_system
    opened(incident_system)
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200))) as client:
        assert flush(engine.ledger, "https://receiver.example/", client=client)["sent"] == 1
    assert list_incidents(engine.ledger)[0]["status"] == "open"


# A late failed attempt must not erase an earlier delivery success
def test_concurrent_notification_failure_cannot_undo_success(incident_system):
    engine, _, _ = incident_system
    opened(incident_system)
    barrier = threading.Barrier(2)
    success_done = threading.Event()


    # Race two flushes while controlling response order
    def send(ok):

        # Release the failure only after the successful flush is recorded
        def handle(_):
            barrier.wait(timeout=5)
            if not ok:
                assert success_done.wait(5)
            return httpx.Response(204 if ok else 503)
        with httpx.Client(transport=httpx.MockTransport(handle)) as client:
            result = flush(engine.ledger, "https://receiver.example/", client=client)
        if ok:
            success_done.set()
        return result
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(send, [True, False]))
    with engine.ledger.store.connect() as connection:
        row = connection.execute("SELECT sent,attempts FROM notifications").fetchone()
    assert row["sent"] == 1 and row["attempts"] == 2
    assert sum(result["sent"] for result in results) == 1


# Reserve attempts atomically before concurrent network calls
def test_concurrent_notification_attempt_limit(incident_system):
    engine, _, _ = incident_system
    opened(incident_system)
    with engine.ledger.store.transaction() as connection:
        connection.execute("UPDATE notifications SET attempts=9")
    attempts = []


    # Give each worker its own mocked transport
    def send(_):

        # Count actual attempts at the transport boundary
        def handle(request):
            attempts.append(request)
            return httpx.Response(503)
        with httpx.Client(transport=httpx.MockTransport(handle)) as client:
            return flush(engine.ledger, "https://receiver.example/", client=client)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(send, range(4)))
    assert len(attempts) == 1


# CLI and authenticated console decisions must update one ledger
def test_cli_console_use_same_incident_state(incident_system):
    engine, release, _ = incident_system
    args = parser().parse_args(['--project', str(engine.path), 'incidents', 'open', release['id'],
                                '--owner', 'operations', '--note', 'Inspect target'])
    incident = dispatch(args)
    client = TestClient(create_app(engine, 'x' * 32), base_url='http://127.0.0.1:8765')
    assert client.get('/api/incidents').status_code == 401
    headers = {'Authorization': 'Bearer ' + 'x' * 32}
    assert client.get('/api/incidents', headers=headers).json() == [incident]
    body = {'version': incident['version'], 'note': 'Investigating'}
    response = client.post('/api/incidents/' + incident['id'] + '/acknowledge', headers=headers, json=body)
    assert response.status_code == 200 and response.json()['status'] == 'acknowledged'
    assert dispatch(parser().parse_args(['--project',str(engine.path),'incidents','list']))[0]['status'] == 'acknowledged'
    assert client.post('/api/incidents/' + incident['id'] + '/resolve', headers=headers, json=body).status_code == 409
    assert client.post('/api/incidents/tick', headers=headers, json={}).json()['escalated'] == []


# Only one decision can consume a given incident version
def test_acknowledgement_racing_tick_preserves_newer_decision(incident_system):
    engine, _, clock = incident_system
    incident = opened(incident_system)
    clock[0] += 61
    barrier = threading.Barrier(2)


    # Try the displayed version at the same time as escalation
    def acknowledge():
        barrier.wait(timeout=5)
        try:
            return transition(engine.ledger, incident["id"], "acknowledge", 1, "Responding")
        except Refused:
            return None


    # Make the next escalation compete for the incident transaction
    def escalate():
        barrier.wait(timeout=5)
        return tick(engine.ledger)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        ack = pool.submit(acknowledge)
        checked = pool.submit(escalate)
        response, result = ack.result(), checked.result()
    current = list_incidents(engine.ledger)[0]
    assert current["version"] == 2
    if response:
        assert current["status"] == "acknowledged" and result["escalated"] == []
    else:
        assert current["status"] == "open" and len(result["escalated"]) == 1
        current = transition(engine.ledger, incident["id"], "acknowledge", 2, "Reviewed new escalation")
    clock[0] += 600
    assert tick(engine.ledger)["escalated"] == []


# A delayed older delivery must not regress receiver state
def test_inflight_old_signal_requires_receiver_version_ordering(incident_system):
    engine, _, _ = incident_system
    incident = opened(incident_system)
    started, finish_old = threading.Event(), threading.Event()
    receiver = {}
    arrival_versions = []


    # Apply only a newer version while forcing reversed arrival order
    def receive(request):
        snapshot = json.loads(request.content)
        if snapshot["version"] == 1:
            started.set()
            assert finish_old.wait(5)
        arrival_versions.append(snapshot["version"])
        if snapshot["version"] > receiver.get("version", 0):
            receiver.update(snapshot)
        return httpx.Response(204)


    # Flush through the receiver that enforces version ordering
    def send():
        with httpx.Client(transport=httpx.MockTransport(receive)) as client:
            return flush(engine.ledger, "https://receiver.example/", client=client)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        old = pool.submit(send)
        assert started.wait(5)
        transition(engine.ledger, incident["id"], "resolve", 1, "Target checked")
        send()
        finish_old.set()
        old.result()
    assert arrival_versions == [2, 1]
    assert receiver["status"] == "resolved" and receiver["version"] == 2
    from deploy_ops.delivery.notify import status
    assert status(engine.ledger)["pending"] == 0
