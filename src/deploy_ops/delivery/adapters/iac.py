# src/deploy_ops/delivery/adapters/iac.py - Apply a saved plan and export only named, non-secret outputs.


import json
import re

from ...client import file_digest, private_json
from ...errors import Refused
from ...plans import normalize
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
    if context.options.get("local_state", False):
        if context.provider != "local":
            raise Refused("Local state is limited to local environments", 422)
        state = context.deployments / context.environment / context.name / "terraform.tfstate"
        state.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        private_json(context.source / "deploy_ops_backend_override.tf.json",
                     {"terraform": {"backend": {"local": {"path": str(state)}}}})
    elif not context.options.get("remote_state", False):
        raise Refused("Declare remote_state=true after configuring a remote backend, or local_state=true locally", 422)
    # Validate the initialized workspace before retaining the plan
    initialization = init(context)
    context.command(initialization)
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
    return {"preview": normalized.model_dump(), "tool_version": version,
            "lock_digest": file_digest(lock)}


# Recheck tool identity before applying the retained plan
def apply(context: Context, prepared: dict) -> dict:
    binary = BINARIES[context.component.adapter]
    version = json.loads(context.command([binary, "version", "-json"], capture=True))["terraform_version"]
    if version != prepared["tool_version"] or file_digest(context.source / ".terraform.lock.hcl") != prepared["lock_digest"]:
        raise Refused("IaC tool or provider lock differs from the reviewed plan")
    context.command(init(context))
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
    command = [BINARIES[context.component.adapter], "init", "-input=false", "-lockfile=readonly", "-no-color"]
    values = context.options.get("backend_config", {})
    if not isinstance(values, dict):
        raise Refused("backend_config must be a public-value map", 422)
    for key, value in values.items():
        if not re.fullmatch(r"[a-zA-Z0-9_]+", key) or not isinstance(value, (str, int, bool)):
            raise Refused("Invalid backend setting", 422)
        command.append(f"-backend-config={key}={str(value).lower() if isinstance(value, bool) else value}")
    return command
