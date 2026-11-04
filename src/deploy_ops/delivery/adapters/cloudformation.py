# src/deploy_ops/delivery/adapters/cloudformation.py - Review and execute exact CloudFormation change sets.


import json
import re
import time
from pathlib import Path

from ...client import file_digest, private_json
from ...config import fingerprint
from ...errors import Refused
from ..files import inside
from .base import Context, need


# Keep remote state and executable template features explicit
READY = {"CREATE_COMPLETE", "UPDATE_COMPLETE", "UPDATE_ROLLBACK_COMPLETE"}
CAPABILITIES = {"CAPABILITY_IAM", "CAPABILITY_NAMED_IAM"}
OWNER_TAG = "DeployOpsOwner"
STACK_FIELDS = ("StackId", "StackStatus", "CreationTime", "LastUpdatedTime", "Parameters",
                "RoleARN", "Tags", "EnableTerminationProtection")
CHANGE_FIELDS = ("ChangeSetId", "StackId", "ChangeSetType", "Status", "ExecutionStatus",
                 "Parameters", "Capabilities", "Changes", "RoleARN", "Tags", "IncludeNestedStacks")


# Select a region and optional named profile without shell interpolation
def aws(context: Context, service: str, *parts: str) -> list[str]:
    command = ["aws", service, *parts, "--region", need(context.options, "region"),
               "--output", "json", "--no-cli-pager", "--no-cli-auto-prompt"]
    if context.options.get("profile"):
        command.extend(["--profile", need(context.options, "profile")])
    return command


# Bind the exact native CLI release that interpreted the reviewed requests
def tool_version(context: Context) -> str:
    value = context.command(["aws", "--version"], capture=True).split()[0]
    if not re.fullmatch(r"aws-cli/2\.[0-9]+\.[0-9]+", value):
        raise Refused("CloudFormation requires an identifiable AWS CLI v2 release", 422)
    return value


# Capture provider responses privately instead of echoing values to events
def call(context: Context, operation: str, *parts: str) -> dict:
    value = json.loads(context.command(aws(context, "cloudformation", operation, *parts), capture=True) or "{}")
    if not isinstance(value, dict) or value.get("NextToken"):
        raise Refused("CloudFormation returned incomplete or malformed evidence")
    return value


# Refuse the wrong account before any stack operation
def identity(context: Context) -> dict:
    if context.provider != "aws" or context.component.kind != "infrastructure":
        raise Refused("CloudFormation requires an AWS infrastructure component", 422)
    account = need(context.options, "account_id")
    if not re.fullmatch(r"\d{12}", account):
        raise Refused("account_id must contain twelve digits", 422)
    result = json.loads(context.command(aws(context, "sts", "get-caller-identity"), capture=True))
    if result.get("Account") != account or result.get("Arn", "").endswith(":root"):
        raise Refused("AWS identity does not match the reviewed non-root account", 403)
    expected = context.options.get("principal_arn")
    if expected and result.get("Arn") != expected:
        raise Refused("AWS principal differs from the configured identity", 403)
    return {"account_id": account, "region": context.options["region"]}


# Support native JSON and safe YAML intrinsics without executing constructors
def template(text: str) -> dict:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        try:
            import yaml
        except ImportError as error:
            raise Refused("YAML templates require PyYAML; JSON templates need no extra parser", 422) from error

        # CloudFormation tags are data, never Python object constructors
        class Loader(yaml.SafeLoader):
            yaml_implicit_resolvers = {
                key: [(tag, pattern) for tag, pattern in values if tag != "tag:yaml.org,2002:timestamp"]
                for key, values in yaml.SafeLoader.yaml_implicit_resolvers.items()
            }

        # Preserve intrinsic names for semantic inspection and canonical JSON
        def intrinsic(loader, tag, node):
            allowed = {"Ref", "Condition", "Base64", "Cidr", "FindInMap", "GetAtt", "GetAZs",
                       "ImportValue", "Join", "Select", "Split", "Sub", "Transform", "And",
                       "Equals", "If", "Not", "Or", "Length", "ToJsonString"}
            if tag not in allowed:
                raise Refused("Unsupported CloudFormation YAML tag", 422)
            if isinstance(node, yaml.ScalarNode):
                item = loader.construct_scalar(node)
            elif isinstance(node, yaml.SequenceNode):
                item = loader.construct_sequence(node)
            else:
                item = loader.construct_mapping(node)
            if tag == "GetAtt" and isinstance(item, str):
                item = item.split(".", 1)
            return {tag if tag in {"Ref", "Condition"} else "Fn::" + tag: item}

        Loader.add_multi_constructor("!", intrinsic)
        try:
            value = yaml.load(text, Loader=Loader)
        except yaml.YAMLError as error:
            raise Refused("Invalid CloudFormation template", 422) from error
    if not isinstance(value, dict) or not isinstance(value.get("Resources"), dict):
        raise Refused("A CloudFormation template needs a Resources mapping", 422)
    return value


