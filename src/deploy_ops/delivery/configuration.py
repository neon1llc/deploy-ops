# delivery/configuration.py - Edit validated workspace drafts without discarding authored TOML.


import difflib
import hashlib
import os
import re
import time
from collections import defaultdict
from contextlib import contextmanager
import tomllib

import tomlkit
from pydantic import ValidationError

from ..errors import Refused
from .spec import ADAPTERS, NAME, Build, Component, Environment, Repository, Workspace, options
from .options import LOCAL_MODELS, MODELS, form

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
    return {"configuration": workspace.model_dump(), "forms": forms, "option_forms": option_forms,
            "creation_forms": creation_forms(forms), "local_edit_forms": local_forms(source, workspace), "digest": workspace.digest,
            "source_hash": source_hash(source), "order": workspace.order()}


# Describe authored and inherited local settings without writing adapter defaults
def local_forms(source: str, workspace: Workspace) -> dict:
    authored = tomllib.loads(source)
    result = {}
    for name, component in workspace.components.items():
        if component.adapter not in LOCAL_MODELS:
            continue
        model = LOCAL_MODELS[component.adapter]
        base = authored["components"][name].get("options", {})
        fields = model.model_json_schema()["properties"]
        environments = {}
        for environment in workspace.environments:
            effective = workspace.component(name, environment)
            if effective.adapter != component.adapter or workspace.environments[environment].provider != "local":
                continue
            override = component.targets.get(environment, {})
            environments[environment] = {key: {"component": base.get(key), "component_authored": key in base,
                "override": override.get(key), "override_authored": key in override,
                "effective": effective.options.get(key, None if component.adapter == "static" and key == "destination" else definition.get("default")),
                "runtime_default": component.adapter == "static" and key == "destination" and key not in effective.options,
                "source": "override" if key in override else "component" if key in base else "adapter default"}
                for key, definition in fields.items()}
        result[name] = {"fields": fields, "base": base, "environments": environments,
            "build": component.build.model_dump(),
            "build_authored": authored["components"][name].get("build", {}),
            "build_sources": {key: "component" if key in authored["components"][name].get("build", {}) else "adapter default"
                for key in ("checks", "command")}, "build_fields": {key: Build.model_json_schema()["properties"][key]
                for key in ("checks", "command")}}
    return result


# Validate edited local contracts and declared references without inventing output values
def local_valid(workspace: Workspace, name: str):
    for environment_name, environment in workspace.environments.items():
        component = workspace.component(name, environment_name)
        if component.adapter not in LOCAL_MODELS or environment.provider != "local":
            continue
        values = dict(component.options)
        for key, value in values.items():
            if not isinstance(value, str) or not value.startswith("${"):
                continue
            reference = re.fullmatch(r"\$\{(env\.[^{}]+|outputs\.[^{}.]+\.[^{}.]+)\}", value)
            if not reference:
                raise ValueError("Use a complete environment or declared dependency output reference")
            parts = reference[1].split(".")
            if parts[0] == "env":
                variable = reference[1][4:]
                if variable not in environment.variables:
                    raise ValueError("Unknown environment variable reference")
                values[key] = environment.variables[variable]
            elif parts[1] not in component.needs:
                raise ValueError("Output references must name a declared dependency")
        LOCAL_MODELS[component.adapter].model_validate(values, strict=True)


# Edit a specific supported scope while preserving authored pins and unrelated target metadata
def edit(source: str, name: str, scope: str, values: dict, environment: str = "", field: str = "") -> dict:
    before = inspect(source)
    contract = before["local_edit_forms"].get(name)
    if contract is None or scope not in {"build", "base", "environment"}:
        raise Refused("Choose an existing directory/static component and explicit edit scope", 422)
    if (scope == "environment" and environment not in contract["environments"]) or (scope != "environment" and environment):
        raise Refused("Choose an applicable local environment only for environment scope", 422)
    allowed = {"checks", "command"} if scope == "build" else set(contract["fields"])
    if not isinstance(values, dict) or set(values) - allowed or (field and (field not in allowed or values or scope != "environment")):
        raise Refused("Choose supported fields; reset requires one environment field and no values", 422)
    if not values and not field:
        raise Refused("Provide edited fields or one override to remove", 422)
    document = tomlkit.parse(source)
    component = document["components"][name]
    try:
        if scope == "build":
            build = Build.model_validate({**contract["build"], **values}, strict=True)
            if (build.command and not build.command[0]) or any(not command or not command[0] for command in build.checks):
                raise ValueError("Each command needs a nonempty executable; clear the whole list to disable it")
            target = component.setdefault("build", tomlkit.table())
        elif scope == "base":
            target = component.setdefault("options", tomlkit.table())
        else:
            targets = component.get("targets", {})
            if field:
                target = targets.get(environment, {})
                if field in target:
                    del target[field]
            else:
                target = component.setdefault("targets", tomlkit.table()).setdefault(environment, tomlkit.table())
        for key, value in values.items():
            # Preserve omitted defaults on unchanged base/build forms; environment values are explicit pins
            current = contract["build"].get(key) if scope == "build" else contract["base"].get(key, contract["fields"][key].get("default"))
            if scope == "environment" or value != current:
                target[key] = value
        group = "build" if scope == "build" else "options"
        if scope in {"build", "base"} and not target and group not in tomllib.loads(source)["components"][name]:
            del component[group]
        revised = tomlkit.dumps(document)
        workspace = Workspace.model_validate(tomllib.loads(revised))
        local_valid(workspace, name)
    except (ValueError, TypeError, ValidationError) as error:
        raise Refused(str(error), 422) from error
    return {**inspect(revised), "toml": revised, "diff": difference(source, revised)}



