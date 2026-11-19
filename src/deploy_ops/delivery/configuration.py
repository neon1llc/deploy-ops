# delivery/configuration.py - Edit validated workspace drafts without discarding authored TOML.


import difflib
import hashlib
import json
import os
import re
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
import tomllib

import tomlkit
from pydantic import ValidationError

from ..errors import Refused
from ..client import private_json, sync_directory
from ..store import Store, require_writable
from .files import read_regular
from .ledger import Ledger
from .spec import ADAPTERS, NAME, Build, Component, Environment, Repository, Workspace
from .options import FORM_MODELS, LOCAL_MODELS, form

# Guided edits expose only fields owned by the workspace rather than controller authority
FIELDS = {
    "repositories": (Repository, ("path", "revision", "allow_uncommitted")),
    "environments": (Environment, ("provider", "production", "preview", "promote_from")),
    "components": (Component, ("repository", "kind", "adapter", "path", "needs", "timeout_seconds")),
}


# Bind conflict detection to the exact authored TOML bytes
def source_hash(source: str) -> str:
    return hashlib.sha256(source.encode()).hexdigest()


# Preserve UTF-8 source bytes, including authored line endings, across all configuration entry points
def read_source(path) -> str:
    return read_regular(path).decode('utf-8')


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
    authored = tomllib.loads(source)
    for name in workspace.components:
        for environment in workspace.environments:
            component = workspace.component(name, environment)
            if component.adapter == 'cloud-run' and workspace.environments[environment].provider != 'gcp':
                continue
            if component.adapter in FORM_MODELS:
                base = authored['components'][name].get('options', {})
                override = authored['components'][name].get('targets', {}).get(environment, {})
                try:
                    selected_form = form(component.adapter, component.options)
                except ValidationError as error:
                    fields = sorted({item['loc'][0] for item in error.errors()
                                     if item['loc'] and item['loc'][0] in FORM_MODELS[component.adapter].model_fields})
                    raise Refused(f"Invalid {component.adapter} settings for {name}/{environment}; check {', '.join(fields)}", 422) from None
                option_forms[name + "/" + environment] = {
                    **selected_form, "component": name,
                    "environment": environment, "adapter": component.adapter,
                    'inheritance': {key: {'override_authored': key in override,
                        'source': 'environment override' if key in override else 'component' if key in base else 'adapter default'}
                        for key in FORM_MODELS[component.adapter].model_fields},
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
        schema = model.model_json_schema()
        fields = schema["properties"]
        environments = {}
        for environment in workspace.environments:
            effective = workspace.component(name, environment)
            if effective.adapter != component.adapter or workspace.environments[environment].provider != "local":
                continue
            override = component.targets.get(environment, {})
            environments[environment] = {key: {"component": base.get(key), "component_authored": key in base,
                "override": override.get(key), "override_authored": key in override,
                "effective": effective.options.get(key, None if component.adapter == "static" and key == "destination" else
                    f"{environment}-{name}" if component.adapter == "compose" and key == "project" else definition.get("default")),
                "runtime_default": component.adapter == "static" and key == "destination" and key not in effective.options,
                "source": "override" if key in override else "component" if key in base else "adapter default"}
                for key, definition in fields.items()}
        result[name] = {"fields": fields, "required": schema.get("required", []), "base": base, "environments": environments,
            "build": component.build.model_dump(),
            "build_authored": authored["components"][name].get("build", {}),
            "build_sources": {key: "component" if key in authored["components"][name].get("build", {}) else "adapter default"
                for key in ("checks", "command")}, "build_fields": {key: Build.model_json_schema()["properties"][key]
                for key in ("checks", "command")}}
    return result


# Edit a specific supported scope while preserving authored pins and unrelated target metadata
def edit(source: str, name: str, scope: str, values: dict, environment: str = "", field: str = "") -> dict:
    before = inspect(source)
    if name in before['option_forms']:
        return native_edit(source, before, name, scope, values, environment, field)
    contract = before["local_edit_forms"].get(name)
    if contract is None or scope not in {"build", "base", "environment"}:
        raise Refused("Choose an existing directory/static/Compose component and explicit edit scope", 422)
    if (scope == "environment" and environment not in contract["environments"]) or (scope != "environment" and environment):
        raise Refused("Choose an applicable local environment only for environment scope", 422)
    allowed = {"checks", "command"} if scope == "build" else set(contract["fields"])
    if not isinstance(values, dict) or set(values) - allowed or (field and (field not in allowed or values or
            scope == "build" or (scope == "base" and field in contract["required"]))):
        raise Refused("Choose supported fields; reset requires one optional component option or environment override and no values", 422)
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
            target = component.get("options", {}) if field else component.setdefault("options", tomlkit.table())
            if field and field in target:
                del target[field]
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
        if scope in {"build", "base"} and not field and not target and group not in tomllib.loads(source)["components"][name]:
            del component[group]
        revised = tomlkit.dumps(document)
        Workspace.model_validate(tomllib.loads(revised))
    except (ValueError, TypeError, ValidationError) as error:
        raise Refused(str(error), 422) from error
    return {**inspect(revised), "toml": revised, "diff": difference(source, revised)}


# Native explicit pins and single-field inheritance resets share the same validated draft owner
def native_edit(source: str, before: dict, name: str, scope: str, values: dict, environment: str, field: str) -> dict:
    selected = before['option_forms'][name]
    fields = selected['fields']
    if scope != 'environment' or environment not in {'', selected['environment']}:
        raise Refused('Choose the selected native environment scope', 422)
    if not isinstance(values, dict) or set(values) - fields.keys() or (field and (field not in fields or values)) or (not field and not values):
        raise Refused('Pin supported native fields or reset one environment override', 422)
    document = tomlkit.parse(source)
    component = document['components'][selected['component']]
    if field:
        target = component.get('targets', {}).get(selected['environment'], {})
        if field in target:
            del target[field]
    else:
        target = component.setdefault('targets', tomlkit.table()).setdefault(selected['environment'], tomlkit.table())
        for key, value in values.items():
            target[key] = value
    revised = tomlkit.dumps(document)
    return {**inspect(revised), 'toml': revised, 'diff': difference(source, revised)}


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
    for environment_name, environment in workspace.environments.items():
        component = workspace.component(name, environment_name)
        if component.adapter not in LOCAL_MODELS:
            continue
        expected = "infrastructure" if component.adapter == "directory" else "application"
        if environment.provider != "local" or component.kind != expected:
            raise ValueError("Guided directory/static/Compose components need a local environment and matching kind")



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
            raise Refused("Choose a component with native adapter settings", 422)
        selected = before["option_forms"][name]
        component = before["configuration"]["components"][selected["component"]]
        model = FORM_MODELS[selected["adapter"]]
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
    saved = inspect(current)
    changed = saved["digest"] != result["digest"]
    return {key: result[key] for key in ("digest", "order", "source_hash")} | {
        "status": "valid", "saved_source_hash": source_hash(current), "diff": difference(current, source),
        "impact": {"contract_changed": changed, "saved_digest": saved["digest"], "draft_digest": result["digest"],
            "effects": [
                "Promotion and rollback from the saved contract will require fresh releases.",
                "Local dependency outputs from the saved contract will no longer be reusable.",
                "Provider inspection and preview retirement require their original contract. Finish retirement before Save.",
                "Retained releases, approvals and audit remain unchanged; Save executes no deployment.",
            ] if changed else ["The deployment contract is unchanged; existing digest gates remain in effect."],
            "recovery": "Save keeps the exact prior TOML as previous-workspace.toml and retains per-Save before/draft copies in local state. "
                        "An interrupted Save requires explicit reconciliation that keeps the current file. "
                        "Restoring a retained version is another reviewed, hash-guarded Save; approvals are never rewritten."},
    }


# Exclude workspace writers across CLI and console without constructing execution authority
@contextmanager
def workspace_lock(state):
    import fcntl
    require_writable(state / 'state.db')
    descriptor = os.open(state / "workspace.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Refused("Workspace configuration or release preparation is active; retry after it finishes") from None
        yield descriptor
    finally:
        os.close(descriptor)


# Require explicit interruption reconciliation before another Save, release or retirement preparation
def require_saved(project, state):
    intent = state / 'workspace-save.json'
    if intent.exists() or intent.is_symlink():
        raise Refused('An interrupted configuration Save needs inspection and reconciliation before preparing work')
    pending = project.with_suffix('.pending')
    if pending.exists() or pending.is_symlink():
        raise Refused('A pending configuration file already exists. Inspect it first')


# Share the same guard with all cooperative configuration and release writers
@contextmanager
def boundary(engine):
    with workspace_lock(engine.state) as descriptor:
        require_saved(engine.path, engine.state)
        yield descriptor


# Keep exact recovery bytes before touching an existing authored file
def _new_text(path, source):
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8', newline='') as stream:
        stream.write(source)
        stream.flush()
        os.fsync(stream.fileno())
    sync_directory(path.parent)


# Replace the compatibility backup without following a pre-existing link or publishing partial bytes
def _backup(path, source):
    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix='.workspace-backup-')
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8', newline='') as stream:
            stream.write(source)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        sync_directory(path.parent)
    finally:
        Path(name).unlink(missing_ok=True)


# Inspect exact before/draft/current identities and the existing audit without loading a workspace
def save_recovery(project, state, *, locked=False):
    project, state = project.resolve(), state.resolve()
    store = Ledger.open_existing(state).store
    path = state / 'workspace-save.json'
    if not path.exists() and not path.is_symlink():
        pending = project.with_suffix('.pending')
        return {'available': False, 'status': 'unmanaged_pending' if pending.exists() or pending.is_symlink() else 'none',
                'reason': 'No retained Save intent. Preserve any legacy pending draft and inspect it before removing or renaming it.'}
    try:
        intent = json.loads(read_regular(path, 64 * 1024).decode('utf-8'))
        fields = {'version', 'id', 'project_path', 'state_path', 'before_hash', 'after_hash', 'old_digest',
                  'new_digest', 'created_at', 'lock_device', 'lock_inode'}
        if (set(intent) != fields or intent['version'] != 1 or not re.fullmatch(r'[0-9a-f]{32}', intent['id'])
                or any(not re.fullmatch(r'[0-9a-f]{64}', intent[key]) for key in ('before_hash', 'after_hash', 'old_digest', 'new_digest'))
                or any(type(intent[key]) is not int for key in ('created_at', 'lock_device', 'lock_inode'))):
            raise ValueError('format')
        if intent['project_path'] != str(project) or intent['state_path'] != str(state):
            return {'available': False, 'status': 'relocated', 'reason': 'Save intent belongs to its original workspace and state location. Retain this copy for inspection.'}
        copies = state / 'workspace-saves' / intent['id']
        if copies.parent.is_symlink() or copies.is_symlink():
            raise ValueError('links')
        before = read_regular(copies / 'before.toml').decode('utf-8')
        after = read_regular(copies / 'draft.toml').decode('utf-8')
        if source_hash(before) != intent['before_hash'] or source_hash(after) != intent['after_hash']:
            raise ValueError('copies')
        current_hash = source_hash(read_regular(project).decode('utf-8'))
        pending = project.with_suffix('.pending')
        pending_present = pending.exists() or pending.is_symlink()
        if pending_present and source_hash(read_regular(pending).decode('utf-8')) != intent['after_hash']:
            return {'available': False, 'status': 'pending_changed', 'reason': 'The pending file is no longer this Save draft. Preserve all files; no current source or unrelated draft was replaced.'}

        # A lock-file replacement cannot manufacture proof that the original Save stopped
        import fcntl
        descriptor = os.open(state / 'workspace.lock', os.O_RDONLY | os.O_NOFOLLOW)
        try:
            metadata = os.fstat(descriptor)
            if (metadata.st_dev, metadata.st_ino) != (intent['lock_device'], intent['lock_inode']):
                raise ValueError('lock identity')
            if not locked:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
                except BlockingIOError:
                    return {'available': False, 'status': 'active', 'reason': 'A workspace writer is active. Wait for it to stop before reconciling Save.'}
        finally:
            os.close(descriptor)

        with store.connect() as connection:
            events = [json.loads(row[0]) for row in connection.execute(
                "SELECT payload FROM audit WHERE json_extract(payload,'$.detail.operation_id')=? ORDER BY sequence", (intent['id'],))]
        updated = any(event['event'] == 'workspace.updated' and event['detail'].get('source_hash') == intent['after_hash'] for event in events)
        reconciliations = [event for event in events if event['event'] == 'workspace.save_reconciled']
        reconciled_hash = reconciliations[-1]['detail']['observed_source_hash'] if reconciliations else None
        phase = ('same_content' if current_hash == intent['before_hash'] == intent['after_hash'] else
                 'before_replace' if current_hash == intent['before_hash'] else
                 'after_replace' if current_hash == intent['after_hash'] else 'external_change')
        return {'available': True, 'status': phase, 'operation_id': intent['id'], 'source_hash': current_hash,
                'before_hash': intent['before_hash'], 'draft_hash': intent['after_hash'],
                'audit': 'committed' if updated else 'not_recorded',
                'reconciliation': 'recorded' if reconciliations else 'not_recorded', 'reconciled_source_hash': reconciled_hash,
                'recovery_directory': str(copies), 'pending_owned': pending_present,
                'reason': 'Keep the exact current file and record the interrupted Save outcome. Before/draft copies remain available; no configuration, approval or deployment will be replayed.'}
    except (OSError, ValueError, TypeError, KeyError, Refused):
        return {'available': False, 'status': 'unverified', 'reason': 'Save intent, recovery copies or ownership cannot be verified. Preserve all files; no repair or overwrite was attempted.'}


# Reconcile an observed current file, without restoring over external edits or inventing an old audit commit
def reconcile_save(project, state, operation_id, expected_hash, note):
    project, state = project.resolve(), state.resolve()
    if not re.fullmatch(r'[0-9a-f]{32}', operation_id) or not re.fullmatch(r'[0-9a-f]{64}', expected_hash) or not 10 <= len(note.strip()) <= 500:
        raise Refused('Choose an exact Save operation, current source hash and inspection note of 10–500 characters', 422)
    retained = Ledger.open_existing(state).store
    retained.verify()
    with workspace_lock(state):
        path = state / 'workspace-save.json'
        if not path.exists() and not path.is_symlink():
            with retained.connect() as connection:
                prior = connection.execute("SELECT payload FROM audit WHERE json_extract(payload,'$.event')='workspace.save_reconciled' "
                    "AND json_extract(payload,'$.detail.operation_id')=? ORDER BY sequence DESC LIMIT 1", (operation_id,)).fetchone()
            if prior:
                return {'status': 'reconciled', 'operation_id': operation_id, 'configuration_changed': False,
                        'source_hash': json.loads(prior[0])['detail']['observed_source_hash'], 'repeat': True}
            raise Refused('No matching retained Save intent exists')
        observed = save_recovery(project, state, locked=True)
        if not observed.get('available') or observed['operation_id'] != operation_id or observed['source_hash'] != expected_hash:
            raise Refused('Save evidence or current file changed. Inspect recovery again; no file was replaced')
        store = Store.open_existing(state / 'state.db', writable=True)
        repeated = observed['reconciled_source_hash'] == expected_hash
        with store.transaction() as connection:
            active = connection.execute("SELECT 1 FROM releases WHERE status NOT IN ('succeeded','failed','cancelled') LIMIT 1").fetchone()
            if active:
                raise Refused('Finish outstanding releases before reconciling a configuration Save')
            if not repeated:
                store.event(connection, int(time.time()), 'local-operator', 'workspace.save_reconciled', str(project),
                    {'operation_id': operation_id, 'observed_source_hash': expected_hash, 'phase': observed['status'],
                     'original_audit': observed['audit'], 'note': note.strip(), 'configuration_changed': False})

        # The audited observation commits first; repeated cleanup cannot invent another workspace update
        if observed['pending_owned']:
            if source_hash(read_regular(project.with_suffix('.pending')).decode('utf-8')) != observed['draft_hash']:
                raise Refused('Pending draft changed during reconciliation; preserve it and inspect again')
            project.with_suffix('.pending').unlink()
            sync_directory(project.parent)
        path.unlink()
        sync_directory(state)
        return {'status': 'reconciled', 'operation_id': operation_id, 'source_hash': expected_hash,
                'configuration_changed': False, 'recovery_directory': observed['recovery_directory'], 'repeat': repeated}


# Save the reviewed draft only while the local workspace has no outstanding delivery work
def save(engine, source: str, expected_hash: str, worker_active: bool = False) -> dict:
    proposed = inspect(source)
    if engine.gate.__class__.__name__ != "LocalGate":
        raise Refused("Only local operators may save a local workspace here", 403)
    with boundary(engine) as lock_descriptor:
        current = read_source(engine.path)
        if expected_hash != source_hash(current):
            raise Refused("The workspace changed since it was loaded. Preserve your draft and reload before saving")
        current_digest = inspect(current)["digest"]
        with engine.ledger.store.transaction() as connection:
            active = connection.execute(
                "SELECT 1 FROM releases WHERE status NOT IN ('succeeded','failed','cancelled') LIMIT 1"
            ).fetchone()
            cleanup = connection.execute(
                "SELECT 1 FROM releases WHERE json_extract(data, '$.cleanup.status') "
                "IN ('preparing','waiting','running','uncertain') LIMIT 1"
            ).fetchone()
            rollout = connection.execute("SELECT 1 FROM releases WHERE "
                "json_extract(data, '$.rollout.status') IN ('preparing','waiting','running','post-check','uncertain') OR "
                "json_extract(data, '$.rollout.retirement.status') IN ('preparing','waiting','running','uncertain') LIMIT 1").fetchone()
            if worker_active or active or cleanup or rollout:
                raise Refused("Finish, cancel, or reconcile outstanding releases, preview cleanup and rollout work before changing configuration")

            # Flush both authored versions and bind intent before touching the compatibility backup
            pending = engine.path.with_suffix(".pending")
            _new_text(pending, source)
            operation_id = uuid.uuid4().hex
            copies_root = engine.state / 'workspace-saves'
            if copies_root.is_symlink():
                raise Refused('Save recovery directory must not be a symbolic link')
            copies_root.mkdir(mode=0o700, exist_ok=True)
            copies = copies_root / operation_id
            copies.mkdir(mode=0o700)
            sync_directory(copies_root)
            _new_text(copies / 'before.toml', current)
            _new_text(copies / 'draft.toml', source)
            metadata = os.fstat(lock_descriptor)
            intent = {'version': 1, 'id': operation_id, 'project_path': str(engine.path), 'state_path': str(engine.state),
                      'before_hash': expected_hash, 'after_hash': source_hash(source), 'old_digest': current_digest,
                      'new_digest': proposed['digest'], 'created_at': int(time.time()),
                      'lock_device': metadata.st_dev, 'lock_inode': metadata.st_ino}
            private_json(engine.state / 'workspace-save.json', intent, durable=True)
            if source_hash(read_source(engine.path)) != expected_hash:
                raise Refused("The workspace changed during saving. Preserve your draft and inspect Save recovery")

            # Recheck external edits immediately before replacement; the advisory lock governs cooperative writers
            backup = engine.state / "previous-workspace.toml"
            _backup(backup, current)
            if source_hash(read_regular(engine.path).decode('utf-8')) != expected_hash:
                raise Refused('The workspace changed before replacement. Its current edits were kept; inspect Save recovery')
            os.replace(pending, engine.path)
            sync_directory(engine.path.parent)
            engine.ledger.store.event(connection, int(time.time()), "local-operator", "workspace.updated",
                                      proposed["configuration"]["name"],
                                      {"old_digest": current_digest, "new_digest": proposed["digest"],
                                       'operation_id': operation_id, 'previous_source_hash': expected_hash,
                                       'source_hash': source_hash(source)})

        # A committed audit is distinct from replaced file bytes; an interruption here remains reconcilable
        if source_hash(read_regular(engine.path).decode('utf-8')) != source_hash(source):
            raise Refused('The workspace changed after Save committed. Keep the current edits and inspect Save recovery')
        (engine.state / 'workspace-save.json').unlink()
        sync_directory(engine.state)
    return {"digest": proposed["digest"], "source_hash": proposed["source_hash"], "status": "saved", "reload": True,
            'operation_id': operation_id, 'recovery_directory': str(copies)}