# Reject executable macros and hidden outputs before submitting a preview
def check_template(context: Context, value: dict) -> None:
    encoded = json.dumps(value)
    resources = value["Resources"]
    if value.get("Transform") or '"Fn::Transform"' in encoded:
        raise Refused("Macros and transforms need a separately reviewed execution path", 422)
    if any(not isinstance(item, dict) or not isinstance(item.get("Type"), str) for item in resources.values()):
        raise Refused("Every resource must declare a CloudFormation type", 422)
    if any(item["Type"].startswith("Custom::") or item["Type"] in {
        "AWS::CloudFormation::CustomResource", "AWS::CloudFormation::Stack"
    } for item in resources.values()):
        raise Refused("Custom resources and nested stacks require a separately reviewed execution path", 422)
    parameters = value.get("Parameters", {})
    if not isinstance(parameters, dict):
        raise Refused("Template parameters must be a mapping", 422)
    hidden = {name for name, item in parameters.items() if isinstance(item, dict) and item.get("NoEcho")}
    if hidden & set(context.options.get("parameters", {})):
        raise Refused("NoEcho values must not be stored in workspace parameters", 422)
    outputs = value.get("Outputs", {})
    for name in context.component.outputs:
        output = json.dumps(outputs.get(name))
        if name not in outputs or "{{resolve:" in output or any(key in output for key in hidden):
            raise Refused("Selected outputs must exist and must not expose secret references", 422)


# Bound public maps before converting them to native request shapes
def public_map(options: dict, key: str) -> dict[str, str]:
    values = options.get(key, {})
    if not isinstance(values, dict) or len(values) > 100 or any(
        not isinstance(name, str) or not name or not isinstance(value, (str, int, bool))
        or len(str(value)) > 4096 for name, value in values.items()
    ):
        raise Refused(f"{key} must be a small map of public scalar values", 422)
    return {name: str(value).lower() if isinstance(value, bool) else str(value) for name, value in values.items()}


# Distinguish a missing stack from an authentication or transport failure
def lookup(context: Context) -> dict | None:
    name = need(context.options, "stack_name")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{0,127}", name):
        raise Refused("stack_name must be a CloudFormation stack name", 422)
    summaries = call(context, "list-stacks").get("StackSummaries", [])
    matches = [item for item in summaries if item.get("StackName") == name and item.get("StackStatus") != "DELETE_COMPLETE"]
    if len(matches) > 1:
        raise Refused("CloudFormation returned ambiguous stack identity")
    return stack(context, matches[0]["StackId"]) if matches else None


# Bind later actions to the immutable provider stack ID
def stack(context: Context, stack_id: str) -> dict:
    entries = call(context, "describe-stacks", "--stack-name", stack_id).get("Stacks", [])
    if len(entries) != 1 or entries[0].get("StackId") != stack_id:
        raise Refused("CloudFormation stack identity could not be verified")
    return entries[0]


# Fingerprint relevant state without publishing parameters or outputs
def state_digest(value: dict) -> str:
    return fingerprint({key: value.get(key) for key in STACK_FIELDS})


# Keep recovery writes outside the approved immutable artifact directory
def record(context: Context, **values) -> None:
    path = context.stage.parent / "cloudformation-operation.json"
    current = json.loads(path.read_text()) if path.exists() else {}
    private_json(path, {**current, **values})


# Keep durable ownership separate from names and from mutable caller claims
def owner_path(context: Context) -> Path:
    return context.deployments / context.environment / context.name / "cloudformation-owner.json"


