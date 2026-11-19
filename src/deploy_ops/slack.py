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


# Authenticate raw bytes before trusting any interaction fields
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


# Preserve all normalized review text, including types and findings, without interpreting labels as markup
def review_text(request: dict) -> str:
    manifest, preview = request['manifest'], request['manifest']['preview']
    quoted = lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True)
    lines = [
        'Scope: complete normalized controller preview and public bindings. Native plan/artifact bytes are referenced, not embedded.',
        'Target: ' + quoted(manifest['target']),
        'Status: ' + request['status'],
        'Reason: ' + quoted(request['reason']),
        'Manual-only review: ' + ('yes' if preview.get('manual_only') else 'no'),
    ]
    # The manifest is the existing public contract; dedicated sections cover the remaining fields
    # Include its public generated planning packet instead of maintaining a second field allowlist
    separate = {'target', 'source', 'preview', 'estimated_monthly_delta_cents', 'caller', 'agent'}
    for key in sorted(set(manifest) - separate):
        lines.append(key + ': ' + quoted(manifest[key]))
    cost = manifest.get('estimated_monthly_delta_cents')
    lines.append('Estimated monthly delta: ' + ('unknown' if cost is None else f'${cost / 100:.2f}; estimate, not observed cost'))
    lines.append('Source claims: ' + quoted(manifest['source']))
    lines.append('Caller (self-reported, not authority): ' + quoted(manifest.get('caller', manifest.get('agent', {}))))
    lines.append('Request expires at Unix time: ' + quoted(request.get('expires_at')))
    if request.get('execution_blocker'):
        lines.append('Execution unavailable: ' + quoted(request['execution_blocker']))
    lines.append('Findings (' + str(len(preview.get('findings', []))) + '):')
    lines.extend(str(index) + '. ' + quoted(finding) for index, finding in enumerate(preview.get('findings', []), 1))
    lines.append('Changes (' + str(len(preview['changes'])) + '), in normalized order:')
    lines.extend(str(index) + '. ' + change['action'] + ' | type ' + quoted(change['resource_type']) +
                 ' | address ' + quoted(change['address']) for index, change in enumerate(preview['changes'], 1))
    return '\n'.join(lines)


# Pack lines without losing long labels or Unicode; 1400 code points fit within 2800 UTF-16 units
# This remains below Slack's 3000-character section limit under either counting convention
def _review_sections(content):
    sections, pending = [], ''
    for line in content.splitlines(keepends=True):
        if len(pending) + len(line) > 1400 and pending:
            sections.append(pending)
            pending = ''
        while len(line) > 1400:
            sections.append(line[:1400])
            line = line[1400:]
        pending += line
    if pending:
        sections.append(pending)
    return sections


# Page the native message within Slack's bounds; incomplete pages never offer a new approval shortcut
def card(request: dict, page: int = 0, expected_review_digest: str = '') -> dict:
    content = review_text(request)
    review_digest = hashlib.sha256(content.encode()).hexdigest()
    if expected_review_digest and expected_review_digest != review_digest:
        raise Refused('Review content changed. Show the request again before continuing', 409)
    sections = _review_sections(content)
    pages = (len(sections) + 7) // 8
    if type(page) is not int or not 0 <= page < pages:
        raise Refused('Review page is unavailable. Show the request again', 422)
    start, stop = page * 8, min((page + 1) * 8, len(sections))
    preview = request['manifest']['preview']
    header = (f"Request: {request['id']}\nDigest: {request['digest']}\nTarget: {request['manifest']['target']}\n"
              f"Page {page + 1}/{pages}; text sections {start + 1}–{stop}/{len(sections)}. "
              f"{len(preview['changes'])} changes; {len(preview.get('findings', []))} findings; "
              f"manual-only: {'yes' if preview.get('manual_only') else 'no'}.\n"
              + ('More review text remains. Continue before deciding.' if stop < len(sections) else
                 'End of normalized review text. Paging does not prove that a person read or understood it.'))
    blocks = [{'type': 'section', 'text': {'type': 'plain_text', 'text': header}}]
    blocks.extend({'type': 'section', 'text': {'type': 'plain_text', 'text': section}} for section in sections[start:stop])
    value = request['id'] + ':' + request['digest']
    elements = []
    for label, target in [('Previous review page', page - 1), ('Continue review', page + 1)]:
        if 0 <= target < pages:
            elements.append({'type': 'button', 'text': {'type': 'plain_text', 'text': label},
                             'action_id': 'review_previous' if target < page else 'review_next',
                             'value': value + ':' + review_digest + ':' + str(target)})
    if request['status'] == 'pending':
        if stop == len(sections) and not request.get('execution_blocker'):
            elements.append({'type': 'button', 'text': {'type': 'plain_text', 'text': 'Approve this plan'},
                             'action_id': 'approve', 'value': value, 'style': 'primary'})
        elements.append({'type': 'button', 'text': {'type': 'plain_text', 'text': 'Reject'},
                         'action_id': 'reject', 'value': value, 'style': 'danger'})
    if elements:
        blocks.append({'type': 'actions', 'elements': elements})
    return {'text': f"DeployOps deployment review: {request['id']}, page {page + 1}/{pages}", 'blocks': blocks}


