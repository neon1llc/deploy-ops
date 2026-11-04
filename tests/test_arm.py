# tests/test_arm.py - Check frozen Azure predictions, identity boundaries, and uncertain submissions.


# Provider responses are fixtures; these checks do not contact Azure
import copy
import json
from types import SimpleNamespace

import pytest

from deploy_ops.delivery.adapters import arm
from deploy_ops.delivery.adapters.base import Context
from deploy_ops.errors import Refused


# Fixed public identities keep assertions independent of local cloud configuration
SUBSCRIPTION = "11111111-1111-1111-1111-111111111111"
TENANT = "22222222-2222-2222-2222-222222222222"
RESOURCE = f"/subscriptions/{SUBSCRIPTION}/resourceGroups/test/providers/Microsoft.Storage/storageAccounts/sample"
ACCOUNT = {"id": SUBSCRIPTION, "tenantId": TENANT, "environmentName": "AzureCloud", "state": "Enabled",
           "user": {"name": "fixture-principal", "type": "servicePrincipal"}}
VERSION = {"azure-cli": "2.76.0"}
TEMPLATE = {"$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentTemplate.json#",
            "contentVersion": "1.0.0.0", "resources": [],
            "parameters": {"name": {"type": "string"}},
            "outputs": {"name": {"type": "string", "value": "[parameters('name')]"}}}
PREDICTION = {"status": "Succeeded", "changes": [{"resourceId": RESOURCE, "changeType": "Create",
               "after": {"type": "Microsoft.Storage/storageAccounts", "name": "sample"}}]}


# Fail loudly when adapter behavior diverges from the expected provider conversation
class Commands:
    # Retain exact arguments without inheriting any machine credentials
    def __init__(self, replies):
        self.replies = iter(replies)
        self.calls = []


    # Return synthetic responses or simulate a lost provider response
    def run(self, argv, cwd, timeout, capture, input_text):
        self.calls.append(argv)
        assert capture
        reply = next(self.replies)
        if isinstance(reply, Exception):
            raise reply
        return json.dumps(reply)


# Model the shared adapter context without requiring registry changes in this unit
def context(tmp_path, replies, adapter="arm", template=None, **options):
    source = tmp_path / "source"
    source.mkdir()
    stage = tmp_path / "artifacts"
    stage.mkdir()
    (source / "main.json").write_text(json.dumps(template or TEMPLATE))
    settings = {"subscription_id": SUBSCRIPTION, "tenant_id": TENANT, "resource_group": "test",
                "template": "main.json", "parameters": {"name": "sample"}, **options}
    component = SimpleNamespace(adapter=adapter, kind="infrastructure", outputs=["name"], timeout_seconds=60)
    return Context(component, "infrastructure", "staging", "azure", source, stage, tmp_path / "deployed",
                   settings, Commands(replies), "release-one")


# Supply the named deployment that the adapter can later inspect independently
def deployment(ctx, state="Succeeded", output_type="String"):
    return {"id": arm.target(ctx)["deployment_id"], "properties": {"provisioningState": state,
            "outputs": {"name": {"type": output_type, "value": "sample"}}}}


# Exercise preview, fresh prediction, one write, readback and public output delivery
def test_reviewed_template_is_submitted_once_and_read_back(tmp_path):
    ctx = context(tmp_path, [])
    ctx.process = Commands([VERSION, ACCOUNT, PREDICTION, VERSION, ACCOUNT, PREDICTION, [], {},
                            VERSION, ACCOUNT, deployment(ctx)])
    prepared = arm.prepare(ctx)
    assert prepared["preview"]["manual_only"]
    assert arm.apply(ctx, prepared) == {"name": "sample"}
    create = [call for call in ctx.process.calls if call[1:4] == ["deployment", "group", "create"]]
    assert len(create) == 1
    assert create[0][create[0].index("--template-file") + 1] == str(ctx.stage / "arm-template.json")
    assert create[0][create[0].index("--mode") + 1] == "Incremental"
    assert arm.operation_path(ctx).parent != ctx.stage
    ctx.process = Commands([VERSION, ACCOUNT])
    with pytest.raises(Refused, match="already attempted"):
        arm.apply(ctx, prepared)
    assert not any(call[1:4] == ["deployment", "group", "create"] for call in ctx.process.calls)


