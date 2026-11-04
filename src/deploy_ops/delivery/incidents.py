# src/deploy_ops/delivery/incidents.py - Track local incident decisions atomically with audit and notification events.


import json
import time
import uuid

from ..config import canonical
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


# Create one incident per failed release with a fixed escalation policy
def open_incident(ledger, release_id, owner, note, interval_seconds=900, limit=3):
    owner, note = _text(owner, "Owner"), _text(note, "Incident note")
    if type(interval_seconds) is not int or not 60 <= interval_seconds <= 86400:
        raise Refused("Escalation interval must be 60 to 86400 seconds", 422)
    if type(limit) is not int or not 1 <= limit <= 5:
        raise Refused("Escalation limit must be 1 to 5", 422)
    now = int(time.time())
    with ledger.store.transaction() as connection:
        existing = connection.execute("SELECT data FROM incidents WHERE release_id=?", (release_id,)).fetchone()
        if existing:
            incident = json.loads(existing[0])
            if (incident["owner"], incident["interval_seconds"], incident["limit"]) != (owner, interval_seconds, limit):
                raise Refused("An incident already exists for this release with a different owner or escalation policy")
            return incident
        row = connection.execute("SELECT status FROM releases WHERE id=?", (release_id,)).fetchone()
        if row is None or row[0] not in {"failed", "uncertain"}:
            raise Refused("Open an incident for a failed or uncertain release", 422)
        incident = {"id": uuid.uuid4().hex, "release_id": release_id, "owner": owner, "status": "open",
                    "version": 1, "level": 0, "interval_seconds": interval_seconds, "limit": limit,
                    "created_at": now, "updated_at": now, "next_escalation_at": now + interval_seconds}
        return _save(ledger, connection, incident, "incident.opened", note, now)


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
