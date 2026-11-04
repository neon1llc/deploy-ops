# src/deploy_ops/delivery/adapters/workflow.py - Run an existing trusted toolchain without claiming native plan guarantees.


import json

from ...client import private_json
from ...errors import Refused
from .base import Context, preview


# Keep external workflow commands as argument arrays
def commands(options: dict, phase: str) -> list[list[str]]:
    value = options.get(phase, [])
    if not isinstance(value, list) or any(
        not isinstance(command, list) or not command or any(not isinstance(item, str) for item in command)
        for command in value
    ):
        raise Refused(f"Workflow {phase} must contain command argument lists", 422)
    return value


# Require both execution and verification before offering a manual review
def prepare(context: Context) -> dict:
    if not commands(context.options, "apply") or not commands(context.options, "verify"):
        raise Refused("Existing workflows need both apply and verify commands", 422)
    for argv in commands(context.options, "prepare"):
        context.command(argv)
    # External previews differ by engine. Unknown change semantics never receive automatic approval
    return {"preview": preview(context.name, "external_workflow"),
            "apply": commands(context.options, "apply"), "verify": commands(context.options, "verify")}


# Run the frozen command sequence and export declared public values only
def apply(context: Context, prepared: dict) -> dict:
    for command in [*prepared["apply"], *prepared["verify"]]:
        context.command(command)
    output_file = context.options.get("outputs_file")
    if not output_file:
        return {}
    from ..files import inside
    raw = json.loads(inside(context.source, output_file).read_text())
    result = {}
    for key in context.component.outputs:
        if key not in raw or not isinstance(raw[key], (str, int, bool)) or len(str(raw[key])) > 2000:
            raise Refused("Workflow outputs must include each declared public scalar")
        result[key] = raw[key]
    return result
