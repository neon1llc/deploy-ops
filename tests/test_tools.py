# tests/test_tools.py - Exercise provider protocols and fail-closed tool behavior without cloud calls.

import json
import os
import sys
import time
from pathlib import Path

import httpx
import pytest

from deploy_ops.client import Client, private_json
from deploy_ops.clouds import environment
from deploy_ops.errors import Refused
from deploy_ops.plans import cost_delta, normalize
from deploy_ops.runner import clean_environment, supervised


@pytest.mark.parametrize("engine", ["terraform", "opentofu"])
@pytest.mark.parametrize("cloud,kind", [("aws", "aws_ecs_service"), ("azure", "azurerm_linux_web_app"), ("gcp", "google_cloud_run_v2_service")])
def test_saved_plan_normalization_across_clouds(proposal, engine, cloud, kind):
    raw = proposal.preview
    raw["resource_changes"][0]["type"] = kind
    preview = normalize(engine, cloud, raw)
    assert preview.changes[0].action == "update"
    assert preview.findings == []


@pytest.mark.parametrize("mutation", ["empty", "unknown_action", "incomplete", "provisioner", "bad_type", "bad_output", "bad_values"])
def test_unsupported_plan_shapes_do_not_become_safe_changes(proposal, mutation):
    raw = proposal.preview
    if mutation == "empty":
        raw = {}
    elif mutation == "unknown_action":
        raw["resource_changes"][0]["change"]["actions"] = ["new-unknown-operation"]
    elif mutation == "incomplete":
        raw["complete"] = False
    elif mutation == "bad_type":
        raw["resource_changes"][0]["type"] = 123
    elif mutation == "bad_output":
        raw["output_changes"] = {"value": "not-a-change"}
    elif mutation == "bad_values":
        raw["planned_values"] = []
    else:
        raw["configuration"] = {"root_module": {"resources": [{"provisioners": [{"type": "local-exec"}]}]}}
        assert normalize("terraform", "aws", raw).findings
        return
    with pytest.raises(Refused):
        normalize("terraform", "aws", raw)


@pytest.mark.parametrize("amount,expected", [("1.001", 101), ("-1.001", -100), ("0", 0)])
def test_cost_estimates_round_up(amount, expected):
    assert cost_delta({"diffTotalMonthlyCost": amount, "currency": "USD"}) == expected


@pytest.mark.parametrize("cloud", ["aws", "azure", "gcp"])
def test_cloud_authentication_uses_short_lived_assertions(cloud):
    calls, audiences = [], []
    identities = {
        "aws": {"role_arn": "arn:aws:iam::111111111111:role/example", "region": "us-east-1"},
        "azure": {"client_id": "11111111-1111-1111-1111-111111111111", "tenant_id": "22222222-2222-2222-2222-222222222222", "subscription_id": "33333333-3333-3333-3333-333333333333"},
        "gcp": {"workload_identity_provider": "projects/123/locations/global/workloadIdentityPools/example/providers/github", "service_account": "runner@example-project.iam.gserviceaccount.com", "project_id": "example-project"},
    }

    def respond(request):
        calls.append(request)
        if request.url.host.endswith("amazonaws.com"):
            return httpx.Response(200, text="<Response><AccessKeyId>id</AccessKeyId><SecretAccessKey>secret</SecretAccessKey><SessionToken>session</SessionToken></Response>")
        if request.url.host == "sts.googleapis.com":
            return httpx.Response(200, json={"access_token": "federated"})
        return httpx.Response(200, json={"accessToken": "temporary"})

    def token(audience):
        audiences.append(audience)
        return "test-assertion"

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = environment(cloud, identities[cloud], token, "request-id", client)
    assert len(audiences) == 1
    assert result
    assert all(request.url.scheme == "https" for request in calls)
    assert len(calls) == {"aws": 1, "azure": 0, "gcp": 2}[cloud]


def test_runner_drops_ambient_credentials_and_cli_injection(monkeypatch, tmp_path):
    monkeypatch.setenv("AWS_PROFILE", "human-admin")
    monkeypatch.setenv("DEPLOY_OPS_ADMIN_TOKEN", "operator-secret")
    monkeypatch.setenv("TF_CLI_ARGS_apply", "-auto-approve")
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "workflow-secret")
    result = clean_environment(str(tmp_path), {"AWS_SESSION_TOKEN": "temporary"})
    assert all(key not in result for key in ("AWS_PROFILE", "DEPLOY_OPS_ADMIN_TOKEN", "TF_CLI_ARGS_apply", "ACTIONS_ID_TOKEN_REQUEST_TOKEN"))
    assert result["AWS_SESSION_TOKEN"] == "temporary"


def test_supervised_process_stops_at_the_deadline(tmp_path):
    with pytest.raises(Refused, match="deadline"):
        supervised([sys.executable, "-c", "import time; time.sleep(30)"], tmp_path,
                   dict(os.environ), lambda: None, int(time.time()) + 1)


def test_local_secret_files_and_remote_transport(tmp_path):
    path = tmp_path / "lease.json"
    private_json(path, {"token": "not-a-real-secret"})
    assert path.stat().st_mode & 0o777 == 0o600
    assert json.loads(path.read_text())["token"] == "not-a-real-secret"
    with pytest.raises(Refused, match="HTTPS"):
        Client("http://example.com")


@pytest.mark.parametrize("unknown", [True, {"name": True}, [False, {"id": True}]])
def test_unknown_values_need_human_review(proposal, unknown):
    proposal.preview["resource_changes"][0]["change"]["after_unknown"] = unknown
    assert normalize("terraform", "aws", proposal.preview).manual_only
