# delivery/operator.py - Expose local controller decisions without creating execution authority.


import json
import re

from pydantic import ValidationError

from ..errors import Refused
from ..models import Decision, Resolution
from .gate import LocalGate


# Select only the workspace's existing local authority owner
class Operator:
    # Remote and isolated registrations require their own explicit operator boundary
    def __init__(self, engine):
        self.engine = engine
        self.isolated = getattr(engine.gate, 'route', None)
        self.controller = engine.gate.controller if isinstance(engine.gate, LocalGate) else None

    # Report availability without discovering credentials or contacting remote services
    def status(self):
        if self.controller is None:
            return {"available": False, "mode": "unavailable", "reason": "Local controller operator access is unavailable in this mode"}
        with self.controller.store.connect() as connection:
            paused = self.controller.store.paused(connection)
        return {"available": True, "mode": "isolated-operator" if self.isolated else "local-operator", "paused": paused,
                "resolution_outcomes": ['failed'] if self.isolated else ['failed', 'succeeded', 'cancelled']}

    # Share fixed operations across CLI and authenticated console routes
    def call(self, action, identifier="", values=None):
        if action == "status":
            return self.status()
        controller = self.controller
        if controller is None:
            raise Refused("Local controller operator access is unavailable in this mode", 403)
        if action in {"request", "decision", "resolve", "revoke"} and not re.fullmatch(r"[0-9a-f]{32}", identifier):
            raise Refused("Choose an existing request or delegation ID", 422)
        values = values or {}
        try:
            if action == "requests":
                active = controller.active_requests()
                ids = {item['id'] for item in active}
                return [{**self.request(item), 'history_scope': 'active'} for item in active] + [
                    {**self.request(item), 'history_scope': 'recent'} for item in controller.list_requests() if item['id'] not in ids]
            if action == "request":
                return self.request(controller.get(identifier))
            if action == "decision":
                decision = Decision.model_validate(values)
                return self.request(self.isolated.decide(identifier, decision) if self.isolated else
                                    controller.decide(identifier, decision, "local-operator"))
            if action == "resolve":
                resolution = Resolution.model_validate(values)
                if self.isolated:
                    if resolution.outcome != 'failed':
                        raise Refused('Isolated inspection can record failure only; it cannot mint successful outputs', 422)
                    request = controller.get(identifier)
                    if request['manifest']['target'] in {self.isolated.plan_target, self.isolated.apply_target}:
                        return self.request(self.isolated.reconcile(identifier, request['digest'], resolution.note))
                return self.request(self.resolve(identifier, resolution))
        except ValidationError:
            raise Refused("Check the decision digest, outcome and inspection note", 422) from None
        if values:
            raise Refused("This operation accepts no extra values", 422)
        if action in {"pause", "resume"}:
            return controller.pause(action == "pause", "local-operator")
        if action == "revoke":
            controller.revoke(identifier, "local-operator")
            return {"id": identifier, "active": False}
        if action == "delegations":
            with controller.store.connect() as connection:
                rows = connection.execute("SELECT data FROM delegations ORDER BY rowid DESC")
                return [{key: grant[key] for key in ("id", "target", "active", "expires_at", "max_uses", "uses")}
                        for grant in (json.loads(row[0]) for row in rows)]
        if action == "audit":
            return controller.store.verify()
        raise Refused("Unknown operator operation", 404)

    # Every local delivery resolution honors the same release worker guard as Engine recovery
    def resolve(self, identifier, resolution):
        with self.engine.ledger.store.connect() as connection:
            rows = connection.execute('SELECT id,data FROM releases')
            owner = next(((row['id'], 'cleanup' if json.loads(row['data']).get('cleanup', {}).get('request_id') == identifier else 'worker')
                         for row in rows if any(node.get('request_id') == identifier
                         for node in json.loads(row['data'])['nodes'].values()) or
                         json.loads(row['data']).get('cleanup', {}).get('request_id') == identifier), None)
        if not owner:
            raise Refused('Retained worker ownership is unavailable; inspect through the original operation owner')
        owner, kind = owner
        scope = 'release:' + owner
        self.engine.ledger.recovery_lock(scope, owner, kind=kind)
        try:
            return self.controller.resolve(identifier, resolution, 'local-operator')
        finally:
            self.engine.ledger.unlock(scope, owner)

    # Return normalized review evidence while withholding execution state and credential references
    @staticmethod
    def request(record):
        manifest = record["manifest"]
        keys = ("target", "cloud", "engine", "artifact_digest", "preview_digest", "preview", "tool_version",
                "lock_digest", "source", "schema_version", "caller", "agent", "estimated_monthly_delta_cents", "policy_digest")
        return {key: record[key] for key in ("id", "digest", "status", "reason", "created_at", "expires_at")} | {
            "manifest": {key: manifest[key] for key in keys if key in manifest},
            "timing": record.get("timing")}
