# src/deploy_ops/delivery/adapters/local.py - Deploy local files or an exact rendered Compose configuration.


import json
import re
import shutil
from pathlib import Path

from ...client import private_json
from ...errors import Refused
from ..files import digest_tree, inside
from .base import Context, need, preview


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
        return {"preview": preview(context.name, "application_static", False),
                "destination": destination, "site_digest": digest_tree(target),
                "previous_pointer": pointer(context.deployments, destination)}
    # Render Compose once and reject host-level privileges before approval
    config = need(context.options, "file")
    file = inside(context.source, config)
    rendered = json.loads(context.command(
        ["docker", "compose", "-f", str(file), "config", "--format", "json"], capture=True))
    if not rendered.get("services"):
        raise Refused("Compose has no services", 422)
    for service in rendered["services"].values():
        if service.get("build") or not re.fullmatch(r"[^\s]+@sha256:[0-9a-f]{64}", service.get("image", "")):
            raise Refused("Compose delivery requires prebuilt images pinned by digest", 422)
        if service.get("privileged") or service.get("network_mode") == "host" or service.get("pid") == "host":
            raise Refused("Privileged and host-namespace Compose services need an external reviewed workflow", 422)
        if any(volume.get("type") == "bind" for volume in service.get("volumes", []) if isinstance(volume, dict)):
            raise Refused("Host bind mounts are outside the built-in Compose adapter", 422)
    private_json(context.stage / "compose.json", rendered)
    return {"preview": preview(context.name, "application_compose"),
            "project": context.options.get("project", f"{context.environment}-{context.name}")}


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
        # Replace only the small pointer after the entire release directory is ready
        private_json(destination / "current.json", {"release": context.release_id, "path": str(path),
                                                    "digest": prepared["site_digest"]})
        return {"path": str(path), "digest": prepared["site_digest"], "pointer": str(destination / "current.json")}
    context.command(["docker", "compose", "-p", prepared["project"], "-f", str(context.stage / "compose.json"),
                     "up", "--detach", "--no-build", "--wait", "--wait-timeout", "120"])
    return {"project": prepared["project"]}


# Read the activation baseline used to reject stale static updates
def pointer(root: Path, destination: str):
    """Bind a static deployment to the previous active pointer as well as its new artifact."""
    path = inside(root, destination) / "current.json"
    return json.loads(path.read_text()) if path.exists() else None
