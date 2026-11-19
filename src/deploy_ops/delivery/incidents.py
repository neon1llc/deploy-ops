# src/deploy_ops/delivery/incidents.py - Track local incident decisions atomically with audit and notification events.


import json
import re
import time
import uuid

from ..config import canonical, fingerprint
from ..errors import Refused


# Normalize bounded operator text before writing incident state
def _text(value, label):
    if not isinstance(value, str) or not value.strip() or len(value) > 500:
        raise Refused(f"{label} must contain 1 to 500 characters", 422)
    return value.strip()


# Read the current incident inside the caller's transaction
def _record(connection, incident_id):
    row = connection.execute("SELECT data FROM incidents WHERE id=?", (incident_id,)).fetchone()
    if row is None:
        raise Refused("Incident not found", 404)
    return json.loads(row[0])


# Return durable incident history without sending notifications
def list_incidents(ledger):
    with ledger.store.connect() as connection:
        return [json.loads(row[0]) for row in connection.execute(
            "SELECT data FROM incidents ORDER BY rowid DESC")]


# Commit the decision, audit event and transport envelope together
def _save(ledger, connection, incident, event, note, now):
    connection.execute(
        "INSERT INTO incidents(id,release_id,status,data) VALUES(?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET status=excluded.status,data=excluded.data",
        (incident["id"], incident["release_id"], incident["status"], canonical(incident)))
    # Notes stay in the local audit. Transport receives only the bounded incident envelope
    detail = {"incident_id": incident["id"], "version": incident["version"], "status": incident["status"],
              "owner": incident["owner"], "level": incident["level"], "actor": "local-operator", "note": note}
    ledger._event(connection, incident["release_id"], event, detail)
    payload = {key: value for key, value in incident.items() if key != "id"}
    payload.update(incident_id=incident["id"], event=event, time=now, actor="local-operator")
    connection.execute("INSERT INTO notifications(payload) VALUES (?)", (canonical(payload),))
    return incident


# Open from either a failed release or an exact saved adverse observation, without rewriting delivery history
def open_incident(ledger, release_id, owner, note, interval_seconds=900, limit=3,
                  observation_sequence=None, observation_digest=""):
    owner, note = _text(owner, "Owner"), _text(note, "Incident note")
    if type(interval_seconds) is not int or not 60 <= interval_seconds <= 86400:
        raise Refused("Escalation interval must be 60 to 86400 seconds", 422)
    if type(limit) is not int or not 1 <= limit <= 5:
        raise Refused("Escalation limit must be 1 to 5", 422)
    if observation_sequence is not None or observation_digest:
        if (type(observation_sequence) is not int or observation_sequence < 1
                or not isinstance(observation_digest, str) or not re.fullmatch(r'[0-9a-f]{64}', observation_digest)):
            raise Refused('Provide the exact saved observation sequence and digest', 422)
    now = int(time.time())
    with ledger.store.transaction() as connection:
        release = connection.execute('SELECT status FROM releases WHERE id=?', (release_id,)).fetchone()
        if release is None:
            raise Refused('Release not found', 404)
        origin = None
        if observation_sequence is not None:
            row = connection.execute("SELECT detail FROM release_events WHERE sequence=? AND release_id=? AND name='release.observed'",
                                     (observation_sequence, release_id)).fetchone()
            if row is None:
                raise Refused('Saved observation is unavailable for this release', 409)
            observation = json.loads(row[0])
            if (fingerprint(observation) != observation_digest or observation.get('release_id') != release_id
                    or observation.get('status') not in {'drifted', 'unknown'}
                    or release[0] not in {'succeeded', 'failed', 'uncertain'}):
                raise Refused('The saved observation does not support this incident reference', 409)
            origin = {'kind': 'observation', 'sequence': observation_sequence, 'digest': observation_digest,
                      'status': observation['status'], 'observed_at': observation['observed_at'],
                      'scope': observation['scope'], 'components': sorted(name for name, value in observation['components'].items()
                                                                    if value['status'] in {'drifted', 'unknown', 'unsupported'})}
        existing = connection.execute('SELECT data FROM incidents WHERE release_id=?', (release_id,)).fetchone()
        if existing:
            incident = json.loads(existing[0])
            if (incident['owner'], incident['interval_seconds'], incident['limit']) != (owner, interval_seconds, limit):
                raise Refused('An incident already exists for this release with a different owner or escalation policy')
            if incident.get('origin') != origin:
                raise Refused('An incident already exists with another source. Preserve its history; multiple episodes per release are not supported')
            return incident
        if origin is None and release[0] not in {'failed', 'uncertain'}:
            raise Refused('Open an incident for a failed or uncertain release, or an exact saved adverse observation', 422)
        incident = {'id': uuid.uuid4().hex, 'release_id': release_id, 'owner': owner, 'status': 'open',
                    'version': 1, 'level': 0, 'interval_seconds': interval_seconds, 'limit': limit,
                    'created_at': now, 'updated_at': now, 'next_escalation_at': now + interval_seconds}
        if origin:
            incident.update(origin=origin, native_handoff=False)
        return _save(ledger, connection, incident, 'incident.opened', note, now)


# Reject stale decisions before acknowledging or resolving an incident
def transition(ledger, incident_id, action, version, note):
    if action not in {"acknowledge", "resolve"}:
        raise Refused("Choose acknowledge or resolve", 422)
    if type(version) is not int or version < 1:
        raise Refused("Supply the displayed incident version", 422)
    note = _text(note, "Decision note")
    now = int(time.time())
    with ledger.store.transaction() as connection:
        incident = _record(connection, incident_id)
        if incident["version"] != version:
            raise Refused("Incident changed. Review its current state before acting")
        target = "acknowledged" if action == "acknowledge" else "resolved"
        if incident["status"] == target:
            return incident
        if incident["status"] == "resolved":
            raise Refused("A resolved incident cannot be reopened or acknowledged")
        incident.update(status=target, version=version + 1, updated_at=now, next_escalation_at=None)
        return _save(ledger, connection, incident, "incident." + target, note, now)


