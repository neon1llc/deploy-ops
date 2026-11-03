# src/deploy_ops/slack.py - Accept signed phone approvals without trusting message text as identity.

import hashlib
import hmac
import json
from urllib.parse import parse_qs

import httpx

from .config import SlackConfig
from .errors import Refused
from .models import Decision, DelegationSpec
from .service import Controller


def verify_signature(body: bytes, timestamp: str, signature: str, secret: str, now: int) -> str:
    """Check the raw body and a five-minute window before parsing the interaction."""
    try:
        if not secret or abs(now - int(timestamp)) > 300:
            raise ValueError("Stale request")
        message = b"v0:" + timestamp.encode() + b":" + body
        expected = "v0=" + hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise ValueError("Signature mismatch")
    except (ValueError, TypeError) as error:
        raise Refused("Invalid Slack signature or timestamp", 401) from error
    return hashlib.sha256(message).hexdigest()


def card(request: dict) -> dict:
    """Use plain text fields so repository labels cannot inject mentions or links."""
    manifest = request["manifest"]
    source = manifest["source"]
    changes = manifest["preview"]["changes"]
    cost = manifest["estimated_monthly_delta_cents"]
    estimate = "Unknown" if cost is None else f"${cost / 100:.2f}/month estimated change"
    lines = [
        f"{manifest['target']} | {request['status']}",
        f"{source['repository']} @ {source['sha'][:12]}",
        f"Initiator: {source['actor']} (GitHub ID {source['actor_id']})",
        f"{len(changes)} resource entries | {estimate}",
        f"Reason: {request['reason']}",
        f"Request: {request['id']}",
        f"Digest: {request['digest']}",
    ]
    if manifest["agent"]["label"]:
        lines.append(f"Agent label (self-reported): {manifest['agent']['label']}")
    blocks = [{"type": "section", "text": {"type": "plain_text", "text": "\n".join(lines)[:2900]}}]
    if changes:
        detail = "\n".join(f"{item['action']}: {item['address']}" for item in changes[:20])
        blocks.append({"type": "section", "text": {"type": "plain_text", "text": detail[:2900]}})
    if request["status"] == "pending":
        value = request["id"] + ":" + request["digest"]
        blocks.append({"type": "actions", "elements": [
            {"type": "button", "text": {"type": "plain_text", "text": "Approve this plan"},
             "action_id": "approve", "value": value, "style": "primary"},
            {"type": "button", "text": {"type": "plain_text", "text": "Reject"},
             "action_id": "reject", "value": value, "style": "danger"},
        ]})
    return {"text": "DeployOps deployment review", "blocks": blocks}


class Slack:
    """Keep workspace and approver membership checks on the controller."""

    def __init__(self, controller: Controller, settings: SlackConfig, signing_secret: str, bot_token: str):
        self.controller = controller
        self.settings = settings
        self.signing_secret = signing_secret
        self.bot_token = bot_token

    def _actor(self, team: str, app: str, user: str) -> tuple[str, str]:
        if not self.settings.team_id or team != self.settings.team_id or app != self.settings.app_id:
            raise Refused("Slack workspace or app is not authorized", 403)
        github_id = self.settings.approvers.get(user)
        if not github_id:
            raise Refused("Slack member is not an approver", 403)
        return f"slack:{team}:{user}", github_id

    def handle(self, body: bytes, timestamp: str, signature: str) -> dict:
        delivery = verify_signature(body, timestamp, signature, self.signing_secret, self.controller.now())
        try:
            fields = parse_qs(body.decode(), strict_parsing=True, max_num_fields=50)
            if any(len(value) != 1 for value in fields.values()):
                raise ValueError("Repeated form fields")
            form = {key: value[0] for key, value in fields.items()}
            if "payload" in form:
                payload = json.loads(form["payload"])
                actor, github_id = self._actor(payload["team"]["id"], payload["api_app_id"], payload["user"]["id"])
                if payload.get("type") != "block_actions" or len(payload["actions"]) != 1:
                    raise ValueError("Unsupported interaction")
                action = payload["actions"][0]
                request_id, digest = action["value"].split(":", 1)
                request = self.controller.decide(
                    request_id, Decision(digest=digest, decision=action["action_id"]), actor, github_id,
                )
                return {"response_type": "ephemeral", **card(request)}
            actor, github_id = self._actor(form["team_id"], form["api_app_id"], form["user_id"])
            if form.get("command") != "/deploy-ops":
                raise ValueError("Unexpected command")
            words = form.get("text", "").split()
            command = words[0] if words else "help"
            if command == "show" and len(words) == 2:
                return {"response_type": "ephemeral", **card(self.controller.get(words[1]))}
            if command == "pending" and len(words) == 1:
                requests = [item for item in self.controller.list_requests() if item["status"] == "pending"]
                text = "\n".join(
                    f"{item['id']}  {item['manifest']['target']}" for item in requests[:20]
                ) or "No pending requests"
            elif command == "delegate" and len(words) == 4:
                grant = self.controller.delegate(
                    DelegationSpec(target=words[1], hours=int(words[2]), uses=int(words[3])), actor, delivery,
                )
                text = f"Delegation {grant['id']} enabled. It expires at Unix time {grant['expires_at']}"
            elif command == "revoke" and len(words) == 2:
                self.controller.revoke(words[1], actor)
                text = "Delegation revoked. In-flight cloud operations still need inspection"
            elif command in ("stop", "resume") and len(words) == 1:
                self.controller.pause(command == "stop", actor, delivery)
                text = (
                    "Controller paused. Inspect active cloud operations"
                    if command == "stop" else "Controller resumed. Old grants stay revoked"
                )
            elif command == "help":
                text = "Commands: pending | show REQUEST | delegate TARGET HOURS USES | revoke GRANT | stop | resume"
            else:
                raise ValueError("Unknown command or incorrect argument count")
            return {"response_type": "ephemeral", "text": text}
        except (KeyError, ValueError, TypeError, IndexError) as error:
            raise Refused("Invalid Slack command or interaction", 422) from error

    def notify(self, request: dict) -> None:
        if not self.bot_token or not self.settings.channel_id:
            return
        try:
            with httpx.Client(timeout=5, follow_redirects=False) as client:
                response = client.post(
                    "https://slack.com/api/chat.postMessage",
                    headers={"Authorization": "Bearer " + self.bot_token},
                    json={"channel": self.settings.channel_id, **card(request)},
                )
                response.raise_for_status()
                if not response.json().get("ok"):
                    raise ValueError("Delivery rejected")
        except (httpx.HTTPError, ValueError):
            # Delivery failure never turns into approval and never logs the bot credential
            with self.controller.store.transaction() as connection:
                self.controller.store.event(connection, self.controller.now(), "notifier",
                                            "notification.failed", request["id"], {})
