# tests/test_local_access.py - Exercise bounded local cloud authority without cloud calls or real credentials.


import json
import time
from pathlib import Path

import pytest
import tomlkit
from fastapi.testclient import TestClient

from deploy_ops.console import create_app
from deploy_ops.delivery.access import Grant, credentials, load_access, verify
from deploy_ops.delivery.configuration import source_hash
from deploy_ops.delivery.engine import Engine
from deploy_ops.delivery.process import Process
from deploy_ops.delivery.spec import load
from deploy_ops.errors import Refused


# Synthetic identities and artifacts must never resolve to real cloud access
ACCOUNT = "111111111111"
PRINCIPAL = f"arn:aws:iam::{ACCOUNT}:user/fixture"
IMAGE = "registry.example/app@sha256:" + "a" * 64
OUTPUTS = {"cluster": "fixture-cluster", "service": "fixture-service", "container": "web"}


# Model native provider state while leaving the real Engine and approval protocol intact
class Provider:
    def __init__(self):
        self.calls = []
        self.environments = []
        self.principal = PRINCIPAL
        self.stack = None
        self.change = None
        self.token = ""
        self.revision = "old"
        self.writes = []
        self.fail_preview = False


    # Advance only the synthetic remote state requested by the actual adapter command
    def run(self, process, argv, cwd, timeout=600, capture=False, input_text=None):
        process.heartbeat()
        self.calls.append(argv)
        self.environments.append(dict(process.env))
        process.emit("command.started", {"tool": argv[0]})
        if argv == ["aws", "--version"]:
            return "aws-cli/2.31.0 Python/3.13.0 fixture"
        if "get-caller-identity" in argv:
            return json.dumps({"Account": ACCOUNT, "Arn": self.principal})
        if "cloudformation" in argv:
            result = self.cloudformation(argv)
        elif "ecs" in argv:
            result = self.ecs(argv)
        else:
            raise AssertionError(f"Unexpected native command: {argv[0]}")
        process.emit("command.finished", {"tool": argv[0]})
        return json.dumps(result)


    # Return native-shaped change-set and stack evidence for one disposable fixture stack
    def cloudformation(self, argv):
        operation = argv[argv.index("cloudformation") + 1]
        if operation == "list-stacks":
            return {"StackSummaries": [] if self.stack is None else [self.stack]}
        if operation == "create-change-set":
            request = json.loads(Path(argv[argv.index("--cli-input-json") + 1].removeprefix("file://")).read_text())
            self.token = request["ClientToken"]
            self.stack = {"StackId": "arn:aws:cloudformation:us-east-1:111111111111:stack/fixture/one",
                          "StackName": "fixture", "StackStatus": "REVIEW_IN_PROGRESS", "Tags": request["Tags"]}
            self.change = {"ChangeSetId": "arn:aws:cloudformation:us-east-1:111111111111:changeSet/fixture/one",
                           "StackId": self.stack["StackId"], "ChangeSetType": "CREATE", "Status": "CREATE_COMPLETE",
                           "ExecutionStatus": "AVAILABLE", "Tags": request["Tags"], "Changes": [{"ResourceChange": {
                               "LogicalResourceId": "Cluster", "ResourceType": "AWS::ECS::Cluster", "Action": "Add"}}]}
            self.writes.append(operation)
            return {"Id": self.change["ChangeSetId"], "StackId": self.stack["StackId"]}
        if operation == "describe-change-set":
            if self.fail_preview:
                self.fail_preview = False
                raise Refused("Synthetic lost preview response")
            return self.change
        if operation == "describe-stacks":
            return {"Stacks": [self.stack]}
        if operation == "execute-change-set":
            self.writes.append(operation)
            self.stack.update(StackStatus="CREATE_COMPLETE", Outputs=[
                {"OutputKey": name, "OutputValue": value} for name, value in OUTPUTS.items()])
            return {}
        if operation == "describe-stack-events":
            return {"StackEvents": [{"EventId": "complete", "ResourceType": "AWS::CloudFormation::Stack",
                                     "ResourceStatus": "CREATE_COMPLETE", "ClientRequestToken": self.token}]}
        raise AssertionError(f"Unexpected CloudFormation operation: {operation}")


    # Preserve the baseline revision and expose only verified application updates
    def ecs(self, argv):
        operation = argv[argv.index("ecs") + 1]
        if operation == "describe-services":
            return {"services": [{"taskDefinition": self.revision, "deploymentController": {"type": "ECS"},
                                   "deployments": [{"rolloutState": "COMPLETED"}]}]}
        if operation == "describe-task-definition":
            return {"taskDefinition": {"family": "fixture", "containerDefinitions": [{"name": "web", "image": "old"}]}}
        if operation == "register-task-definition":
            self.writes.append(operation)
            return {"taskDefinition": {"taskDefinitionArn": "new"}}
        if operation == "update-service":
            self.revision = "new"
            self.writes.append(operation)
            return {}
        if operation == "wait":
            return {}
        raise AssertionError(f"Unexpected ECS operation: {operation}")


