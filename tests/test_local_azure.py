# tests/test_local_azure.py - Bound certificate authentication to an explicit Azure resource group.


import json
import time
from pathlib import Path

import pytest
import tomlkit
from pydantic import ValidationError

from deploy_ops.delivery.access import Grant, credentials, verify
from deploy_ops.delivery.engine import Engine
from deploy_ops.delivery.process import Process
from deploy_ops.delivery.spec import load
from deploy_ops.errors import Refused

# Public fixture identifiers never authenticate to Azure
SUBSCRIPTION = "11111111-1111-1111-1111-111111111111"
TENANT = "22222222-2222-2222-2222-222222222222"
CLIENT = "33333333-3333-3333-3333-333333333333"
GROUP = f"/subscriptions/{SUBSCRIPTION}/resourceGroups/fixture"


# Model native authentication and one reviewed ARM deployment without a network connection
class Azure:

    # Keep all native effects and identity mismatches observable to the tests
    def __init__(self):
        self.calls, self.environments, self.writes = [], [], []
        self.account = {"id": SUBSCRIPTION, "tenantId": TENANT, "environmentName": "AzureCloud", "state": "Enabled",
                        "user": {"name": CLIENT, "type": "servicePrincipal"}}
        self.group = GROUP
        self.deployment = ""
        self.fail_preview = False


    # Return provider-shaped observations through the real isolated Process boundary
    def run(self, process, argv, cwd, timeout=600, capture=False, input_text=None):
        process.heartbeat()
        self.calls.append(argv)
        self.environments.append(dict(process.env))
        assert capture and argv[0] == "az"
        if argv[1:3] == ["account", "show"]:
            result = self.account
        elif argv[1:3] == ["group", "show"]:
            result = {"id": self.group}
        elif argv[1:3] == ["containerapp", "show"]:
            result = {"properties": {"configuration": {"activeRevisionsMode": "Single"}, "latestReadyRevisionName": "old"}}
        elif argv[1:3] == ["containerapp", "update"]:
            self.writes.append("update-app")
            result = {"properties": {"latestRevisionName": "new"}}
        elif argv[1:4] == ["containerapp", "revision", "show"]:
            result = {"properties": {"healthState": "Healthy", "provisioningState": "Provisioned"}}
        elif argv[1] == "version":
            result = {"azure-cli": "2.91.0"}
        elif argv[1:4] == ["deployment", "group", "what-if"]:
            if self.fail_preview:
                raise Refused("azure.deployment.group.what-if failed with exit code 1")
            result = {"status": "Succeeded", "changes": [{"resourceId": GROUP + "/providers/Microsoft.Storage/storageAccounts/fixture",
                      "changeType": "Create", "after": {"type": "Microsoft.Storage/storageAccounts", "name": "fixture"}}]}
        elif argv[1:4] == ["deployment", "group", "list"]:
            result = []
        elif argv[1:4] == ["deployment", "group", "create"]:
            self.writes.append("create")
            self.deployment = argv[argv.index("--name") + 1]
            result = {}
        elif argv[1:4] == ["deployment", "group", "show"]:
            result = {"id": GROUP + "/providers/Microsoft.Resources/deployments/" + self.deployment,
                      "properties": {"provisioningState": "Succeeded", "outputs": {}}}
        else:
            assert argv[1] == "login" or argv[1:3] in (["cloud", "set"], ["account", "set"])
            result = {}
        return json.dumps(result)


