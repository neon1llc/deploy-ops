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
        self.controller = engine.gate.controller if isinstance(engine.gate, LocalGate) else None

    # Report availability without discovering credentials or contacting remote services
    def status(self):
        if self.controller is None:
            return {"available": False, "mode": "unavailable", "reason": "Local controller operator access is unavailable in this mode"}
        with self.controller.store.connect() as connection:
            paused = self.controller.store.paused(connection)
        return {"available": True, "mode": "local-operator", "paused": paused}

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
                return [self.request(item) for item in controller.list_requests()]
            if action == "request":
                return self.request(controller.get(identifier))
            if action == "decision":
                return self.request(controller.decide(identifier, Decision.model_validate(values), "local-operator"))
            if action == "resolve":
                return self.request(controller.resolve(identifier, Resolution.model_validate(values), "local-operator"))
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

    # Return normalized review evidence while withholding execution state and credential references
    @staticmethod
    def request(record):
        manifest = record["manifest"]
        keys = ("target", "cloud", "engine", "artifact_digest", "preview_digest", "preview", "tool_version",
                "lock_digest", "source", "agent", "estimated_monthly_delta_cents", "policy_digest")
        return {key: record[key] for key in ("id", "digest", "status", "reason", "created_at", "expires_at")} | {
            "manifest": {key: manifest[key] for key in keys if key in manifest}}
