# src/deploy_ops/delivery/adapters/iac.py - Apply a saved plan and export only named, non-secret outputs.


import json
import re
from pathlib import Path

from ...client import file_digest, private_json
from ...errors import Refused
from ...config import fingerprint
from ...plans import execution_blocker, normalize
from ..files import inside
from .base import Context


# Keep engine selection explicit without changing existing state ownership
BINARIES = {"opentofu": "tofu", "terraform": "terraform"}


# Save the exact plan with its provider lock and engine version
def prepare(context: Context) -> dict:
    binary = BINARIES[context.component.adapter]
    lock = context.source / ".terraform.lock.hcl"
    if not lock.is_file():
        raise Refused("Commit the provider lock file before guarded infrastructure delivery", 422)
    if any(key in context.options and type(context.options[key]) is not bool for key in ("local_state", "remote_state")):
        raise Refused("State selection flags must be booleans", 422)
    if bool(context.options.get("local_state")) == bool(context.options.get("remote_state")):
        raise Refused("Choose exactly one of local_state or remote_state", 422)
    if context.options.get("local_state", False):
        if context.provider != "local":
            raise Refused("Local state is limited to local environments", 422)
        if any(path.name in {"override.tf", "override.tf.json"} or path.name.endswith(("_override.tf", "_override.tf.json"))
               for path in context.source.iterdir()):
            raise Refused("Local state requires a single operator-owned backend override; source override files are unsupported", 422)
        if context.options.get("backend_config"):
            raise Refused("Local state fixes its destination; remove backend_config overrides", 422)
        state = (context.deployments / context.environment / context.name / "terraform.tfstate").resolve()
        state.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        private_json(context.source / "deploy_ops_backend_override.tf.json",
                     {"terraform": {"backend": {"local": {"path": str(state)}}}})
    elif not context.options.get("remote_state", False):
        raise Refused("Declare remote_state=true after configuring a remote backend, or local_state=true locally", 422)
    # Validate the initialized workspace before retaining the plan
    initialization = init(context)
    context.command(initialization)
    backend = backend_binding(context)
    context.command([binary, "validate", "-no-color"])
    saved = context.stage / "infrastructure.plan"
    arguments = [binary, "plan", "-input=false", "-no-color", "-out=" + str(saved)]
    # Resolve declared inputs within the frozen source snapshot
    var_files = context.options.get("var_files", [])
    if not isinstance(var_files, list) or not all(isinstance(path, str) for path in var_files):
        raise Refused("var_files must be a list of repository-relative paths", 422)
    for path in var_files:
        arguments.append("-var-file=" + str(inside(context.source, path)))
    variables = context.options.get("variables", {})
    if not isinstance(variables, dict):
        raise Refused("variables must be a public-value map", 422)
    if variables:
        values = context.stage / "inputs.tfvars.json"
        private_json(values, variables)
        arguments.append("-var-file=" + str(values))
    context.command(arguments)
    raw = json.loads(context.command([binary, "show", "-json", str(saved)], capture=True))
    normalized = normalize(context.component.adapter, context.provider, raw)
    version = json.loads(context.command([binary, "version", "-json"], capture=True))["terraform_version"]
    readiness = output_readiness(raw, context.component.outputs)
    return {"iac_contract_version": 2, "native_effects_version": 2,
            "preview": normalized.model_dump(), "tool_version": version,
            "lock_digest": file_digest(lock), "plan_digest": file_digest(saved),
            "backend": backend, "output_readiness": readiness}


# Recheck tool identity before applying the retained plan
def apply(context: Context, prepared: dict) -> dict:
    require_contract(prepared, context.component.adapter)
    if file_digest(context.stage / "infrastructure.plan") != prepared["plan_digest"]:
        raise Refused("Saved infrastructure plan differs from its reviewed digest")
    binary = BINARIES[context.component.adapter]
    version = json.loads(context.command([binary, "version", "-json"], capture=True))["terraform_version"]
    if version != prepared["tool_version"] or file_digest(context.source / ".terraform.lock.hcl") != prepared["lock_digest"]:
        raise Refused("IaC tool or provider lock differs from the reviewed plan")
    context.command(init(context))
    if backend_binding(context) != prepared["backend"]:
        raise Refused("Effective IaC backend differs from the reviewed destination; prepare fresh evidence")
    raw_plan = json.loads(context.command([binary, "show", "-json", str(context.stage / "infrastructure.plan")], capture=True))
    output_readiness(raw_plan, context.component.outputs)
    context.command([binary, "apply", "-input=false", "-no-color", str(context.stage / "infrastructure.plan")])
    # Export only named small public values for dependent application work
    raw = json.loads(context.command([binary, "output", "-json"], capture=True))
    result = {}
    for name in context.component.outputs:
        entry = raw.get(name)
        if not entry or entry.get("sensitive"):
            raise Refused("A required infrastructure output is missing or marked sensitive")
        value = entry.get("value")
        if not isinstance(value, (str, int, bool)) or len(str(value)) > 2000:
            raise Refused("Public output bindings must be small scalar values")
        result[name] = value
    return result