# Never substitute a new prediction beneath an existing approval
def test_changed_what_if_stops_before_submission(tmp_path):
    changed = copy.deepcopy(PREDICTION)
    changed["changes"][0]["after"]["name"] = "different"
    ctx = context(tmp_path, [VERSION, ACCOUNT, PREDICTION, VERSION, ACCOUNT, changed])
    prepared = arm.prepare(ctx)
    with pytest.raises(Refused, match="what-if changed"):
        arm.apply(ctx, prepared)
    assert not arm.operation_path(ctx).exists()


# File tampering and target edits are rejected before further provider reads
@pytest.mark.parametrize("change", ["template", "parameters", "target"])
def test_reviewed_inputs_cannot_change(tmp_path, change):
    ctx = context(tmp_path, [VERSION, ACCOUNT, PREDICTION])
    prepared = arm.prepare(ctx)
    if change == "target":
        ctx.options["resource_group"] = "other"
    else:
        (ctx.stage / f"arm-{change}.json").write_text("{}")
    with pytest.raises(Refused, match="changed after review"):
        arm.apply(ctx, prepared)
    assert len(ctx.process.calls) == 3


# Tenant, subscription, cloud and principal are separate identity boundaries
@pytest.mark.parametrize("field,value", [("tenantId", SUBSCRIPTION), ("id", TENANT),
    ("environmentName", "AzureChinaCloud"), ("state", "Disabled")])
def test_wrong_identity_is_rejected_before_what_if(tmp_path, field, value):
    account = {**ACCOUNT, field: value}
    ctx = context(tmp_path, [VERSION, account])
    with pytest.raises(Refused, match="login does not match"):
        arm.prepare(ctx)
    assert len(ctx.process.calls) == 2


# A principal change cannot borrow a previous identity's approval
def test_principal_change_blocks_apply(tmp_path):
    account = {**ACCOUNT, "user": {"name": "different", "type": "servicePrincipal"}}
    ctx = context(tmp_path, [VERSION, ACCOUNT, PREDICTION, VERSION, account])
    prepared = arm.prepare(ctx)
    with pytest.raises(Refused, match="principal changed"):
        arm.apply(ctx, prepared)


# Unknown analysis is not silently represented as a safe no-op
@pytest.mark.parametrize("kind", ["Delete", "Ignore", "Deploy", "Unsupported", "NewFutureKind"])
def test_incomplete_or_destructive_predictions_are_refused(tmp_path, kind):
    prediction = copy.deepcopy(PREDICTION)
    prediction["changes"][0]["changeType"] = kind
    ctx = context(tmp_path, [VERSION, ACCOUNT, prediction])
    with pytest.raises(Refused, match="unresolved changes"):
        arm.prepare(ctx)


# Successful HTTP status cannot hide incomplete resource analysis
@pytest.mark.parametrize("field", ["diagnostics", "potentialChanges", "nextLink"])
def test_prediction_diagnostics_are_not_discarded(tmp_path, field):
    prediction = {**PREDICTION, field: ["incomplete"]}
    ctx = context(tmp_path, [VERSION, ACCOUNT, prediction])
    with pytest.raises(Refused, match="incomplete or failed"):
        arm.prepare(ctx)


# Neither templates nor provider predictions may expand the reviewed scope
@pytest.mark.parametrize("kind", ["Microsoft.Authorization/roleAssignments", "Microsoft.Resources/deployments",
                                 "Microsoft.Resources/deploymentScripts"])
def test_access_changes_and_executable_templates_are_refused_offline(tmp_path, kind):
    template = {**TEMPLATE, "resources": [{"type": kind, "name": "example"}]}
    ctx = context(tmp_path, [], template=template)
    with pytest.raises(Refused, match="outside this ARM route"):
        arm.prepare(ctx)
    assert ctx.process.calls == []


# Secrets cannot enter the frozen parameter artifact through a secure declaration
def test_secure_parameters_and_outputs_are_not_public_artifacts(tmp_path):
    template = copy.deepcopy(TEMPLATE)
    template["parameters"]["name"]["type"] = "secureString"
    ctx = context(tmp_path, [], template=template)
    with pytest.raises(Refused, match="secret-reference"):
        arm.prepare(ctx)
    assert not (ctx.stage / "arm-parameters.json").exists()
    template["parameters"]["name"]["type"] = "string"
    template["outputs"]["name"]["type"] = "secureString"
    with pytest.raises(Refused, match="non-secret scalar"):
        arm.validate_template(template, ["name"])


