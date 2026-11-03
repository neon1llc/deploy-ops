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

ADMIN = "operator-test-secret-" + "x" * 43


@pytest.fixture
def signing():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    verifier = GithubVerifier("deploy-ops")

    class Keys:
        def get_signing_key_from_jwt(self, token):
            class Public:
                key = private.public_key()
            return Public()

    verifier.keys = Keys()
    return private, verifier


def signed(source, private, **updates):
    now = int(time.time())
    values = source.model_dump()
    values["sub"] = values.pop("subject")
    values.update(iss=ISSUER, aud="deploy-ops", iat=now, nbf=now, exp=now + 300, jti="example-token")
    values.update(updates)
    return jwt.encode(values, private, algorithm="RS256", headers={"kid": "test-key"})


@pytest.mark.parametrize("updates", [{"aud": "wrong"}, {"iss": "https://attacker.invalid"}, {"exp": 1}, {"iat": 9999999999}, {"job_workflow_sha": "bad"}])
def test_oidc_rejects_invalid_claims(system, signing, updates):
    private, verifier = signing
    with pytest.raises(Refused):
        verifier.verify(signed(sample_source(system[1]), private, **updates))


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


def signature(body, timestamp, secret):
    return "v0=" + hmac.new(secret.encode(), b"v0:" + str(timestamp).encode() + b":" + body, hashlib.sha256).hexdigest()


@pytest.mark.parametrize("age", [-301, 301, 10000])
def test_slack_rejects_stale_and_future_requests(age):
    now, body, secret = 1800000000, b"command=test", "signing-secret"
    timestamp = now - age
    with pytest.raises(Refused):
        verify_signature(body, str(timestamp), signature(body, timestamp, secret), secret, now)


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
