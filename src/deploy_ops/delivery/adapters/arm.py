# src/deploy_ops/delivery/adapters/arm.py - Review frozen Azure templates and reconcile named deployments.


# Native Azure commands own provider behavior while the shared engine owns approvals
import json
import re
from pathlib import Path

from ...client import file_digest, private_json
from ...config import fingerprint
from ...errors import Refused
from ..files import inside
from .base import Context, need


# Keep the first native route inside one existing resource group without access mutations
UUID = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")
NAME = re.compile(r"[A-Za-z0-9_-]{1,64}")
BLOCKED_TYPES = {
    "microsoft.authorization/roleassignments", "microsoft.authorization/roledefinitions",
    "microsoft.resources/deployments", "microsoft.resources/deploymentscripts",
}
ACTIONS = {"Create": "create", "Modify": "update", "NoChange": "no-op", "NoEffect": "no-op"}
TERMINAL = {"Succeeded", "Failed", "Canceled"}


# Parse provider JSON without leaking its body through exceptions
def decode(value: str) -> dict:
    try:
        result = json.loads(value)
    except (ValueError, TypeError) as error:
        raise Refused("Azure returned an invalid JSON response") from error
    if not isinstance(result, dict):
        raise Refused("Azure returned an unexpected response shape")
    return result


# Refuse unqualified modes and pin the subscription and tenant before authentication
def target(context: Context) -> dict:
    if context.provider != "azure" or context.component.kind != "infrastructure":
        raise Refused("ARM and Bicep require Azure infrastructure components", 422)
    if context.options.get("mode", "Incremental") != "Incremental":
        raise Refused("Only incremental resource-group deployments are supported; complete mode can delete unrelated resources", 422)
    if context.options.get("operation", "deploy") != "deploy":
        raise Refused("ARM resource destruction needs a separately reviewed resource inventory; deployment deletion only removes history", 422)
    result = {key: need(context.options, key) for key in ("subscription_id", "tenant_id", "resource_group")}
    if not all(UUID.fullmatch(result[key]) for key in ("subscription_id", "tenant_id")):
        raise Refused("Azure subscription_id and tenant_id must be UUIDs", 422)
    if not re.fullmatch(r"[A-Za-z0-9_().-]{1,90}", result["resource_group"]) or result["resource_group"].endswith("."):
        raise Refused("Use a valid explicit Azure resource_group", 422)
    name = context.options.get("deployment_name", "deploy-ops")
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise Refused("deployment_name must use at most 64 letters, digits, underscores or hyphens", 422)
    suffix = fingerprint([context.release_id, context.name])[:16]
    result["deployment_name"] = name[:47] + "-" + suffix
    result["deployment_id"] = (
        f"/subscriptions/{result['subscription_id']}/resourceGroups/{result['resource_group']}"
        f"/providers/Microsoft.Resources/deployments/{result['deployment_name']}"
    )
    return result


# Force Azure CLI calls into the reviewed subscription with captured provider output
def command(scope: dict, *parts: str) -> list[str]:
    return ["az", *parts, "--subscription", scope["subscription_id"], "--only-show-errors", "--output", "json"]


# Verify the active cloud, tenant and subscription rather than trusting shell defaults
def identity(context: Context, scope: dict) -> dict:
    raw = decode(context.command(command(scope, "account", "show"), capture=True))
    if (str(raw.get("id", "")).lower() != scope["subscription_id"].lower()
            or str(raw.get("tenantId", "")).lower() != scope["tenant_id"].lower()
            or raw.get("environmentName") != "AzureCloud" or raw.get("state") != "Enabled"):
        raise Refused("Azure login does not match the reviewed public-cloud subscription and tenant")
    user = raw.get("user", {})
    if not isinstance(user, dict) or not user.get("name") or not user.get("type"):
        raise Refused("Azure CLI did not identify the authenticated principal")
    return {"subscription_id": raw["id"], "tenant_id": raw["tenantId"],
            "principal": user["name"], "principal_type": user["type"]}