# Advance only the next due escalation under the same transaction
def tick(ledger):
    """Queue at most one due escalation per incident; a stopped scheduler cannot create a catch-up burst."""
    now = int(time.time())
    changed = []
    with ledger.store.transaction() as connection:
        rows = connection.execute("SELECT data FROM incidents WHERE status='open'").fetchall()
        for row in rows:
            incident = json.loads(row[0])
            due = incident["next_escalation_at"]
            if due is None or due > now or incident["level"] >= incident["limit"]:
                continue
            incident.update(level=incident["level"] + 1, version=incident["version"] + 1, updated_at=now)
            incident["next_escalation_at"] = now + incident["interval_seconds"] if incident["level"] < incident["limit"] else None
            changed.append(_save(ledger, connection, incident, "incident.escalated", "Explicit escalation check", now))
    return {"checked_at": now, "escalated": changed, "delivery": "queued; run notify separately"}


# Drop queued versions superseded by a later operator decision
def current_notification(connection, payload):
    """Suppress obsolete queued signals; receivers must still order deliveries already in flight."""
    if "incident_id" not in payload:
        return True
    incident = _record(connection, payload["incident_id"])
    return incident["version"] == payload.get("version")


# Review the exact current route without adopting a previously suppressed signal or contacting a responder
def _handoff_review(connection, incident, now):
    from .paging import _configuration, _route, routing_context
    current = _configuration(connection)
    row = connection.execute('SELECT data FROM releases WHERE id=?', (incident['release_id'],)).fetchone()
    if row is None:
        raise Refused('Incident release evidence is unavailable', 409)
    release = json.loads(row[0])
    origin = incident.get('origin')
    if origin:
        saved = connection.execute("SELECT detail FROM release_events WHERE sequence=? AND release_id=? AND name='release.observed'",
                                   (origin['sequence'], incident['release_id'])).fetchone()
        if saved is None or fingerprint(json.loads(saved[0])) != origin['digest']:
            raise Refused('The referenced incident observation is unavailable or changed', 409)
    context = {**routing_context(release, 'incident.handoff_requested', incident),
               'release_id': incident['release_id'], 'incident_id': incident['id'], 'version': incident['version'],
               'native_handoff': True}
    decision = _route(current['active'], context, now)
    digest = fingerprint({'incident_id': incident['id'], 'version': incident['version'],
                          'active_revision': current['active_revision'], 'configuration_digest': fingerprint(current['active']),
                          'context': context, 'decision': decision})
    bindings = connection.execute('SELECT 1 FROM notification_bindings WHERE incident_id=? LIMIT 1', (incident['id'],)).fetchone()
    reason = ('Incident is not open' if incident['status'] != 'open' else
              'A handoff was already requested; inspect its delivery evidence' if incident.get('handoff_requested_at') else
              'Existing native bindings require their own explicit actions; another handoff could duplicate a page' if bindings else
              'No destinations are eligible under the active route now' if not decision['selected'] else '')
    history = []
    for row in connection.execute(
            "SELECT revision,context,decision FROM notification_routes WHERE json_extract(context,'$.incident_id')=? ORDER BY rowid DESC LIMIT 5",
            (incident['id'],)):
        payload = json.loads(row['context'])
        if payload.get('incident_id') == incident['id']:
            history.append({'revision': row['revision'], 'event': payload['event'], 'version': payload.get('version'),
                            'time': payload.get('time'), 'decision': json.loads(row['decision'])})
            if len(history) == 5:
                break
    return {'incident_id': incident['id'], 'incident_version': incident['version'], 'origin': origin,
            'source': 'active', 'revision': current['active_revision'], 'configuration_digest': fingerprint(current['active']),
            'observed_at': now, 'context': context, 'decision': decision, 'route_digest': digest,
            'available': not reason, 'reason': reason, 'prior_routes': history,
            'risk': 'Handoff queues new work. An earlier notification may already have reached someone; unknown remote outcomes require explicit inspection. No automatic recall, remote resolve or deployment action.'}


# Keep preview outside the mutation path while binding every prospective handoff to a concrete digest
def handoff_preview(ledger, incident_id):
    with ledger.store.connect() as connection:
        return _handoff_review(connection, _record(connection, incident_id), int(time.time()))


# Commit one deliberate handoff only while incident, route, quiet state and existing bindings still match
def handoff(ledger, incident_id, version, route_digest, note):
    note = _text(note, 'Handoff note')
    if type(version) is not int or version < 1 or not isinstance(route_digest, str) or not re.fullmatch(r'[0-9a-f]{64}', route_digest):
        raise Refused('Review the exact incident version and route digest before handoff', 422)
    now = int(time.time())
    with ledger.store.transaction() as connection:
        incident = _record(connection, incident_id)
        preview = _handoff_review(connection, incident, now)
        if incident['version'] != version or preview['route_digest'] != route_digest:
            raise Refused('Incident or effective route changed. Preview the handoff again', 409)
        if not preview['available']:
            raise Refused(preview['reason'], 409)
        incident.update(version=version + 1, updated_at=now, native_handoff=True,
                        handoff_requested_at=now, handoff_route_digest=route_digest)
        return _save(ledger, connection, incident, 'incident.handoff_requested', note, now)