# Persist the stack ID immediately after creating its empty review shell
def save_owner(context: Context, stack_id: str, token: str) -> None:
    path = owner_path(context)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    private_json(path, {"stack_id": stack_id, "owner": token, "account_id": context.options["account_id"],
                        "region": context.options["region"], "created_by_release": context.release_id})


# Require both retained creation evidence and matching provider tags for deletion
def owned(context: Context, current: dict) -> dict:
    path = owner_path(context)
    if not path.is_file():
        raise Refused("Stack deletion requires retained DeployOps creation ownership")
    value = json.loads(path.read_text())
    tags = {item["Key"]: item["Value"] for item in current.get("Tags", [])}
    if value.get("stack_id") != current["StackId"] or tags.get(OWNER_TAG) != value.get("owner") or any(
        value.get(key) != context.options.get(key) for key in ("account_id", "region")
    ):
        raise Refused("Stack ownership evidence does not match the provider")
    return value


# Poll briefly while preserving authority heartbeats and a single bounded deadline
def pause(context: Context, deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise Refused("CloudFormation outcome is unknown after the deadline; inspect before retrying")
    context.process.heartbeat()
    time.sleep(min(2, max(0, deadline - time.monotonic())))


# Reduce resource impact without storing potentially secret property values
def changes(value: dict) -> dict:
    result = []
    for item in value.get("Changes", []):
        resource = item.get("ResourceChange", {})
        action = {"Add": "create", "Modify": "update", "Remove": "delete", "Import": "import"}.get(resource.get("Action"))
        if not action or not resource.get("LogicalResourceId") or not resource.get("ResourceType"):
            raise Refused("CloudFormation returned an unsupported resource change")
        if resource.get("Replacement") in {"True", "Conditional"}:
            action = "replace"
        result.append({"address": resource["LogicalResourceId"], "resource_type": resource["ResourceType"], "action": action})
    return {"changes": result, "findings": [], "manual_only": True}


# Snapshot the complete change set with automatic CLI pagination left enabled
def change_set(context: Context, identifier: str) -> dict:
    return call(context, "describe-change-set", "--change-set-name", identifier, "--no-include-property-values")


# Prepare deployment or owned deletion without implicitly executing either
def prepare(context: Context, frozen_template: Path | None = None) -> dict:
    version = tool_version(context)
    scope = identity(context)
    operation = context.options.get("operation", "deploy")
    if operation not in {"deploy", "destroy"}:
        raise Refused("CloudFormation operation must be deploy or destroy", 422)
    current = lookup(context)
    if operation == "destroy":
        return {**prepare_destroy(context, current, scope), "tool_version": version}
    if current and current.get("StackStatus") not in READY:
        raise Refused("Existing stack needs reconciliation before a new change set")

    source = frozen_template or inside(context.source, need(context.options, "template"))
    text = source.read_text()
    if not 0 < len(text.encode()) <= 51200:
        raise Refused("Inline templates must contain at most 51,200 bytes", 422)
    value = template(text)
    check_template(context, value)
    if len(json.dumps(value).encode()) > 51200:
        raise Refused("Canonical CloudFormation template exceeds the inline API limit", 422)
    frozen = context.stage / "cloudformation-template.json"
    private_json(frozen, value)
    capabilities = context.options.get("capabilities", [])
    if not isinstance(capabilities, list) or set(capabilities) - CAPABILITIES:
        raise Refused("Declare only explicitly reviewed IAM capabilities", 422)
    parameters = public_map(context.options, "parameters")
    tags = public_map(context.options, "tags")
    if OWNER_TAG in tags:
        raise Refused("DeployOps ownership tags are reserved", 422)
    previous_tags = {item["Key"]: item["Value"] for item in (current or {}).get("Tags", [])}
    token = "deploy-ops-" + fingerprint([context.release_id, context.name])[:40]
    tags = {**previous_tags, **tags}
    if current is None:
        tags[OWNER_TAG] = token

    # Persist the deterministic request identity before the first remote mutation
    request = {"StackName": current["StackId"] if current else context.options["stack_name"],
               "TemplateBody": json.dumps(value), "ChangeSetName": token, "ClientToken": token,
               "ChangeSetType": "UPDATE" if current else "CREATE", "IncludeNestedStacks": False,
               "Parameters": [{"ParameterKey": key, "ParameterValue": val} for key, val in parameters.items()],
               "Tags": [{"Key": key, "Value": val} for key, val in tags.items()]}
    if capabilities:
        request["Capabilities"] = capabilities
    if context.options.get("role_arn"):
        request["RoleARN"] = need(context.options, "role_arn")
    request_path = context.stage / "cloudformation-request.json"
    private_json(request_path, request)
    record(context, action="create-change-set", token=token, stack_name=context.options["stack_name"], scope=scope,
           change_set_type=request["ChangeSetType"])
    created = call(context, "create-change-set", "--cli-input-json", "file://" + str(request_path))
    identifier, stack_id = created.get("Id"), created.get("StackId")
    if not identifier or not stack_id:
        raise Refused("CloudFormation did not return durable operation identities; inspect before retrying")
    record(context, change_set_id=identifier, stack_id=stack_id)
    if current is None:
        save_owner(context, stack_id, token)

    # A failed no-change preview is distinct from an invalid template or failed execution
    deadline = time.monotonic() + context.component.timeout_seconds
    while True:
        reviewed = change_set(context, identifier)
        status = reviewed.get("Status")
        if status in {"CREATE_COMPLETE", "FAILED"}:
            break
        if status not in {"CREATE_PENDING", "CREATE_IN_PROGRESS"}:
            raise Refused("CloudFormation change set entered an unexpected state")
        pause(context, deadline)
    reason = reviewed.get("StatusReason", "")
    no_change = bool(current and status == "FAILED" and ("didn't contain changes" in reason or "No updates are to be performed" in reason))
    if not no_change and (status != "CREATE_COMPLETE" or reviewed.get("ExecutionStatus") != "AVAILABLE"):
        record(context, status=status)
        raise Refused("CloudFormation could not prepare the change set; inspect its provider diagnostics")
    if reviewed.get("StackId") != stack_id or reviewed.get("ChangeSetId") != identifier:
        raise Refused("CloudFormation change-set identity changed")
    baseline = stack(context, stack_id)
    if current and state_digest(baseline) != state_digest(current):
        raise Refused("Stack changed while preparing the change set")
    if not current and baseline.get("StackStatus") != "REVIEW_IN_PROGRESS":
        raise Refused("New stack was executed outside this review")
    private_json(context.stage / "cloudformation-review.json", reviewed)
    result = {"operation": "deploy", "scope": scope, "tool_version": version, "stack_id": stack_id, "change_set_id": identifier,
              "change_set_type": request["ChangeSetType"], "token": token, "no_change": no_change,
              "baseline_digest": state_digest(baseline), "template_digest": file_digest(frozen),
              "change_set_digest": fingerprint({key: reviewed.get(key) for key in CHANGE_FIELDS}),
              "preview": changes(reviewed)}
    result["warnings"] = [f"{item['address']}: {item['action']} may remove data"
                          for item in result["preview"]["changes"] if item["action"] in {"delete", "replace"}]
    record(context, status="prepared", no_change=no_change)
    return result


# Inventory retained data and exact resource identities before asking for deletion approval
def prepare_destroy(context: Context, current: dict | None, scope: dict) -> dict:
    if not current or (current.get("StackStatus", "").endswith("IN_PROGRESS") and current["StackStatus"] != "REVIEW_IN_PROGRESS"):
        raise Refused("Owned stack must exist and be idle before deletion")
    ownership = owned(context, current)
    if current.get("EnableTerminationProtection"):
        raise Refused("Stack termination protection is enabled; this adapter never disables it")
    inventory = call(context, "list-stack-resources", "--stack-name", current["StackId"])["StackResourceSummaries"]
    review_shell = current["StackStatus"] == "REVIEW_IN_PROGRESS"
    if review_shell:
        if inventory:
            raise Refused("An unexecuted review shell unexpectedly contains resources")
        value = {"Resources": {}}
    else:
        raw = call(context, "get-template", "--stack-name", current["StackId"], "--template-stage", "Original")["TemplateBody"]
        value = template(raw) if isinstance(raw, str) else raw
    resources = value.get("Resources", {})
    retained = [name for name, item in resources.items() if item.get("DeletionPolicy") in {"Retain", "Snapshot", "RetainExceptOnCreate"}]
    preview = {"changes": [{"address": item["LogicalResourceId"], "resource_type": item["ResourceType"],
                            "action": "read" if item["LogicalResourceId"] in retained else "delete"} for item in inventory],
               "findings": [],
               "manual_only": True}
    private_json(context.stage / "cloudformation-delete-inventory.json", inventory)
    return {"operation": "destroy", "scope": scope, "stack_id": current["StackId"],
            "baseline_digest": state_digest(current), "inventory_digest": fingerprint(inventory),
            "delete_template_digest": fingerprint(value), "review_shell": review_shell,
            "owner": ownership["owner"], "retained": retained, "preview": preview,
            "warnings": ["Retained resources or snapshots remain billable: " + ", ".join(retained)] if retained else [],
            "token": "deploy-ops-delete-" + fingerprint([context.release_id, context.name])[:40]}


# Return selected public outputs only after the expected stack operation completes
def outputs(context: Context, current: dict) -> dict:
    available = {item["OutputKey"]: item.get("OutputValue") for item in current.get("Outputs", [])}
    result = {}
    for name in context.component.outputs:
        value = available.get(name)
        if not isinstance(value, str) or len(value) > 2000 or "{{resolve:" in value:
            raise Refused("A selected public CloudFormation output is missing or invalid")
        result[name] = value
    return result


# Expose safe events and state for failed or interrupted operations without replaying them
def inspect(context: Context, prepared: dict) -> dict:
    identity(context)
    stack_id = prepared.get("stack_id")
    if not stack_id:
        raise Refused("Inspect the saved request token in AWS before retrying an unconfirmed creation")
    current = stack(context, stack_id)
    events = call(context, "describe-stack-events", "--stack-name", stack_id).get("StackEvents", [])
    safe = [{key: item[key] for key in ("EventId", "LogicalResourceId", "ResourceType", "ResourceStatus", "Timestamp", "ClientRequestToken") if key in item}
            for item in events[:100]]
    result = {"stack_id": stack_id, "status": current.get("StackStatus", "UNKNOWN"), "events": safe}
    record(context, observed=result)
    return result


# Validate state before any approved provider or asset write
def validate_approved(context: Context, prepared: dict) -> dict:
    if tool_version(context) != prepared["tool_version"]:
        raise Refused("AWS CLI version changed after approval")
    if identity(context) != prepared["scope"]:
        raise Refused("CloudFormation target changed after approval")
    current = stack(context, prepared["stack_id"])
    if state_digest(current) != prepared["baseline_digest"]:
        raise Refused("CloudFormation stack changed after approval; prepare a new release")
    journal = context.stage.parent / "cloudformation-operation.json"
    prior = json.loads(journal.read_text()) if journal.exists() else {}
    if prior.get("action") in {"execute-change-set", "delete-stack"}:
        raise Refused("An operation was already submitted or became uncertain; inspect before retrying")
    if prepared["operation"] == "deploy":
        if file_digest(context.stage / "cloudformation-template.json") != prepared["template_digest"]:
            raise Refused("CloudFormation template changed after approval")
        reviewed = change_set(context, prepared["change_set_id"])
        if fingerprint({key: reviewed.get(key) for key in CHANGE_FIELDS}) != prepared["change_set_digest"]:
            raise Refused("CloudFormation change set changed after approval")
    return current


# Execute the approved provider object once and require observed convergence
def apply(context: Context, prepared: dict) -> dict:
    current = validate_approved(context, prepared)
    if prepared["operation"] == "destroy":
        ownership = owned(context, current)
        inventory = call(context, "list-stack-resources", "--stack-name", current["StackId"])["StackResourceSummaries"]
        if ownership["owner"] != prepared["owner"] or fingerprint(inventory) != prepared["inventory_digest"]:
            raise Refused("Deletion ownership or resource inventory changed after approval")
        if prepared["review_shell"]:
            value = {"Resources": {}}
        else:
            raw = call(context, "get-template", "--stack-name", current["StackId"], "--template-stage", "Original")["TemplateBody"]
            value = template(raw) if isinstance(raw, str) else raw
        if fingerprint(value) != prepared["delete_template_digest"] or state_digest(stack(context, current["StackId"])) != prepared["baseline_digest"]:
            raise Refused("Deletion template or stack state changed after approval")
        record(context, action="delete-stack", stack_id=current["StackId"], token=prepared["token"], status="unknown")
        call(context, "delete-stack", "--stack-name", current["StackId"], "--client-request-token", prepared["token"])
        expected = "DELETE_COMPLETE"
    else:
        if prepared["no_change"]:
            return outputs(context, current)
        record(context, action="execute-change-set", stack_id=current["StackId"], token=prepared["token"], status="unknown")
        call(context, "execute-change-set", "--change-set-name", prepared["change_set_id"],
             "--client-request-token", prepared["token"], "--no-disable-rollback")
        expected = "CREATE_COMPLETE" if prepared["change_set_type"] == "CREATE" else "UPDATE_COMPLETE"

    # A matching operation token prevents an old completed state from passing verification
    deadline = time.monotonic() + context.component.timeout_seconds
    while True:
        observed = inspect(context, prepared)
        status = observed["status"]
        matching = any(item.get("ClientRequestToken") == prepared["token"] and item.get("ResourceType") == "AWS::CloudFormation::Stack"
                       and item.get("ResourceStatus") == expected for item in observed["events"])
        if status == expected and matching:
            record(context, status="succeeded")
            if prepared["operation"] == "destroy":
                return {"stack_id": prepared["stack_id"], "status": expected,
                        "retained_resources": ", ".join(prepared["retained"])}
            return outputs(context, stack(context, prepared["stack_id"]))
        if status.endswith("FAILED") or status in {"ROLLBACK_COMPLETE", "UPDATE_ROLLBACK_COMPLETE"}:
            record(context, status="failed")
            raise Refused("CloudFormation failed or rolled back; inspect retained stack events")
        pause(context, deadline)


# Remove only the exact unexecuted preview after independently validating its identity
def discard(context: Context, prepared: dict) -> dict:
    if identity(context) != prepared["scope"] or prepared.get("operation") != "deploy":
        raise Refused("Only a matching deployment preview can be discarded")
    reviewed = change_set(context, prepared["change_set_id"])
    if reviewed.get("StackId") != prepared["stack_id"] or reviewed.get("ExecutionStatus") not in {"AVAILABLE", "UNAVAILABLE"}:
        raise Refused("Executed or uncertain change sets cannot be discarded")
    record(context, preview_discard_requested=True)
    call(context, "delete-change-set", "--change-set-name", prepared["change_set_id"])
    deadline = time.monotonic() + context.component.timeout_seconds
    while True:
        remaining = call(context, "list-change-sets", "--stack-name", prepared["stack_id"]).get("Summaries", [])
        if not any(item.get("ChangeSetId") == prepared["change_set_id"] for item in remaining):
            break
        pause(context, deadline)
    record(context, preview_discarded=True)
    return {"change_set_id": prepared["change_set_id"], "status": "discarded",
            "stack_retained": True}


# Recover a failed preview from its durable request record without a finished envelope
def inspect_pending(context: Context) -> dict:
    path = context.stage.parent / "cloudformation-operation.json"
    if not path.is_file():
        raise Refused("No CloudFormation operation record is available")
    saved = json.loads(path.read_text())
    if identity(context) != saved.get("scope"):
        raise Refused("Saved preview belongs to another AWS target")
    current = stack(context, saved["stack_id"]) if saved.get("stack_id") else lookup(context)
    if current is None:
        return {"status": "not_observed", "token": saved["token"], "retry_safe": False}
    if saved.get("action") == "create-change-set":
        reviewed = call(context, "describe-change-set", "--stack-name", current["StackId"], "--change-set-name", saved["token"])
        if reviewed.get("StackId") != current["StackId"]:
            raise Refused("Pending change set could not be bound to the saved request")
        tags = {item["Key"]: item["Value"] for item in current.get("Tags", [])}
        if (saved.get("change_set_type") == "CREATE" and current.get("StackStatus") == "REVIEW_IN_PROGRESS"
                and tags.get(OWNER_TAG) == saved["token"] and reviewed.get("ChangeSetName") == saved["token"]):
            save_owner(context, current["StackId"], saved["token"])
        record(context, stack_id=current["StackId"], change_set_id=reviewed["ChangeSetId"])
    return inspect(context, {"stack_id": current["StackId"]})