# Create only synthetic owner-only credential files outside the source workspace
@pytest.fixture
def local_cloud(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    infrastructure = workspace / "infrastructure"
    application = workspace / "application"
    infrastructure.mkdir()
    application.mkdir()
    (application / "artifact.txt").write_text("frozen application source\n")
    (infrastructure / "template.json").write_text(json.dumps({
        "Resources": {"Cluster": {"Type": "AWS::ECS::Cluster"}},
        "Outputs": {name: {"Value": value} for name, value in OUTPUTS.items()},
    }))
    document = {
        "name": "fixture",
        "repositories": {"infrastructure": {"path": "infrastructure", "allow_uncommitted": True},
                         "application": {"path": "application", "allow_uncommitted": True}},
        "environments": {"development": {"provider": "aws"}},
        "components": {
            "foundation": {"repository": "infrastructure", "kind": "infrastructure", "adapter": "cloudformation",
                           "outputs": list(OUTPUTS), "options": {"account_id": ACCOUNT, "region": "us-east-1",
                               "stack_name": "fixture", "template": "template.json"}},
            "web": {"repository": "application", "kind": "application", "adapter": "ecs", "needs": ["foundation"],
                    "options": {"region": "us-east-1", "image": IMAGE, "image_repository": "registry.example/app",
                                **{key: "${outputs.foundation." + key + "}" for key in OUTPUTS}}},
        },
    }
    path = workspace / "deploy-ops.toml"
    path.write_text(tomlkit.dumps(document))
    keyfile = tmp_path / "fixture-credentials"
    keyfile.write_text("[fixture]\naws_access_key_id = synthetic-key-id\naws_secret_access_key = synthetic-secret-value\n")
    keyfile.chmod(0o600)
    config = tmp_path / "fixture-config"
    config.write_text("[profile fixture]\nregion = us-east-1\n")
    config.chmod(0o600)
    access = {"schema_version": 1, "workspace_digest": load(path).digest, "expires_at": int(time.time()) + 3600,
              "authorization": "Synthetic fixture grant only; no live access", "environments": {"development": {
                  "provider": "aws", "identity": {"account_id": ACCOUNT, "principal_arn": PRINCIPAL, "region": "us-east-1",
                      "profile": "fixture", "credentials_file": str(keyfile), "config_file": str(config)},
                  "adapters": ["cloudformation", "ecs"], "resource_types": ["AWS::ECS::Cluster", "application_revision"],
                  "actions": ["create", "update", "read", "no-op"], "max_changes": 4}}}
    access_path = tmp_path / "fixture-access.toml"
    access_path.write_text(tomlkit.dumps(access))
    access_path.chmod(0o600)
    monkeypatch.setenv("DEPLOY_OPS_CLOUD_ACCESS_FILE", str(access_path))
    provider = Provider()

    # Replace only native process execution, leaving target and approval validation real
    def run(process, *args, **kwargs):
        return provider.run(process, *args, **kwargs)

    monkeypatch.setattr(Process, "run", run)
    return Engine(path), provider, access_path, config, keyfile


# Approve the current pending request while retaining the ordinary component sequence
def finish(engine, release):
    for _ in range(6):
        release = engine.advance(release["id"])
        if release["status"] == "succeeded":
            return release
        assert release["status"] == "waiting", release.get("reason")
        for name, node in release["nodes"].items():
            if node["status"] == "waiting":
                engine.approve(release["id"], name, node["request_digest"])
    raise AssertionError("Fixture did not complete within its bounded component sequence")


# Exercise infrastructure, application approval, restart and app-only reuse through the real Engine
def test_local_grant_complete_release_and_app_only_without_private_evidence(local_cloud, monkeypatch):
    engine, provider, access_path, _, keyfile = local_cloud
    monkeypatch.setenv("AWS_ENDPOINT_URL", "https://untrusted.example")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "ambient-key-must-not-reach-worker")
    release = finish(engine, engine.create("development"))
    assert release["nodes"]["foundation"]["outputs"] == OUTPUTS
    assert release["nodes"]["web"]["outputs"]["image"] == IMAGE
    assert provider.writes.count("execute-change-set") == 1

    # A fresh worker consumes persisted public outputs without reapplying infrastructure
    restarted = Engine(engine.path)
    reused = finish(restarted, restarted.create("development", "web", False))
    assert reused["nodes"]["web"]["dependency_receipts"]["foundation"] == release["id"]
    assert provider.writes.count("execute-change-set") == 1
    assert provider.writes.count("update-service") == 2
    assert all(env["AWS_PROFILE"] == "fixture" for env in provider.environments)
    assert all("AWS_ENDPOINT_URL" not in env and "AWS_ACCESS_KEY_ID" not in env for env in provider.environments)

    # Operator browser responses and durable events expose no credential references or values
    client = TestClient(create_app(restarted, "x" * 32), base_url="http://127.0.0.1:8765")
    headers = {"Authorization": "Bearer " + "x" * 32}
    responses = [client.get(route, headers=headers) for route in (
        "/api/workspace", "/api/releases", "/api/releases/" + reused["id"])]
    assert all(response.status_code == 200 for response in responses)
    evidence = json.dumps([response.json() for response in responses] + restarted.ledger.store.audit())
    for private in (str(access_path), str(keyfile), "synthetic-key-id", "synthetic-secret-value"):
        assert private not in evidence
    assert not list((engine.state / "releases").glob("*/*/home"))


