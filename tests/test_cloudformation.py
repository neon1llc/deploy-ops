# tests/test_cloudformation.py - Exercise reviewed stack changes, stale evidence, and owned cleanup.


import json
from copy import deepcopy

import pytest

from deploy_ops.delivery.adapters import cloudformation as cfn
from deploy_ops.delivery.adapters.base import Context
from deploy_ops.delivery.spec import Component
from deploy_ops.errors import Refused


# Keep the fake account and provider objects deterministic
ACCOUNT = "123456789012"
STACK = f"arn:aws:cloudformation:us-east-1:{ACCOUNT}:stack/example/stack-id"
CHANGE = f"arn:aws:cloudformation:us-east-1:{ACCOUNT}:changeSet/example/change-id"
TEMPLATE = {"Resources": {"Registry": {"Type": "AWS::ECR::Repository", "DeletionPolicy": "Retain"}},
            "Outputs": {"ImageRepository": {"Value": "registry.example/test"}}}


# Model provider state transitions without credentials or network access
class Aws:
    # Preserve request bodies to verify the artifact sent to the native API
    def __init__(self, existing=False, no_change=False):
        self.calls = []
        self.heartbeat = lambda: None
        self.account = ACCOUNT
        self.no_change = no_change
        self.current = {"StackId": STACK, "StackName": "example", "StackStatus": "CREATE_COMPLETE",
                        "CreationTime": "2026-01-01", "Tags": [], "Outputs": [
                            {"OutputKey": "ImageRepository", "OutputValue": "registry.example/test"}]} if existing else None
        self.change = None
        self.events = []
        self.template = deepcopy(TEMPLATE)
        self.objects = {}


    # Return only the native operation shapes consumed by the adapter
    def run(self, argv, cwd, timeout, capture, input_text):
        self.calls.append(argv)
        if argv == ["aws", "--version"]:
            return "aws-cli/2.38.1 Python/3.13 Linux/6.0"
        service, operation = argv[1:3]
        if service == "sts":
            return json.dumps({"Account": self.account, "Arn": f"arn:aws:iam::{self.account}:user/test"})
        if service == "s3api":
            key = argv[argv.index("--key") + 1] if "--key" in argv else None
            if operation == "list-objects-v2":
                result = {"Contents": [{"Key": name} for name in self.objects]}
            elif operation == "put-object":
                assert "--if-none-match" in argv
                self.objects[key] = argv[argv.index("--checksum-sha256") + 1]
                result = {}
            elif operation == "head-object":
                result = {"ChecksumSHA256": self.objects[key]}
            else:
                raise AssertionError(argv)
            return json.dumps(result)
        if operation == "list-stacks":
            result = {"StackSummaries": [self.current] if self.current else []}
        elif operation == "describe-stacks":
            result = {"Stacks": [self.current]}
        elif operation == "create-change-set":
            path = argv[argv.index("--cli-input-json") + 1].removeprefix("file://")
            self.request = json.load(open(path))
            self.template = json.loads(self.request["TemplateBody"])
            if self.current is None:
                self.current = {"StackId": STACK, "StackName": "example", "StackStatus": "REVIEW_IN_PROGRESS",
                                "CreationTime": "2026-01-01", "Tags": self.request["Tags"], "Outputs": [
                                    {"OutputKey": "ImageRepository", "OutputValue": "registry.example/test"}]}
            self.change = {"ChangeSetId": CHANGE, "ChangeSetName": self.request["ChangeSetName"], "StackId": STACK, "ChangeSetType": self.request["ChangeSetType"],
                           "Status": "FAILED" if self.no_change else "CREATE_COMPLETE",
                           "ExecutionStatus": "UNAVAILABLE" if self.no_change else "AVAILABLE",
                           "StatusReason": "The submitted information didn't contain changes." if self.no_change else "",
                           "Changes": [] if self.no_change else [{"ResourceChange": {
                               "Action": "Add" if self.request["ChangeSetType"] == "CREATE" else "Modify",
                               "LogicalResourceId": "Registry", "ResourceType": "AWS::ECR::Repository"}}]}
            result = {"Id": CHANGE, "StackId": STACK}
        elif operation == "describe-change-set":
            result = self.change
        elif operation == "execute-change-set":
            assert argv[argv.index("--change-set-name") + 1] == CHANGE
            self.current["StackStatus"] = "CREATE_COMPLETE" if self.request["ChangeSetType"] == "CREATE" else "UPDATE_COMPLETE"
            self.current["LastUpdatedTime"] = "2026-01-02"
            self.events = [{"ResourceType": "AWS::CloudFormation::Stack", "ResourceStatus": self.current["StackStatus"],
                            "ClientRequestToken": argv[argv.index("--client-request-token") + 1]}]
            result = {}
        elif operation == "describe-stack-events":
            result = {"StackEvents": self.events}
        elif operation == "get-template":
            result = {"TemplateBody": self.template}
        elif operation == "list-stack-resources" and self.current["StackStatus"] == "REVIEW_IN_PROGRESS":
            result = {"StackResourceSummaries": []}
        elif operation == "list-stack-resources":
            result = {"StackResourceSummaries": [{"LogicalResourceId": "Registry", "ResourceType": "AWS::ECR::Repository",
                                                 "PhysicalResourceId": "registry-id", "ResourceStatus": "CREATE_COMPLETE"}]}
        elif operation == "delete-stack":
            self.current["StackStatus"] = "DELETE_COMPLETE"
            self.events = [{"ResourceType": "AWS::CloudFormation::Stack", "ResourceStatus": "DELETE_COMPLETE",
                            "ClientRequestToken": argv[argv.index("--client-request-token") + 1]}]
            result = {}
        elif operation == "list-change-sets":
            result = {"Summaries": [] if self.change["Status"] == "DELETE_COMPLETE" else [self.change]}
        elif operation == "delete-change-set":
            self.change["Status"] = "DELETE_COMPLETE"
            result = {}
        else:
            raise AssertionError(argv)
        return json.dumps(result)