# Keep validation-level semantics fixed across preview and execution
def version(context: Context) -> str:
    value = decode(context.command(["az", "version", "--output", "json"], capture=True)).get("azure-cli", "")
    match = re.fullmatch(r"([0-9]+)\.([0-9]+)\.([0-9]+)", str(value))
    if not match or tuple(map(int, match.groups())) < (2, 76, 0):
        raise Refused("ARM delivery requires Azure CLI 2.76.0 or later", 422)
    return value


# Reject remote templates, access changes and executable deployments before cloud commands
def validate_template(template: dict, outputs: list[str]) -> None:
    schema = template.get("$schema", "")
    if not isinstance(schema, str) or not schema.endswith("/deploymentTemplate.json#"):
        raise Refused("Use a resource-group ARM deployment template", 422)
    if any(template.get(key) for key in ("imports", "extensions", "extensionConfigs")):
        raise Refused("Deployment extensions need a separately qualified execution route", 422)
    resources = template.get("resources")
    if not isinstance(resources, (list, dict)):
        raise Refused("ARM resources must be a list or symbolic resource map", 422)

    # Nested resources stay local, but nested deployments need a separately qualified lifecycle
    def inspect(items, parent=""):
        for item in (items.values() if isinstance(items, dict) else items):
            if not isinstance(item, dict) or not isinstance(item.get("type"), str):
                raise Refused("ARM resource types must be explicit", 422)
            kind = item["type"] if not parent else parent + "/" + item["type"]
            if ("[" in kind or kind.lower() in BLOCKED_TYPES or item["type"].lower() in BLOCKED_TYPES
                    or any(key in item for key in ("scope", "subscriptionId", "resourceGroup"))):
                raise Refused("Cross-scope, nested deployments, scripts and role changes are outside this ARM route", 422)
            if "resources" in item:
                children = item["resources"]
                if not isinstance(children, (list, dict)):
                    raise Refused("Invalid nested ARM resources", 422)
                inspect(children, kind)

    inspect(resources)
    definitions = template.get("outputs", {})
    if not isinstance(definitions, dict):
        raise Refused("ARM output definitions must be a map", 422)
    for name in outputs:
        entry = definitions.get(name, {})
        if not isinstance(entry, dict) or str(entry.get("type", "")).lower() not in {"string", "int", "bool"}:
            raise Refused("Select only declared non-secret scalar ARM outputs", 422)


# Freeze only public parameters and never infer permission to pass secret values
def parameters(context: Context, template: dict) -> dict:
    values = context.options.get("parameters", {})
    definitions = template.get("parameters", {})
    if not isinstance(values, dict) or not isinstance(definitions, dict) or set(values) - definitions.keys():
        raise Refused("ARM parameters must name declared template parameters", 422)
    result = {}
    for name, definition in definitions.items():
        if not isinstance(definition, dict):
            raise Refused("Invalid ARM parameter definition", 422)
        if str(definition.get("type", "")).lower() in {"securestring", "secureobject"}:
            raise Refused("Secure ARM parameters need a qualified secret-reference route", 422)
        if name in values:
            result[name] = {"value": values[name]}
        elif "defaultValue" not in definition:
            raise Refused("Supply every required public ARM parameter before preview", 422)
    return {"$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#",
            "contentVersion": "1.0.0.0", "parameters": result}