# Keep synthetic certificate bytes outside the workspace and its source repositories
@pytest.fixture
def azure(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    source = workspace / "infrastructure"
    source.mkdir(parents=True)
    (source / "main.json").write_text(json.dumps({
        "$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentTemplate.json#",
        "contentVersion": "1.0.0.0", "resources": []}))
    path = workspace / "deploy-ops.toml"
    document = {"name": "fixture", "repositories": {"infrastructure": {"path": "infrastructure", "allow_uncommitted": True}},
                "environments": {"development": {"provider": "azure"}}, "components": {"foundation": {
                    "repository": "infrastructure", "kind": "infrastructure", "adapter": "arm", "options": {
                        "subscription_id": SUBSCRIPTION, "tenant_id": TENANT, "resource_group": "fixture", "template": "main.json"}}}}
    path.write_text(tomlkit.dumps(document))
    certificate = tmp_path / "certificate.pem"
    certificate.write_text("synthetic-private-key-marker-not-valid-PEM")
    certificate.chmod(0o600)
    grant = {"provider": "azure", "identity": {"tenant_id": TENANT, "client_id": CLIENT,
             "subscription_id": SUBSCRIPTION, "resource_group": "fixture", "cloud": "AzureCloud",
             "certificate_file": str(certificate)}, "adapters": ["arm", "bicep", "container-apps"],
             "resource_types": ["Microsoft.Storage/storageAccounts"], "actions": ["create", "read", "no-op"], "max_changes": 1}
    access = {"workspace_digest": load(path).digest, "expires_at": int(time.time()) + 3600,
              "authorization": "Synthetic certificate qualification only", "environments": {"development": grant}}
    access_path = tmp_path / "access.toml"
    access_path.write_text(tomlkit.dumps(access))
    access_path.chmod(0o600)
    monkeypatch.setenv("DEPLOY_OPS_CLOUD_ACCESS_FILE", str(access_path))
    provider = Azure()

    # Replace only subprocess execution so grant and controller decisions remain real
    def run(process, *args, **kwargs):
        return provider.run(process, *args, **kwargs)

    monkeypatch.setattr(Process, "run", run)
    return Engine(path), provider, certificate, grant


# Complete the actual approval path and verify no certificate bytes or human cache escape
def test_local_azure_certificate_delivers_reviewed_arm(azure, monkeypatch):
    engine, provider, certificate, _ = azure
    monkeypatch.setenv("AZURE_CONFIG_DIR", "/human/azure-cache")
    monkeypatch.setenv("ARM_CLIENT_SECRET", "ambient-secret-must-not-propagate")
    release = engine.advance(engine.create("development")["id"])
    assert release["status"] == "waiting", release.get("reason")
    engine.approve(release["id"], "foundation", release["nodes"]["foundation"]["request_digest"])
    release = engine.advance(release["id"])
    assert release["status"] == "succeeded", release.get("reason")
    assert provider.writes == ["create"]
    logins = [call for call in provider.calls if call[1] == "login"]
    assert len(logins) == 2
    assert all(call[call.index("--certificate") + 1] == str(certificate) for call in logins)
    assert all("--federated-token" not in call and "--password" not in call for call in logins)
    assert all(env["AZURE_CONFIG_DIR"] == str(Path(env["HOME"]) / ".azure") for env in provider.environments)
    assert all("ARM_CLIENT_SECRET" not in env and env["AZURE_EXTENSION_USE_DYNAMIC_INSTALL"] == "no"
               for env in provider.environments)
    evidence = json.dumps([release, engine.ledger.events(release["id"]), engine.ledger.store.audit()])
    assert str(certificate) not in evidence and certificate.read_text() not in evidence
    assert not list((engine.state / "releases").glob("*/*/home"))


# Failed preparation can expose its target without retrying what-if or clearing uncertainty
def test_failed_azure_preview_status_without_submission(azure):
    engine, provider, _, _ = azure
    provider.fail_preview = True
    release = engine.advance(engine.create("development")["id"])
    assert release["status"] == "uncertain"
    assert not release["nodes"]["foundation"].get("artifact_digest")
    observed = engine.provider_status(release["id"], "foundation")
    assert observed["status"] == "no-submission-recorded"
    assert observed["automatic_retry"] is False
    assert observed["resource_state"] == "unverified"
    assert observed["target"]["subscription_id"] == SUBSCRIPTION
    assert observed["deployment_id"].startswith(GROUP + "/providers/Microsoft.Resources/deployments/")
    assert engine.ledger.get(release["id"])["status"] == "uncertain"
    assert sum(call[1:4] == ["deployment", "group", "what-if"] for call in provider.calls) == 1
    assert provider.writes == []


# Missing reviewed artifacts never permit adoption of an existing submission record
def test_azure_pending_journal_requires_reviewed_envelope(azure):
    engine, provider, _, _ = azure
    provider.fail_preview = True
    release = engine.advance(engine.create("development")["id"])
    marker = engine.state / "releases" / release["id"] / "foundation" / "arm-operation.json"
    marker.write_text('{"status":"submitting"}')
    with pytest.raises(Refused, match="submission record exists without reviewed evidence"):
        engine.provider_status(release["id"], "foundation")
    assert provider.writes == []
    assert engine.ledger.get(release["id"])["status"] == "uncertain"


# Mismatched options are rejected before native authentication receives a certificate
@pytest.mark.parametrize("field", ["subscription_id", "tenant_id", "resource_group"])
def test_azure_target_mismatch_never_logs_in(azure, field):
    engine, provider, _, raw = azure
    release = engine.create("development")
    context = engine._context(release, "foundation", {})
    context.options[field] = "different"
    with pytest.raises(Refused, match="target differs"):
        verify(context, Grant.model_validate(raw))
    assert provider.calls == []


# Account and resource-group readback must identify the exact granted service principal
@pytest.mark.parametrize("field", ["subscription", "tenant", "principal", "human", "cloud", "disabled", "group"])
def test_azure_native_identity_mismatch_stops_before_preview(azure, field):
    engine, provider, _, _ = azure
    if field == "group":
        provider.group = GROUP + "-other"
    elif field in {"principal", "human"}:
        provider.account["user"]["name" if field == "principal" else "type"] = "different"
    else:
        key = {"subscription": "id", "tenant": "tenantId", "cloud": "environmentName", "disabled": "state"}[field]
        provider.account[key] = "different"
    release = engine.advance(engine.create("development")["id"])
    assert release["status"] in {"failed", "uncertain"}, release.get("reason")
    assert not any(call[1] == "deployment" for call in provider.calls)


# Only the approved provider's native non-executable adapters belong in a grant
@pytest.mark.parametrize("change", ["terraform", "workflow", "ecs", "cloud", "extra", "relative"])
def test_azure_grant_rejects_unbounded_authentication(azure, change):
    _, _, _, raw = azure
    if change in {"terraform", "workflow", "ecs"}:
        raw["adapters"] = [change]
    else:
        key = {"cloud": "cloud", "extra": "client_secret", "relative": "certificate_file"}[change]
        raw["identity"][key] = "invalid"
    with pytest.raises((Refused, ValidationError)):
        Grant.model_validate(raw)


# File permissions and resolved locations are checked before source preparation or login
@pytest.mark.parametrize("change", ["permissions", "workspace", "source-alias"])
def test_azure_certificate_must_remain_private_and_outside_source(azure, change):
    engine, provider, certificate, _ = azure
    if change == "permissions":
        certificate.chmod(0o644)
    else:
        moved = engine.path.parent / ("certificate.pem" if change == "workspace" else "infrastructure/certificate.pem")
        certificate.rename(moved)
        certificate.symlink_to(moved)
    with pytest.raises(Refused):
        engine.create("development")
    assert provider.calls == []


# The credential mapping transports only public identity and a file reference
def test_azure_credentials_never_read_or_export_private_key(azure):
    _, _, certificate, raw = azure
    values = credentials(Grant.model_validate(raw))
    assert values["ARM_CLIENT_CERTIFICATE_PATH"] == str(certificate)
    assert certificate.read_text() not in json.dumps(values)


# The same certificate boundary supports a pinned app image without infrastructure credentials in source
def test_local_azure_certificate_delivers_container_app(azure, monkeypatch):
    engine, provider, _, _ = azure
    document = tomlkit.parse(engine.path.read_text())
    document["components"]["foundation"] = {"repository": "infrastructure", "kind": "application",
        "adapter": "container-apps", "options": {"subscription_id": SUBSCRIPTION, "resource_group": "fixture",
        "name": "app", "image": "registry.example/app@sha256:" + "a" * 64}}
    engine.path.write_text(tomlkit.dumps(document))
    access_path = engine.gate.access_file
    access = tomlkit.parse(access_path.read_text())
    access["workspace_digest"] = load(engine.path).digest
    access["environments"]["development"]["resource_types"] = ["application_revision"]
    access["environments"]["development"]["actions"] = ["update"]
    access_path.write_text(tomlkit.dumps(access))
    engine = Engine(engine.path)
    release = engine.advance(engine.create("development")["id"])
    assert release["status"] == "waiting", release.get("reason")
    engine.approve(release["id"], "foundation", release["nodes"]["foundation"]["request_digest"])
    release = engine.advance(release["id"])
    assert release["status"] == "succeeded", release.get("reason")
    assert provider.writes == ["update-app"]
    assert release["nodes"]["foundation"]["outputs"]["revision"] == "new"


# Certificate support must not redirect the established remote OIDC login contract
def test_remote_azure_login_remains_federated():
    from types import SimpleNamespace
    calls = []
    context = SimpleNamespace(component=SimpleNamespace(adapter="arm"), command=lambda argv: calls.append(argv))
    Engine._login(SimpleNamespace(gate=object()), context,
        {"ARM_CLIENT_ID": CLIENT, "ARM_TENANT_ID": TENANT, "ARM_OIDC_TOKEN": "synthetic-assertion"})
    assert len(calls) == 1 and "--federated-token" in calls[0] and "--certificate" not in calls[0]