# Use a source snapshot and a separate immutable artifact directory
def context(tmp_path, process=None, options=None):
    source = tmp_path / "source"
    source.mkdir(exist_ok=True)
    (source / "template.json").write_text(json.dumps(TEMPLATE))
    stage = tmp_path / "release" / "artifacts"
    stage.mkdir(parents=True, exist_ok=True)
    component = Component(repository="infrastructure", kind="infrastructure", adapter="terraform",
                          outputs=["ImageRepository"]).model_copy(update={"adapter": "cloudformation"})
    return Context(component, "infrastructure", "test", "aws", source, stage, tmp_path / "deployed",
                   {"account_id": ACCOUNT, "region": "us-east-1", "stack_name": "example", "template": "template.json",
                    **(options or {})}, process or Aws(), "release-one")


# Complete create, public output reuse, owned deletion, and retention review
def test_create_output_and_owned_destroy(tmp_path):
    ctx = context(tmp_path)
    prepared = cfn.prepare(ctx)
    assert prepared["preview"]["changes"][0]["action"] == "create"
    assert cfn.apply(ctx, prepared) == {"ImageRepository": "registry.example/test"}
    assert cfn.owner_path(ctx).is_file()
    ctx.options["operation"] = "destroy"
    ctx.release_id = "release-two"
    ctx.stage = tmp_path / "cleanup" / "artifacts"
    ctx.stage.mkdir(parents=True)
    removal = cfn.prepare(ctx)
    assert removal["retained"] == ["Registry"]
    assert removal["preview"]["findings"] == []
    assert removal["warnings"]
    assert cfn.apply(ctx, removal)["status"] == "DELETE_COMPLETE"
    assert "--force-delete-stack" not in str(ctx.process.calls)


# A no-change result exports existing outputs without executing a failed change set
def test_update_no_change_and_discard(tmp_path):
    ctx = context(tmp_path, Aws(existing=True, no_change=True))
    prepared = cfn.prepare(ctx)
    assert prepared["no_change"]
    assert cfn.apply(ctx, prepared)["ImageRepository"]
    assert not any("execute-change-set" in call for call in ctx.process.calls)
    assert cfn.discard(ctx, prepared)["status"] == "discarded"


# Stale evidence and wrong identities must never reach a cloud writer
@pytest.mark.parametrize("mutation", ["identity", "state", "change", "template"])
def test_changed_approval_refuses_execution(tmp_path, mutation):
    ctx = context(tmp_path)
    prepared = cfn.prepare(ctx)
    if mutation == "identity":
        ctx.process.account = "999999999999"
    elif mutation == "state":
        ctx.process.current["LastUpdatedTime"] = "outside-change"
    elif mutation == "change":
        ctx.process.change["ExecutionStatus"] = "OBSOLETE"
    else:
        (ctx.stage / "cloudformation-template.json").write_text("{}")
    with pytest.raises(Refused):
        cfn.apply(ctx, prepared)
    assert not any("execute-change-set" in call for call in ctx.process.calls)


# Never infer cleanup ownership from a matching configured name
def test_preexisting_stack_cannot_be_destroyed(tmp_path):
    ctx = context(tmp_path, Aws(existing=True), {"operation": "destroy"})
    with pytest.raises(Refused, match="ownership"):
        cfn.prepare(ctx)
    assert not any("delete-stack" in call for call in ctx.process.calls)