# Keep chat delivery separate from deployment authority
class Slack:
    """Keep workspace and approver membership checks on the controller."""


    # Retain notification credentials only inside the transport owner
    def __init__(self, controller: Controller, settings: SlackConfig, signing_secret: str, bot_token: str):
        self.controller = controller
        self.settings = settings
        self.signing_secret = signing_secret
        self.bot_token = bot_token


    # Resolve approver identity through configured workspace membership
    def _actor(self, team: str, app: str, user: str) -> tuple[str, str]:
        if not self.settings.team_id or team != self.settings.team_id or app != self.settings.app_id:
            raise Refused("Slack workspace or app is not authorized", 403)
        github_id = self.settings.approvers.get(user)
        if not github_id:
            raise Refused("Slack member is not an approver", 403)
        return f"slack:{team}:{user}", github_id


    # Verify the signed interaction before applying the shared controller operation
    def handle(self, body: bytes, timestamp: str, signature: str) -> dict:
        delivery = verify_signature(body, timestamp, signature, self.signing_secret, self.controller.now())
        try:
            fields = parse_qs(body.decode(), strict_parsing=True, max_num_fields=50)
            if any(len(value) != 1 for value in fields.values()):
                raise ValueError("Repeated form fields")
            form = {key: value[0] for key, value in fields.items()}
            # Button actions bind the selected request digest to the configured approver
            if "payload" in form:
                payload = json.loads(form["payload"])
                actor, github_id = self._actor(payload["team"]["id"], payload["api_app_id"], payload["user"]["id"])
                if payload.get("type") != "block_actions" or len(payload["actions"]) != 1:
                    raise ValueError("Unsupported interaction")
                action = payload["actions"][0]
                # Continuation is read-only and checks both immutable request and displayed review identity
                if action['action_id'] in {'review_previous', 'review_next'}:
                    request_id, digest, review_digest, page = action['value'].split(':')
                    if not page.isascii() or not page.isdecimal() or str(int(page)) != page:
                        raise ValueError('Invalid review page')
                    request = self.controller.get(request_id)
                    if digest != request['digest']:
                        raise Refused('Request digest changed. Show the request again', 409)
                    return {'response_type': 'ephemeral', **card(request, int(page), review_digest)}
                request_id, digest = action["value"].split(":", 1)
                request = self.controller.decide(
                    request_id, Decision(digest=digest, decision=action["action_id"]), actor, github_id,
                )
                return {"response_type": "ephemeral", **card(request)}
            # Slash commands share the same configured identity and controller policy
            actor, github_id = self._actor(form["team_id"], form["api_app_id"], form["user_id"])
            if form.get("command") != "/deploy-ops":
                raise ValueError("Unexpected command")
            words = form.get("text", "").split()
            command = words[0] if words else "help"
            if command == "show" and len(words) == 2:
                return {"response_type": "ephemeral", **card(self.controller.get(words[1]))}
            if command == "pending" and len(words) == 1:
                requests = [item for item in self.controller.active_requests() if item["status"] == "pending"]
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


    # Record delivery failure without exposing credentials or changing approval
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