# A lost response leaves durable identity for readback and never retries the write
def test_uncertain_submission_can_be_inspected_without_replay(tmp_path):
    ctx = context(tmp_path, [VERSION, ACCOUNT, PREDICTION, VERSION, ACCOUNT, PREDICTION, [],
                            Refused("Command deadline reached")])
    prepared = arm.prepare(ctx)
    with pytest.raises(Refused, match="deadline"):
        arm.apply(ctx, prepared)
    assert json.loads(arm.operation_path(ctx).read_text())["status"] == "submitting"
    ctx.process = Commands([VERSION, ACCOUNT, deployment(ctx)])
    observed = arm.inspect(ctx, prepared)
    assert observed["status"] == "succeeded"
    assert observed["outputs"] == {"name": "sample"}
    assert all(call[1:4] != ["deployment", "group", "create"] for call in ctx.process.calls)


# Provider failure and in-flight status remain distinct from successful delivery
@pytest.mark.parametrize("state,expected", [("Failed", "failed"), ("Canceled", "canceled"), ("Running", "unknown")])
def test_reconciliation_preserves_remote_outcome(tmp_path, state, expected):
    ctx = context(tmp_path, [VERSION, ACCOUNT, PREDICTION])
    prepared = arm.prepare(ctx)
    arm.record(ctx, prepared, "submitting")
    ctx.process = Commands([VERSION, ACCOUNT, deployment(ctx, state)])
    assert arm.inspect(ctx, prepared)["status"] == expected


# Both native infrastructure routes report only local absence before submission
@pytest.mark.parametrize("adapter", ["arm", "bicep"])
def test_pending_status_never_claims_remote_absence(tmp_path, adapter):
    ctx = context(tmp_path, [], adapter=adapter)
    observed = arm.inspect_pending(ctx)
    assert observed["status"] == "no-submission-recorded"
    assert observed["target"] == arm.target(ctx)
    assert observed["automatic_retry"] is False
    assert observed["resource_state"] == "unverified"
    assert ctx.process.calls == []


# Even an unreadable or dangling submission marker prevents the missing-record path
@pytest.mark.parametrize("kind", ["file", "directory", "dangling-symlink"])
def test_pending_record_cannot_bypass_reviewed_evidence(tmp_path, kind):
    ctx = context(tmp_path, [])
    path = arm.operation_path(ctx)
    if kind == "file":
        path.write_text("secret-invalid-provider-body")
    elif kind == "directory":
        path.mkdir()
    else:
        path.symlink_to(tmp_path / "missing-record")
    with pytest.raises(Refused, match="submission record exists without reviewed evidence"):
        arm.inspect_pending(ctx)
    assert ctx.process.calls == []


# Deployment history cleanup is never confused with deleting deployed resources
@pytest.mark.parametrize("options", [{"mode": "Complete"}, {"operation": "destroy"}])
def test_destructive_modes_have_no_implicit_implementation(tmp_path, options):
    ctx = context(tmp_path, [], **options)
    with pytest.raises(Refused):
        arm.prepare(ctx)
    assert ctx.process.calls == []


# Native Bicep compiler output is deployed without executing mutable source again
def test_bicep_uses_frozen_native_output_without_compiling_in_writer(tmp_path):
    template = {**TEMPLATE, "metadata": {"_generator": {"name": "bicep", "version": "0.41.2.0", "templateHash": "12345"}}}
    ctx = context(tmp_path, [VERSION, ACCOUNT, PREDICTION], adapter="bicep", template=template)
    prepared = arm.prepare(ctx)
    assert prepared["compiler_version"] == "0.41.2.0"
    assert all(call[0] == "az" and "bicep" not in call for call in ctx.process.calls)


# Require explicit compiler metadata before selecting the Bicep path
def test_bicep_requires_compiler_metadata(tmp_path):
    ctx = context(tmp_path, [], adapter="bicep")
    with pytest.raises(Refused, match="compiler metadata"):
        arm.prepare(ctx)
    assert ctx.process.calls == []
