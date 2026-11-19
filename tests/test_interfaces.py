# tests/test_interfaces.py - Check signed identity, HTTP boundaries, and phone approvals together.


import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from deploy_ops.api import create_app
from deploy_ops.demo import sample_source
from deploy_ops.errors import Refused
from deploy_ops.identity import GithubVerifier, ISSUER
from deploy_ops.slack import Slack, verify_signature

# Fixed synthetic operator material keeps tests independent of configured secrets
ADMIN = "operator-test-secret-" + "x" * 43


# Audit export verifies the saved extent even when the controller receives a concurrent append
@pytest.mark.parametrize("altered", [False, True])
def test_audit_export_binds_exact_authenticated_rows(system, monkeypatch, tmp_path, altered):
    from deploy_ops import gate_cli
    from deploy_ops.store import Store
    controller = system[0]
    client = TestClient(create_app(controller.settings, ADMIN))
    output = tmp_path / 'audit.json'
    output.write_text('previous-completed-export')

    # Keep authentication real while controlling a deterministic read/append boundary
    class Transport:
        # Bind only the test controller rather than inherited connection settings
        def __init__(self, url):
            pass

        # Append after reading the exact authenticated exported extent
        def call(self, method, path, body=None, operator=False):
            assert operator
            response = client.request(method, path, headers={"Authorization": "Bearer " + ADMIN}, json=body)
            assert response.status_code == 200
            result = response.json()
            with controller.store.transaction() as connection:
                controller.store.event(connection, 1800000000, 'fixture', 'append', 'export', {})
            if path.endswith('/verify'):
                return result
            if altered:
                result.append({'sequence': 99, 'payload': '{}', 'previous_hash': '', 'hash': ''})
            return result

    monkeypatch.setattr(gate_cli, 'Client', Transport)
    args = gate_cli.parser().parse_args(['audit', '--out', str(output)])
    if altered:
        with pytest.raises(Refused, match='Audit chain'):
            gate_cli._dispatch(args)
        assert output.read_text() == 'previous-completed-export'
    else:
        result = gate_cli._dispatch(args)
        rows = json.loads(output.read_text())
        assert {key: result[key] for key in ('events', 'head', 'format')} == Store.verify_rows(rows)
        assert result['sha256'] == hashlib.sha256(output.read_bytes()).hexdigest()
        assert controller.store.verify()['events'] > result['events']
        assert output.stat().st_mode & 0o777 == 0o600


# Use fresh fixture keys without contacting the identity provider
@pytest.fixture
def signing():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    verifier = GithubVerifier("deploy-ops")


    # Resolve only the fixture public key without network discovery
    class Keys:
        # Return the public half of the fixture signing key
        def get_signing_key_from_jwt(self, token):
            # Match the verifier key interface without exposing the private key
            class Public:
                key = private.public_key()
            return Public()

    verifier.keys = Keys()
    return private, verifier


# Create bounded fixture claims with explicit overrides for negative cases
def signed(source, private, **updates):
    now = int(time.time())
    values = source.model_dump()
    values["sub"] = values.pop("subject")
    values.update(iss=ISSUER, aud="deploy-ops", iat=now, nbf=now, exp=now + 300, jti="example-token")
    values.update(updates)
    return jwt.encode(values, private, algorithm="RS256", headers={"kid": "test-key"})


# Invalid issuer, audience, time and workflow identity cannot authenticate
@pytest.mark.parametrize("updates", [{"aud": "wrong"}, {"iss": "https://attacker.invalid"}, {"exp": 1}, {"iat": 9999999999}, {"job_workflow_sha": "bad"}])
def test_oidc_rejects_invalid_claims(system, signing, updates):
    private, verifier = signing
    with pytest.raises(Refused):
        verifier.verify(signed(sample_source(system[1]), private, **updates))


# Validate real signatures through approval, claim and completion
def test_http_lifecycle_uses_real_signature_validation(system, signing, proposal, source):
    controller, target, _ = system
    private, verifier = signing
    app = create_app(controller.settings, ADMIN, verifier)
    client = TestClient(app)
    plan_auth = {"Authorization": "Bearer " + signed(source, private)}
    execute_auth = {"Authorization": "Bearer " + signed(sample_source(target, "execute"), private)}
    response = client.post("/v1/requests", json=proposal.model_dump(), headers=plan_auth)
    assert response.status_code == 201
    request = response.json()
    assert client.get(f"/v1/requests/{request['id']}").status_code == 401
    response = client.post(f"/v1/admin/requests/{request['id']}/decision", headers={"Authorization": "Bearer " + ADMIN}, json={"digest": request["digest"], "decision": "approve"})
    assert response.status_code == 200
    response = client.post(f"/v1/requests/{request['id']}/claim", headers=execute_auth, json={"digest": request["digest"], "artifact_digest": proposal.artifact_digest})
    assert response.status_code == 200
    lease = response.json()
    execution_auth = {"Authorization": "Bearer " + lease["token"]}
    assert client.post(f"/v1/executions/{request['id']}/heartbeat", headers=execution_auth).status_code == 200
    assert client.post(f"/v1/executions/{request['id']}/complete", headers=execution_auth, json={"outcome": "succeeded", "exit_code": 0}).json()["status"] == "succeeded"