# Reuse the declared backend without changing the provider lock
def init(context: Context) -> list[str]:
    """Backend selectors are public configuration. Credentials must come from the worker identity."""
    command = [BINARIES[context.component.adapter], "init", "-input=false", "-lockfile=readonly", "-no-color", "-reconfigure"]
    values = context.options.get("backend_config", {})
    if not isinstance(values, dict):
        raise Refused("backend_config must be a public-value map", 422)
    for key, value in values.items():
        if not re.fullmatch(r"[a-zA-Z0-9_]+", key) or not isinstance(value, (str, int, bool)):
            raise Refused("Invalid backend setting", 422)
        command.append(f"-backend-config={key}={str(value).lower() if isinstance(value, bool) else value}")
    return command


# Preserve old reviews without admitting an unbound effective backend or lossy migration effects
def require_contract(prepared: dict, engine: str) -> None:
    if prepared.get("iac_contract_version") != 2 or not prepared.get("backend") or not prepared.get("plan_digest"):
        raise Refused("Legacy IaC approval lacks the effective backend/output contract. Preserve history and prepare fresh evidence")
    blocker = execution_blocker({"engine": engine, "preview": prepared["preview"],
                                 "native_effects_version": prepared.get("native_effects_version")})
    if blocker:
        raise Refused(blocker)


# Bind the actual initialized backend and default workspace, rather than the remote-state declaration
def backend_binding(context: Context) -> dict:
    try:
        data = json.loads((context.source / ".terraform" / "terraform.tfstate").read_text())
        if data.get("version") != 3:
            raise ValueError("Unsupported native metadata")
        native = data["backend"]
        kind, config = native["type"], native["config"]
        required = {"local": ("path",), "s3": ("bucket", "key"),
                    "gcs": ("bucket", "prefix"), "azurerm": ("storage_account_name", "container_name", "key")}
        if kind not in required or not isinstance(config, dict):
            raise ValueError("Unsupported effective backend")
        workspace = context.source / ".terraform" / "environment"
        if workspace.exists() and workspace.read_text().strip() != "default":
            raise ValueError("Only the default workspace is currently supported")
        if context.options.get("local_state", False):
            expected = (context.deployments / context.environment / context.name / "terraform.tfstate").resolve()
            actual = Path(config.get("path", ""))
            if not actual.is_absolute():
                actual = context.source / actual
            if kind != "local" or actual.resolve() != expected or config.get("workspace_dir"):
                raise ValueError("Local destination differs")
        elif not context.options.get("remote_state", False) or kind == "local":
            raise ValueError("Remote declaration does not select remote state")
        if any(not isinstance(config.get(key), str) or (not config[key] and key != "prefix") for key in required[kind]):
            raise ValueError("Backend destination is incomplete")
        return {"version": 1, "type": kind, "workspace": "default",
                "destination": {key: config[key] for key in required[kind]}, "config_digest": fingerprint(config)}
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        raise Refused("Effective backend must be the exact local destination or supported remote s3/gcs/azurerm backend in the default workspace", 422) from error


# Refuse only known unusable public outputs; unknown scalar values still need post-apply checks
def output_readiness(raw: dict, selected: list[str]) -> dict:
    values, configuration = raw.get("planned_values", {}), raw.get("configuration", {})
    if not isinstance(values, dict) or not isinstance(configuration, dict) or not isinstance(configuration.get("root_module", {}), dict):
        raise Refused("Native plan output metadata is unsupported", 422)
    planned = values.get("outputs", {})
    changes = raw.get("output_changes", {})
    declared = configuration.get("root_module", {}).get("outputs", {})
    if not all(isinstance(value, dict) for value in (planned, changes, declared)):
        raise Refused("Native plan output metadata is unsupported", 422)
    result = {}
    for name in selected:
        if name not in planned and name not in changes and name not in declared:
            raise Refused("A selected infrastructure output is not declared in the native plan: " + name, 422)
        entry, change = planned.get(name, {}), changes.get(name, {})
        if not isinstance(entry, dict) or not isinstance(change, dict):
            raise Refused("Selected output metadata is unsupported", 422)
        if entry.get("sensitive") or change.get("after_sensitive") is True:
            raise Refused("Selected infrastructure output is marked sensitive: " + name, 422)
        kind = entry.get("type")
        if kind is not None and kind not in ("string", "number", "bool"):
            raise Refused("Selected infrastructure output has a non-scalar type: " + name, 422)
        known = "value" in entry or "after" in change
        value = entry.get("value", change.get("after"))
        unknown = bool(change.get("after_unknown"))
        if known and not unknown:
            if not isinstance(value, (str, int, bool)) or len(str(value)) > 2000:
                raise Refused("Selected infrastructure output is not a small supported public scalar: " + name, 422)
            result[name] = "known_supported_scalar"
        else:
            result[name] = "unknown_until_apply"
    return result