# Interrupted calls leave a durable intent and require readback before any replay
def test_uncertain_execution_keeps_operation_id(tmp_path):
    ctx = context(tmp_path)
    prepared = cfn.prepare(ctx)
    run = ctx.process.run

    # Simulate a network failure after the provider has accepted the operation
    def interrupted(argv, *args):
        result = run(argv, *args)
        if "execute-change-set" in argv:
            raise Refused("transport lost")
        return result

    ctx.process.run = interrupted
    with pytest.raises(Refused, match="transport"):
        cfn.apply(ctx, prepared)
    journal = json.loads((ctx.stage.parent / "cloudformation-operation.json").read_text())
    assert journal["token"] == prepared["token"] and journal["status"] == "unknown"
    assert cfn.inspect(ctx, prepared)["status"] == "CREATE_COMPLETE"
    with pytest.raises(Refused):
        cfn.apply(ctx, prepared)
    assert sum("execute-change-set" in call for call in ctx.process.calls) == 1


# Treat replacement as a review warning while preserving the controller's action boundary
def test_replacement_is_explicit(tmp_path):
    value = {"Changes": [{"ResourceChange": {"Action": "Modify", "Replacement": "Conditional",
             "LogicalResourceId": "Database", "ResourceType": "AWS::RDS::DBInstance"}}]}
    assert cfn.changes(value)["changes"][0]["action"] == "replace"


# Executable templates and secret output bindings fail before remote creation
@pytest.mark.parametrize("value", [
    {"Transform": "AWS::Serverless-2016-10-31", "Resources": {}},
    {"Resources": {"Code": {"Type": "Custom::Code"}}},
    {"Resources": {}, "Parameters": {"Password": {"Type": "String", "NoEcho": True}},
     "Outputs": {"ImageRepository": {"Value": {"Ref": "Password"}}}},
])
def test_unsafe_template_features_refused(tmp_path, value):
    ctx = context(tmp_path)
    (ctx.source / "template.json").write_text(json.dumps(value))
    with pytest.raises(Refused):
        cfn.prepare(ctx)
    assert not any("create-change-set" in call for call in ctx.process.calls)


# Parse only CloudFormation YAML tags while retaining native short-form semantics
def test_yaml_intrinsics_are_data():
    value = cfn.template("Resources:\n  Bucket:\n    Type: AWS::S3::Bucket\nOutputs:\n  Arn:\n    Value: !GetAtt Bucket.Arn\n")
    assert value["Outputs"]["Arn"]["Value"] == {"Fn::GetAtt": ["Bucket", "Arn"]}


# Recover a lost creation response using the original token and matching provider ownership
def test_failed_preview_readback_recovers_owned_shell(tmp_path):
    ctx = context(tmp_path)
    run = ctx.process.run

    # Lose the acknowledgement after AWS has created only its empty review shell
    def interrupted(argv, *args):
        result = run(argv, *args)
        if "create-change-set" in argv:
            raise Refused("transport lost")
        return result

    ctx.process.run = interrupted
    with pytest.raises(Refused, match="transport"):
        cfn.prepare(ctx)
    assert not cfn.owner_path(ctx).exists()
    assert cfn.inspect_pending(ctx)["status"] == "REVIEW_IN_PROGRESS"
    assert cfn.owner_path(ctx).exists()
    ctx.options["operation"] = "destroy"
    removal = cfn.prepare(ctx)
    assert removal["review_shell"] and not removal["preview"]["changes"]
    assert cfn.apply(ctx, removal)["status"] == "DELETE_COMPLETE"


# Refuse public substitutions that would reveal a NoEcho parameter
def test_noecho_substitution_never_becomes_public_output(tmp_path):
    ctx = context(tmp_path)
    value = {"Resources": {}, "Parameters": {"Password": {"Type": "String", "NoEcho": True}},
             "Outputs": {"ImageRepository": {"Value": {"Fn::Sub": "${Password}"}}}}
    with pytest.raises(Refused, match="secret"):
        cfn.check_template(ctx, value)


# Bind CLI parsing and request semantics to the reviewed native version
def test_changed_cli_version_refuses_execution(tmp_path):
    ctx = context(tmp_path)
    prepared = cfn.prepare(ctx)
    run = ctx.process.run

    # Substitute a different installed release without changing any cloud evidence
    def newer(argv, *args):
        return "aws-cli/2.39.0" if argv == ["aws", "--version"] else run(argv, *args)

    ctx.process.run = newer
    with pytest.raises(Refused, match="CLI version"):
        cfn.apply(ctx, prepared)
    assert not any("execute-change-set" in call for call in ctx.process.calls)


# A provider rollback remains a failed delivery even when the stack is idle again
def test_rollback_result_is_not_success(tmp_path):
    ctx = context(tmp_path)
    prepared = cfn.prepare(ctx)
    run = ctx.process.run

    # Simulate a failed create followed by the provider's automatic rollback
    def rolled_back(argv, *args):
        result = run(argv, *args)
        if "execute-change-set" in argv:
            ctx.process.current["StackStatus"] = "ROLLBACK_COMPLETE"
        return result

    ctx.process.run = rolled_back
    with pytest.raises(Refused, match="rolled back"):
        cfn.apply(ctx, prepared)
    assert json.loads((ctx.stage.parent / "cloudformation-operation.json").read_text())["status"] == "failed"