# Consume frozen compiler output without rerunning Bicep source with writer credentials
def freeze(context: Context) -> dict:
    source = inside(context.source, need(context.options, "template"))
    if source.suffix != ".json" or not source.is_file() or source.stat().st_size > 4_000_000:
        raise Refused("Provide a local ARM JSON template up to 4 MB; compile Bicep in an isolated build first", 422)
    template = decode(source.read_text())
    validate_template(template, context.component.outputs)
    inputs = parameters(context, template)
    metadata = {}
    if context.component.adapter == "bicep":
        details = template.get("metadata", {})
        generator = details.get("_generator", {}) if isinstance(details, dict) else {}
        if (not isinstance(generator, dict) or generator.get("name") != "bicep"
                or not generator.get("version") or not generator.get("templateHash")):
            raise Refused("Bicep delivery requires native compiler metadata in the frozen JSON template", 422)
        metadata = {"compiler": "bicep", "compiler_version": str(generator["version"]),
                    "compiler_template_hash": str(generator["templateHash"])}
    private_json(context.stage / "arm-template.json", template)
    private_json(context.stage / "arm-parameters.json", inputs)
    return {"template_digest": file_digest(context.stage / "arm-template.json"),
            "parameters_digest": file_digest(context.stage / "arm-parameters.json"), **metadata}


# Share the exact frozen template arguments between prediction and submission
def inputs(context: Context, scope: dict) -> list[str]:
    return ["--resource-group", scope["resource_group"], "--name", scope["deployment_name"],
            "--template-file", str(context.stage / "arm-template.json"),
            "--parameters", "@" + str(context.stage / "arm-parameters.json"), "--mode", "Incremental",
            "--validation-level", "Provider"]


# Bind full provider changes privately while retaining only safe addresses and actions
def what_if(context: Context, scope: dict) -> dict:
    raw = decode(context.command(command(scope, "deployment", "group", "what-if", *inputs(context, scope),
        "--no-pretty-print", "--no-prompt", "true", "--result-format", "FullResourcePayloads"), capture=True))
    changes = raw.get("changes")
    if (raw.get("status") != "Succeeded" or raw.get("error") or raw.get("nextLink")
            or raw.get("diagnostics") or raw.get("potentialChanges")
            or not isinstance(changes, list) or len(changes) > 2000):
        raise Refused("Azure what-if is incomplete or failed; inspect the provider diagnostics")
    preview = []
    prefix = f"/subscriptions/{scope['subscription_id']}/resourceGroups/{scope['resource_group']}/providers/"
    for change in changes:
        address = change.get("resourceId", "") if isinstance(change, dict) else ""
        action = ACTIONS.get(change.get("changeType")) if isinstance(change, dict) else None
        if not isinstance(address, str) or not address.lower().startswith(prefix.lower()) or len(address) > 512:
            raise Refused("Azure what-if includes a resource outside the reviewed group")
        if action is None:
            raise Refused("Azure what-if contains deletion, ignored, unsupported or unresolved changes; inspect before preparing again")
        resource = change.get("after") or change.get("before") or {}
        kind = resource.get("type") if isinstance(resource, dict) else None
        if not isinstance(kind, str) or not kind or len(kind) > 256:
            raise Refused("Azure what-if omitted a resource type")
        preview.append({"address": address, "resource_type": kind, "action": action})
    return {"digest": fingerprint(sorted(changes, key=lambda item: item["resourceId"].lower())),
            "preview": {"changes": preview, "findings": [], "manual_only": True}}


# Prepare a prediction that must be revalidated because Azure does not save an executable plan
def prepare(context: Context) -> dict:
    scope = target(context)
    frozen = freeze(context)
    tool_version = version(context)
    principal = identity(context, scope)
    prediction = what_if(context, scope)
    return {**frozen, "target": scope, "identity": principal, "tool_version": tool_version,
            "what_if_digest": prediction["digest"], "preview": prediction["preview"],
            "limitations": ["What-if is a prediction, not an immutable Azure execution grant",
                            "Only incremental deployments into an existing resource group are supported",
                            "Resources are retained; removing deployment history does not destroy resources"]}


# Recheck the exact files and target before any execution or recovery readback
def verify(context: Context, prepared: dict) -> dict:
    scope = target(context)
    if scope != prepared["target"]:
        raise Refused("Azure deployment target changed after review")
    for name, key in (("arm-template.json", "template_digest"), ("arm-parameters.json", "parameters_digest")):
        path = context.stage / name
        if not path.is_file() or path.is_symlink() or file_digest(path) != prepared[key]:
            raise Refused("Frozen Azure template or parameters changed after review")
    if version(context) != prepared["tool_version"] or identity(context, scope) != prepared["identity"]:
        raise Refused("Azure tool or authenticated principal changed after review")
    return scope