# Reject untrusted request content without echoing supplied secret values
def test_api_does_not_echo_secret_inputs_or_accept_large_bodies(system, signing):
    private, verifier = signing
    settings = system[0].settings.model_copy(update={"max_body_bytes": 1024})
    client = TestClient(create_app(settings, ADMIN, verifier))
    response = client.post("/v1/requests", json={"unexpected": "private-plan-value"})
    assert response.status_code == 422
    assert "private-plan-value" not in response.text
    assert client.post("/v1/requests", content=b"x" * 1025).status_code == 413
    assert client.post("/v1/admin/pause").status_code == 401
    assert client.get("/healthz").headers["cache-control"] == "no-store"


# Match the documented Slack request-signature format for fixtures
def signature(body, timestamp, secret):
    return "v0=" + hmac.new(secret.encode(), b"v0:" + str(timestamp).encode() + b":" + body, hashlib.sha256).hexdigest()


# Reject signed requests outside the replay window in either direction
@pytest.mark.parametrize("age", [-301, 301, 10000])
def test_slack_rejects_stale_and_future_requests(age):
    now, body, secret = 1800000000, b"command=test", "signing-secret"
    timestamp = now - age
    with pytest.raises(Refused):
        verify_signature(body, str(timestamp), signature(body, timestamp, secret), secret, now)


# Bind a Slack decision to the authorized member and exact pending request
def test_slack_buttons_are_bound_to_workspace_member_and_digest(system, proposal, source):
    controller, _, clock = system
    config = controller.settings.slack.model_copy(update={"team_id": "T1", "app_id": "A1", "approvers": {"U1": "999"}})
    slack = Slack(controller, config, "signing-secret", "")
    request = controller.submit(proposal, source)
    payload = {"type": "block_actions", "team": {"id": "T1"}, "api_app_id": "A1", "user": {"id": "U1"},
               "actions": [{"action_id": "approve", "value": request["id"] + ":" + request["digest"]}]}
    body = urlencode({"payload": json.dumps(payload)}).encode()
    timestamp = clock[0]
    result = slack.handle(body, str(timestamp), signature(body, timestamp, "signing-secret"))
    assert result["response_type"] == "ephemeral"
    assert controller.get(request["id"])["status"] == "approved"
    with pytest.raises(Refused):
        slack.handle(body, str(timestamp), signature(body, timestamp, "signing-secret"))
    payload["user"]["id"] = "intruder"
    body = urlencode({"payload": json.dumps(payload)}).encode()
    with pytest.raises(Refused, match="approver"):
        slack.handle(body, str(timestamp), signature(body, timestamp, "signing-secret"))


# Revocation does not make an old signed delegation reusable
def test_signed_delegation_command_cannot_be_replayed_after_revocation(system):
    controller, _, clock = system
    config = controller.settings.slack.model_copy(update={"team_id": "T1", "app_id": "A1", "approvers": {"U1": "999"}})
    slack = Slack(controller, config, "secret", "")
    body = urlencode({"team_id": "T1", "api_app_id": "A1", "user_id": "U1", "command": "/deploy-ops", "text": "delegate sandbox 1 1"}).encode()
    signed_body = signature(body, clock[0], "secret")
    slack.handle(body, str(clock[0]), signed_body)
    controller.pause(True, "operator")
    controller.pause(False, "operator")
    with pytest.raises(Refused, match="already handled"):
        slack.handle(body, str(clock[0]), signed_body)