# The actual native identity must match before creating even a remote preview
@pytest.mark.parametrize("mismatch", ["identity", "region"])
def test_wrong_identity_or_target_refuses_before_provider_write(local_cloud, mismatch):
    engine, provider, _, _, _ = local_cloud
    if mismatch == "identity":
        provider.principal = f"arn:aws:iam::{ACCOUNT}:user/other"
    else:
        engine.spec.components["foundation"].options["region"] = "us-west-2"
    if mismatch == "region":
        with pytest.raises(Refused):
            engine.create("development")
    else:
        result = engine.advance(engine.create("development")["id"])
        assert result["status"] != "succeeded" and "identity" in result["reason"]
    assert provider.writes == []


# Expiry and revocation must be rechecked after approval and before execution
@pytest.mark.parametrize("change", ["expired", "revoked", "edited", "artifact"])
def test_changed_authority_or_evidence_cannot_execute(local_cloud, change):
    engine, provider, access_path, _, _ = local_cloud
    release = engine.advance(engine.create("development")["id"])
    node = release["nodes"]["foundation"]
    assert release["status"] == "waiting"
    engine.approve(release["id"], "foundation", node["request_digest"])
    if change == "revoked":
        access_path.unlink()
    elif change in {"expired", "edited"}:
        access = tomlkit.parse(access_path.read_text())
        if change == "expired":
            access["expires_at"] = 1
        else:
            access["authorization"] = "A different synthetic operator authorization"
        access_path.write_text(tomlkit.dumps(access))
    else:
        artifact = engine.state / "releases" / release["id"] / "foundation" / "artifacts" / "cloudformation-template.json"
        artifact.write_text("{}\n")
    result = engine.advance(release["id"])
    assert result["status"] in {"failed", "uncertain"}
    assert provider.writes == ["create-change-set"]


# Source hooks and executable IaC cannot share the local credential-handling process
@pytest.mark.parametrize("kind", ["build", "workflow", "terraform", "opentofu"])
def test_executable_source_is_refused_before_credentials(local_cloud, kind):
    engine, provider, access_path, _, _ = local_cloud
    document = tomlkit.parse(engine.path.read_text())
    if kind == "build":
        document["components"]["foundation"]["build"] = {"command": ["python3", "untrusted.py"]}
    else:
        document["components"]["foundation"]["adapter"] = kind
    engine.path.write_text(tomlkit.dumps(document))
    access = tomlkit.parse(access_path.read_text())
    access["workspace_digest"] = load(engine.path).digest
    if kind != "build":
        access["environments"]["development"]["adapters"].append(kind)
    access_path.write_text(tomlkit.dumps(access))
    if kind == "build":
        changed = Engine(engine.path)
        result = changed.advance(changed.create("development")["id"])
        assert result["status"] == "failed" and "isolated" in result["reason"]
    else:
        changed = Engine(engine.path)
        with pytest.raises(Refused):
            changed.create("development")
    assert provider.calls == []