# Keep mutable operation observations outside the approval-bound artifact directory
def operation_path(context: Context) -> Path:
    return context.stage.parent / "arm-operation.json"


# Preserve a named operation before submission so a lost response cannot trigger blind replay
def record(context: Context, prepared: dict, status: str) -> None:
    private_json(operation_path(context), {"release_id": context.release_id, "component": context.name,
        "prepared_digest": fingerprint(prepared), "deployment_id": prepared["target"]["deployment_id"], "status": status})


# Export only the explicitly selected public scalars after provider-confirmed success
def outputs(context: Context, raw: dict) -> dict:
    result = {}
    declared = raw.get("properties", {}).get("outputs", {})
    if not isinstance(declared, dict):
        raise Refused("Azure deployment returned invalid outputs")
    for name in context.component.outputs:
        value = declared.get(name, {})
        if (not isinstance(value, dict) or str(value.get("type", "")).lower() not in {"string", "int", "bool"}
                or not isinstance(value.get("value"), (str, int, bool)) or len(str(value.get("value"))) > 2000):
            raise Refused("A selected Azure output is missing, sensitive or not a small scalar")
        result[name] = value["value"]
    return result


# Read the provider operation after uncertain execution without changing resources
def inspect(context: Context, prepared: dict) -> dict:
    scope = verify(context, prepared)
    path = operation_path(context)
    if not path.is_file():
        raise Refused("No owned Azure submission record exists for this release")
    marker = decode(path.read_text())
    if (marker.get("prepared_digest") != fingerprint(prepared) or marker.get("release_id") != context.release_id
            or marker.get("component") != context.name or marker.get("deployment_id") != scope["deployment_id"]):
        raise Refused("Azure operation ownership does not match this release")
    raw = decode(context.command(command(scope, "deployment", "group", "show", "--resource-group",
        scope["resource_group"], "--name", scope["deployment_name"]), capture=True))
    if str(raw.get("id", "")).lower() != scope["deployment_id"].lower():
        raise Refused("Azure returned an unexpected deployment identity")
    state = raw.get("properties", {}).get("provisioningState", "Unknown")
    if state not in TERMINAL:
        return {"deployment_id": scope["deployment_id"], "status": "unknown", "outputs": {}}
    record(context, prepared, state.lower())
    return {"deployment_id": scope["deployment_id"], "status": state.lower(),
            "outputs": outputs(context, raw) if state == "Succeeded" else {}}


# Reject stale predictions and submit once using only frozen artifacts
def apply(context: Context, prepared: dict) -> dict:
    scope = verify(context, prepared)
    if operation_path(context).exists():
        raise Refused("Azure submission was already attempted; inspect the named deployment before recovery")
    prediction = what_if(context, scope)
    if prediction["digest"] != prepared["what_if_digest"]:
        raise Refused("Azure what-if changed after review; prepare a fresh release")

    # A deployment name is unique to this release, but never overwrite an existing record
    existing = context.command(command(scope, "deployment", "group", "list", "--resource-group",
        scope["resource_group"], "--query", f"[?name=='{scope['deployment_name']}'].id"), capture=True)
    try:
        found = json.loads(existing)
    except ValueError as error:
        raise Refused("Azure deployment-name check failed") from error
    if found != []:
        raise Refused("Azure deployment name already exists; inspect it before recovery")
    record(context, prepared, "submitting")
    context.command(command(scope, "deployment", "group", "create", *inputs(context, scope)), capture=True)
    observed = inspect(context, prepared)
    if observed["status"] != "succeeded":
        raise Refused("Azure deployment did not succeed; inspect the named deployment before recovery")
    return observed["outputs"]