# Every normalized row/finding survives bounded pages, including long labels and non-BMP Unicode
@pytest.mark.parametrize('count', [25, 2000])
def test_slack_review_preserves_complete_normalized_text(system, proposal, source, count):
    from deploy_ops.slack import card, review_text
    request = system[0].submit(proposal, source)
    request['manifest']['preview'] = {
        'changes': [{'address': f'late-resource-{index}-' + 'x' * 400, 'resource_type': 'aws_fixture',
                     'action': 'delete' if index == count - 1 else 'update'} for index in range(count)],
        'findings': ['Full finding ' + '🔎' * 4000 + ' end of finding'], 'manual_only': True}
    request['reason'] = 'Long public review reason ' + 'r' * 5000
    content, rendered, page = review_text(request), [], 0
    while True:
        result = card(request, page)
        sections = [block['text']['text'] for block in result['blocks'] if block['type'] == 'section']
        rendered.extend(sections[1:])
        assert len(result['blocks']) <= 50
        assert all(len(text.encode('utf-16-le')) // 2 <= 3000 for text in sections)
        assert all(block['text']['type'] == 'plain_text' for block in result['blocks'] if block['type'] == 'section')
        assert request['id'] in sections[0] and request['digest'] in sections[0] and 'manual-only: yes' in sections[0]
        buttons = [item for block in result['blocks'] for item in block.get('elements', [])]
        assert len({item['action_id'] for item in buttons}) == len(buttons)
        assert all(len(item['value']) <= 2000 and len(item['text']['text']) <= 75 for item in buttons)
        next_page = next((item for item in buttons if item['text']['text'] == 'Continue review'), None)
        assert any(item['action_id'] == 'reject' for item in buttons)
        if next_page:
            assert not any(item['action_id'] == 'approve' for item in buttons)
            page += 1
        else:
            assert any(item['action_id'] == 'approve' for item in buttons)
            break
    assert ''.join(rendered) == content
    assert f'late-resource-{count-1}' in content and 'type "aws_fixture"' in content
    assert 'end of finding' in content and 'r' * 5000 in content


# Signed continuation reads the exact request and review without changing authority or audit
# Forged pages/digests/membership and changed review content refuse before a decision
def test_slack_authenticated_review_continuation_and_held_approval(system, proposal, source):
    from deploy_ops.slack import card
    controller, _, clock = system
    proposal.preview['resource_changes'] = [
        {'address': 'aws_ecs_service.' + str(index) + 'x' * 380, 'type': 'aws_ecs_service',
         'change': {'actions': ['update']}} for index in range(150)]
    request = controller.submit(proposal, source)
    assert request['status'] == 'pending'
    config = controller.settings.slack.model_copy(update={'team_id': 'T1', 'app_id': 'A1', 'approvers': {'U1': '999'}})
    slack = Slack(controller, config, 'review-secret', '')
    first = card(controller.get(request['id']))
    buttons = lambda result: [item for block in result['blocks'] for item in block.get('elements', [])]
    cursor = next(item['value'] for item in buttons(first) if item['text']['text'] == 'Continue review')
    before = controller.store.verify()

    # Preserve the existing signed member boundary for read-only pagination and later approval
    def interact(action, value, user='U1'):
        payload = {'type': 'block_actions', 'team': {'id': 'T1'}, 'api_app_id': 'A1', 'user': {'id': user},
                   'actions': [{'action_id': action, 'value': value}]}
        body = urlencode({'payload': json.dumps(payload)}).encode()
        return slack.handle(body, str(clock[0]), signature(body, clock[0], 'review-secret'))

    with pytest.raises(Refused, match='approver'):
        interact('review_next', cursor, 'intruder')
    fields = cursor.split(':')
    for index, replacement in [(1, 'f' * 64), (2, 'e' * 64), (3, '999999'), (3, '-1'), (3, '01')]:
        changed = fields.copy(); changed[index] = replacement
        with pytest.raises(Refused):
            interact('review_next', ':'.join(changed))
    result = interact('review_next', cursor)
    while any(item['text']['text'] == 'Continue review' for item in buttons(result)):
        cursor = next(item['value'] for item in buttons(result) if item['text']['text'] == 'Continue review')
        result = interact('review_next', cursor)
    assert controller.store.verify() == before
    assert controller.get(request['id'])['status'] == 'pending'
    approve = next(item for item in buttons(result) if item['action_id'] == 'approve')
    interact('approve', approve['value'])
    approved = controller.get(request['id'])
    assert approved['status'] == 'approved' and approved['execution'] is None
    with pytest.raises(Refused, match='content changed'):
        interact('review_next', cursor)


# Existing incompatible prepared contracts remain visible but get no new approval shortcut
def test_slack_blocked_review_has_reject_without_approve(system, proposal, source):
    from deploy_ops.slack import card
    request = system[0].submit(proposal, source)
    request['execution_blocker'] = 'Prepare fresh compatible native evidence'
    result = card(request)
    actions = [item['action_id'] for block in result['blocks'] for item in block.get('elements', [])]
    assert 'reject' in actions and 'approve' not in actions
    assert any('Prepare fresh compatible native evidence' in block.get('text', {}).get('text', '') for block in result['blocks'])