# Derive compact creation fields from the same typed contracts used to validate additions
def creation_forms(forms: dict) -> dict:
    result = {}
    for section in ("repositories", "environments"):
        result[section] = {"section": section, "fields": forms[section], "values": {},
                           "required": ["path"] if section == "repositories" else ["provider"]}
    result["environments"]["values"] = {"provider": "local", "production": False, "preview": False}
    for adapter, model in LOCAL_MODELS.items():
        schema = model.model_json_schema()
        fields = {key: value for key, value in forms["components"].items() if key not in {"kind", "adapter"}}
        build = Build.model_json_schema()["properties"]
        fields.update({"build." + key: build[key] for key in ("command", "checks")})
        fields.update({"options." + key: value for key, value in schema["properties"].items()})
        result[adapter] = {"section": "components", "fields": fields,
                           "values": {"adapter": adapter, "kind": "infrastructure" if adapter == "directory" else "application"},
                           "required": ["repository", *("options." + key for key in schema.get("required", []))]}
    return result


# Validate guided local settings without changing native cloud forms or executing code
def local_addition(workspace: Workspace, name: str):
    authored = workspace.components[name]
    if authored.adapter in LOCAL_MODELS:
        LOCAL_MODELS[authored.adapter].model_validate(authored.options, strict=True)
    for environment_name, environment in workspace.environments.items():
        component = workspace.component(name, environment_name)
        if component.adapter not in LOCAL_MODELS:
            continue
        expected = "infrastructure" if component.adapter == "directory" else "application"
        if environment.provider != "local" or component.kind != expected:
            raise ValueError("Guided directory/static components need a local environment and matching kind")
        LOCAL_MODELS[component.adapter].model_validate(component.options, strict=True)
        # Check known environment values and reference edges without claiming future output values
        outputs = {dependency: defaultdict(lambda: "public-output") for dependency in component.needs}
        resolved = options(component, environment, environment_name, outputs)
        LOCAL_MODELS[component.adapter].model_validate(resolved, strict=True)


# Add one named entry to a proposed document, leaving persistence and execution separate
def add(source: str, section: str, name: str, values: dict) -> dict:
    before = inspect(source)
    if section not in FIELDS or not isinstance(name, str) or not NAME.fullmatch(name):
        raise Refused("Choose repositories, environments or components and a lowercase entry name", 422)
    if name in before["configuration"][section]:
        raise Refused("That name already exists in this section", 422)
    if not isinstance(values, dict):
        raise Refused("Provide entry fields as an object", 422)
    values = dict(values)
    if section == "environments":
        values.setdefault("provider", "local")
    try:
        FIELDS[section][0].model_validate(values, strict=True)
        document = tomlkit.parse(source)
        document.setdefault(section, tomlkit.table())[name] = values
        revised = tomlkit.dumps(document)
        workspace = Workspace.model_validate(tomllib.loads(revised))
        if section == "components":
            local_addition(workspace, name)
    except (ValueError, TypeError, ValidationError) as error:
        raise Refused(str(error), 422) from error
    return {**inspect(revised), "toml": revised, "diff": difference(source, revised)}


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


# Validate a proposed contract and expose both draft and saved source identities
def validate(source: str, current: str) -> dict:
    result = inspect(source)
    return {key: result[key] for key in ("digest", "order", "source_hash")} | {
        "status": "valid", "saved_source_hash": source_hash(current), "diff": difference(current, source),
    }


# Exclude configuration saves and release preparation across local CLI and console processes
@contextmanager
def boundary(engine):
    import fcntl
    descriptor = os.open(engine.state / "workspace.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Refused("Workspace configuration or release preparation is active; retry after it finishes") from None
        yield
    finally:
        os.close(descriptor)


# Save the reviewed draft only while the local workspace has no outstanding delivery work
def save(engine, source: str, expected_hash: str, worker_active: bool = False) -> dict:
    proposed = inspect(source)
    if engine.gate.__class__.__name__ != "LocalGate":
        raise Refused("Only local operators may save a local workspace here", 403)
    with boundary(engine):
        current = engine.path.read_text()
        if expected_hash != source_hash(current):
            raise Refused("The workspace changed since it was loaded. Preserve your draft and reload before saving")
        current_digest = inspect(current)["digest"]
        with engine.ledger.store.transaction() as connection:
            active = connection.execute(
                "SELECT 1 FROM releases WHERE status NOT IN ('succeeded','failed','cancelled') LIMIT 1"
            ).fetchone()
            if worker_active or active:
                raise Refused("Finish, cancel, or reconcile outstanding releases before changing configuration")

            # Refuse an earlier interrupted write before changing the recovery copy
            pending = engine.path.with_suffix(".pending")
            try:
                descriptor = os.open(pending, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                raise Refused("A pending configuration file already exists. Inspect it first") from None
            with os.fdopen(descriptor, "w") as stream:
                stream.write(source)
            if source_hash(engine.path.read_text()) != expected_hash:
                pending.unlink()
                raise Refused("The workspace changed during saving. Preserve your draft and reload before saving")

            # Preserve authored content before atomically replacing the workspace
            backup = engine.state / "previous-workspace.toml"
            backup_descriptor = os.open(backup, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
            with os.fdopen(backup_descriptor, "w") as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(current)
            os.replace(pending, engine.path)
            engine.ledger.store.event(connection, int(time.time()), "local-operator", "workspace.updated",
                                      proposed["configuration"]["name"],
                                      {"old_digest": current_digest, "new_digest": proposed["digest"]})
    return {"digest": proposed["digest"], "source_hash": proposed["source_hash"], "status": "saved", "reload": True}
