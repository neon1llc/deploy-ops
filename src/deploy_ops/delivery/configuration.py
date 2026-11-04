# delivery/configuration.py - Edit validated workspace drafts without discarding authored TOML.


import difflib
import hashlib
import tomllib

import tomlkit
from pydantic import ValidationError

from ..errors import Refused
from .spec import ADAPTERS, Component, Environment, Repository, Workspace
from .options import MODELS, form

# Guided edits expose only fields owned by the workspace rather than controller authority
FIELDS = {
    "repositories": (Repository, ("path", "revision", "allow_uncommitted")),
    "environments": (Environment, ("provider", "production", "preview", "promote_from")),
    "components": (Component, ("repository", "kind", "adapter", "path", "needs", "timeout_seconds")),
}


# Bind conflict detection to the exact authored TOML bytes
def source_hash(source: str) -> str:
    return hashlib.sha256(source.encode()).hexdigest()


# Show the operator the saved-to-draft change without writing it
def difference(before: str, after: str) -> str:
    return "".join(difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True),
                                        fromfile="saved workspace", tofile="configuration draft"))


# Derive forms and effective options from the shared workspace schema
def inspect(source: str) -> dict:
    try:
        workspace = Workspace.model_validate(tomllib.loads(source))
    except (ValueError, ValidationError) as error:
        raise Refused(str(error), 422) from error
    forms = {}
    for section, (model, fields) in FIELDS.items():
        properties = model.model_json_schema()["properties"]
        forms[section] = {name: properties[name] for name in fields}
    forms["components"]["adapter"]["enum"] = sorted(ADAPTERS)
    forms["components"]["repository"]["enum"] = list(workspace.repositories)
    forms["components"]["needs"]["items"]["enum"] = list(workspace.components)
    forms["environments"]["promote_from"]["items"]["enum"] = list(workspace.environments)

    # Native option forms show inheritance after the selected environment is applied
    option_forms = {}
    for name in workspace.components:
        for environment in workspace.environments:
            component = workspace.component(name, environment)
            if component.adapter in MODELS:
                option_forms[name + "/" + environment] = {
                    **form(component.adapter, component.options), "component": name,
                    "environment": environment, "adapter": component.adapter,
                }
    return {"configuration": workspace.model_dump(), "forms": forms, "option_forms": option_forms, "digest": workspace.digest,
            "source_hash": source_hash(source), "order": workspace.order()}


# Apply validated field edits while preserving authored TOML and inheritance
def patch(source: str, section: str, name: str, values: dict) -> dict:
    before = inspect(source)

    # Provider fields update only the chosen environment override
    if section == "options":
        if name not in before["option_forms"] or not isinstance(values, dict):
            raise Refused("Choose a component with native infrastructure settings", 422)
        selected = before["option_forms"][name]
        component = before["configuration"]["components"][selected["component"]]
        model = MODELS[selected["adapter"]]
        if set(values) - model.model_fields.keys():
            raise Refused("Unknown infrastructure setting", 422)
        try:
            model.model_validate({**selected["values"], **values})
        except ValidationError:
            raise Refused("Invalid infrastructure settings; check the field types and required values", 422) from None
        document = tomlkit.parse(source)
        current = before["option_forms"][name]["values"]
        for key, value in values.items():
            if value != current.get(key):
                targets = document["components"][selected["component"]].setdefault("targets", tomlkit.table())
                targets.setdefault(selected["environment"], tomlkit.table())[key] = value
        revised = tomlkit.dumps(document)
        return {**inspect(revised), "toml": revised, "diff": difference(source, revised)}

    # Ordinary fields retain their existing narrow form contract
    if section not in FIELDS or name not in before["configuration"][section]:
        raise Refused("Choose an existing repository, environment or component", 422)
    if not isinstance(values, dict) or set(values) - set(FIELDS[section][1]):
        raise Refused("This form cannot edit advanced settings or controller authority", 422)
    document = tomlkit.parse(source)
    for key, value in values.items():
        field = before["forms"][section][key]
        types = {"string": str, "boolean": bool, "integer": int, "array": list}
        if type(value) is not types[field["type"]]:
            raise Refused(f"Invalid value type for {key}", 422)
        if field["type"] == "array" and any(type(item) is not str for item in value):
            raise Refused(f"Use names for {key}", 422)
        # Avoid adding default values and rewriting unchanged authored fields
        if value != before["configuration"][section][name][key]:
            document[section][name][key] = value
    revised = tomlkit.dumps(document)
    result = inspect(revised)
    return {**result, "toml": revised, "diff": difference(source, revised)}
