# delivery/configuration.py - Edit validated workspace drafts without discarding authored TOML.

import difflib
import hashlib
import tomllib

import tomlkit
from pydantic import ValidationError

from ..errors import Refused
from .spec import ADAPTERS, Component, Environment, Repository, Workspace

FIELDS = {
    "repositories": (Repository, ("path", "revision", "allow_uncommitted")),
    "environments": (Environment, ("provider", "production", "promote_from")),
    "components": (Component, ("repository", "kind", "adapter", "path", "needs", "timeout_seconds")),
}


def source_hash(source: str) -> str:
    return hashlib.sha256(source.encode()).hexdigest()


def difference(before: str, after: str) -> str:
    return "".join(difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True),
                                        fromfile="saved workspace", tofile="configuration draft"))


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
    return {"configuration": workspace.model_dump(), "forms": forms, "digest": workspace.digest,
            "source_hash": source_hash(source), "order": workspace.order()}


def patch(source: str, section: str, name: str, values: dict) -> dict:
    before = inspect(source)
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