# Native configuration must not introduce endpoints, role helpers or extra key material
@pytest.mark.parametrize("override", ["endpoint_url", "credential_process", "default_endpoint", "session_token"])
def test_native_credential_overrides_are_refused(local_cloud, override):
    engine, _, access_path, config, keyfile = local_cloud
    if override == "default_endpoint":
        config.write_text("[DEFAULT]\nendpoint_url = https://untrusted.example\n[profile fixture]\nregion = us-east-1\n")
    elif override == "session_token":
        keyfile.write_text(keyfile.read_text() + "aws_session_token = synthetic-extra-session\n")
    else:
        config.write_text(config.read_text() + override + " = unexpected\n")
    grant = load_access(access_path, engine.spec.digest).environments["development"]
    with pytest.raises(Refused):
        credentials(grant)


# A saved workspace revision disables the old grant while keeping configuration usable
def test_workspace_save_invalidates_grant_without_reporting_a_failed_save(local_cloud):
    engine, provider, _, _, _ = local_cloud
    original = engine.path.read_text()
    updated = original.replace('name = "fixture"', 'name = "revised"', 1)
    client = TestClient(create_app(engine, "x" * 32), base_url="http://127.0.0.1:8765")
    response = client.post("/api/config/save", headers={"Authorization": "Bearer " + "x" * 32},
                           json={"configuration": updated, "source_hash": source_hash(original)})
    assert response.status_code == 200 and response.json()["status"] == "saved"
    assert engine.path.read_text() == updated
    revised = Engine(engine.path)
    assert revised.describe("development")["project"] == "revised"
    with pytest.raises(Refused, match="grant does not match"):
        revised.create("development")
    assert provider.calls == []


# Empty optional profile fields must retain the grant's explicitly selected profile
def test_empty_native_profile_keeps_granted_profile(local_cloud):
    engine, provider, access_path, _, _ = local_cloud
    release = engine.create("development")
    context = engine._context(release, "foundation", {})
    context.options["profile"] = ""
    grant = load_access(access_path, engine.spec.digest).environments["development"]
    assert verify(context, grant)["principal"] == PRINCIPAL
    assert provider.calls[-1][provider.calls[-1].index("--profile") + 1] == "fixture"


# GCP references must match approved metadata before any native library consumes the key
@pytest.mark.parametrize("change", [None, "endpoint", "identity", "project", "type"])
def test_gcp_credential_metadata_is_checked_without_exposing_key_material(tmp_path, change):
    path = tmp_path / "synthetic-service-account.json"
    data = {"type": "service_account", "project_id": "fixture-project",
            "client_email": "fixture@fixture-project.iam.gserviceaccount.com",
            "token_uri": "https://oauth2.googleapis.com/token", "private_key": "synthetic-unusable-private-key"}
    if change:
        key = {"endpoint": "token_uri", "identity": "client_email", "project": "project_id", "type": "type"}[change]
        data[key] = "unapproved"
    path.write_text(json.dumps(data))
    path.chmod(0o600)
    grant = Grant(provider="gcp", identity={"project_id": "fixture-project", "region": "us-central1",
                  "service_account": "fixture@fixture-project.iam.gserviceaccount.com", "credentials_file": str(path)},
                  adapters=["cloud-run"], resource_types=["application_revision"], actions=["update"], max_changes=1)
    if change:
        with pytest.raises(Refused) as refusal:
            credentials(grant)
        assert "synthetic-unusable-private-key" not in str(refusal.value)
    else:
        values = credentials(grant)
        assert values["GOOGLE_APPLICATION_CREDENTIALS"] == str(path)
        assert values["CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE"] == str(path)
        assert "synthetic-unusable-private-key" not in json.dumps(values)


# A remote preview may exist even when preparation stopped before producing approvable evidence
def test_incomplete_provider_preview_can_be_inspected_without_replay(local_cloud):
    engine, provider, _, _, _ = local_cloud
    provider.fail_preview = True
    release = engine.advance(engine.create("development")["id"])
    assert release["status"] == "uncertain"
    assert "artifact_digest" not in release["nodes"]["foundation"]
    assert provider.writes == ["create-change-set"]
    observed = engine.provider_status(release["id"], "foundation")
    assert observed["status"] == "REVIEW_IN_PROGRESS"
    assert provider.writes == ["create-change-set"]
    assert engine.ledger.events(release["id"])[-1]["name"] == "provider.observed"
    with pytest.raises(Refused):
        engine.provider_status(release["id"], "foundation", discard=True)
    assert provider.writes == ["create-change-set"]
