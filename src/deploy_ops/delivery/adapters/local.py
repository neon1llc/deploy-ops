# src/deploy_ops/delivery/adapters/local.py - Deploy local files or an exact rendered Compose configuration.


import json
import re
import shutil
from pathlib import Path

from ...client import private_json
from ...errors import Refused
from ..files import digest_tree, inside
from .base import Context, need, preview


# Identify the exact local mutation while allowing dependency-owned markers in the same tree
def physical_target(root: Path, environment: str, name: str, adapter: str, values: dict) -> str | None:
    if adapter == 'directory':
        return str(inside(root, f"{environment}/{need(values, 'name')}") / 'resource.json')
    if adapter == 'static':
        destination = values.get('destination', str(root / environment / name))
        return str(inside(root, destination) / 'current.json')
    return None


# Freeze static files or rendered Compose inputs before approval
def prepare(context: Context) -> dict:
    adapter = context.component.adapter
    if adapter == "directory":
        name = need(context.options, "name")
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,47}", name):
            raise Refused("Local resource names must be lowercase slugs", 422)
        return {"preview": preview(context.name, "local_directory", False), "name": name}
    if adapter == "static":
        directory = inside(context.source, context.options.get("directory", "."))
        if not directory.is_dir():
            raise Refused("Static build directory is missing", 422)
        digest_tree(directory)
        target = context.stage / "site"
        shutil.copytree(directory, target)
        destination = context.options.get("destination", str(context.deployments / context.environment / context.name))
        inside(context.deployments, destination)
        return static_prepared(context)
    # Render Compose once and reject host-level privileges before approval
    from ..docker import verify_daemon
    verify_daemon(context)
    selected = context.docker_binding
    config = need(context.options, "file")
    file = inside(context.source, config)
    rendered = json.loads(context.command(
        [selected['tools']['compose_executable']['path'], '-p', selected['project'], "-f", str(file), "config", "--format", "json"], capture=True))
    if not rendered.get("services"):
        raise Refused("Compose has no services", 422)
    for service in rendered["services"].values():
        if service.get("build") or not re.fullmatch(r"[^\s]+@sha256:[0-9a-f]{64}", service.get("image", "")):
            raise Refused("Compose delivery requires prebuilt images pinned by digest", 422)
        if service.get("privileged") or service.get("network_mode") == "host" or service.get("pid") == "host":
            raise Refused("Privileged and host-namespace Compose services need an external reviewed workflow", 422)
        if any(volume.get("type") == "bind" for volume in service.get("volumes", []) if isinstance(volume, dict)):
            raise Refused("Host bind mounts are outside the built-in Compose adapter", 422)
        # A named volume can hide host mounts or another storage driver behind its declaration
        for volume in service.get('volumes', []):
            if not isinstance(volume, dict) or volume.get('type') != 'volume' or not volume.get('source'):
                continue
            declaration = (rendered.get('volumes') or {}).get(volume['source']) or {}
            if declaration.get('driver_opts') or declaration.get('external') or declaration.get('driver', 'local') != 'local':
                raise Refused('Compose storage drivers, driver options and external volumes need an external reviewed workflow', 422)
    private_json(context.stage / "compose.json", rendered)
    reviewed = preview(context.name, "application_compose")
    reviewed['changes'][0]['address'] = f"compose:{selected['project']}@{selected['daemon_id']}"
    return {"preview": reviewed, "project": selected['project'], 'docker_binding': selected}


# Activate only the reviewed local artifact and target
def apply(context: Context, prepared: dict) -> dict:
    adapter = context.component.adapter
    if adapter == "directory":
        destination = inside(context.deployments, f"{context.environment}/{prepared['name']}")
        destination.mkdir(parents=True, exist_ok=True, mode=0o700)
        private_json(destination / "resource.json", {"name": prepared["name"], "environment": context.environment})
        return {"path": str(destination), "name": prepared["name"]}
    if adapter == "static":
        site = context.stage / "site"
        if digest_tree(site) != prepared["site_digest"]:
            raise Refused("Static build changed after review")
        destination = inside(context.deployments, prepared["destination"])
        if pointer(context.deployments, str(destination)) != prepared.get("previous_pointer"):
            raise Refused("Local deployment changed after preparation. Review a fresh release")
        # Create a new release directory before replacing the active pointer
        releases = destination / "releases"
        releases.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = releases / context.release_id
        if path.exists():
            raise Refused("Release directory already exists. Reconcile before retrying")
        shutil.copytree(site, path)
        if digest_tree(path) != prepared['site_digest']:
            raise Refused('Copied static candidate differs from the reviewed artifact')
        if prepared.get('activation') == 'separate':
            return {'path': str(path), 'digest': prepared['site_digest'], 'activation': 'candidate'}
        # Replace only the small pointer after the entire release directory is ready
        private_json(destination / "current.json", {"release": context.release_id, "path": str(path),
                                                    "digest": prepared["site_digest"]})
        return {"path": str(path), "digest": prepared["site_digest"], "pointer": str(destination / "current.json")}
    from ..docker import verify_daemon
    if context.docker_binding != prepared.get('docker_binding'):
        raise Refused('Reviewed Docker binding is unavailable; prepare fresh evidence', 422)
    verify_daemon(context)
    context.command([context.docker_binding['tools']['compose_executable']['path'], "-p", prepared["project"], "-f", str(context.stage / "compose.json"),
                     "up", "--detach", "--no-build", "--wait", "--wait-timeout", "120"])
    return {"project": prepared["project"]}


# Read the activation baseline used to reject stale static updates
def pointer(root: Path, destination: str):
    """Bind a static deployment to the previous active pointer as well as its new artifact."""
    path = inside(root, destination) / "current.json"
    return json.loads(path.read_text()) if path.exists() else None


# Freeze identical target/activation semantics for source builds and promoted artifacts
def static_prepared(context: Context) -> dict:
    destination = context.options.get('destination', str(context.deployments / context.environment / context.name))
    inside(context.deployments, destination)
    previous = pointer(context.deployments, destination)
    result = {'preview': preview(context.name, 'application_static', False), 'destination': destination,
              'site_digest': digest_tree(context.stage / 'site'), 'previous_pointer': previous}
    mode = context.options.get('activation', 'direct')
    if mode not in {'direct', 'separate'}:
        raise Refused('Choose direct or separate static activation', 422)
    if mode == 'separate':
        if previous is None:
            raise Refused('Separate activation needs an existing baseline; deploy the first revision with direct activation', 422)
        result['activation'] = 'separate'
    return result
