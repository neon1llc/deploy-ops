# src/deploy_ops/delivery/previews.py - Own isolated static previews and exact, manually reviewed cleanup.

import hashlib
import json
import os
import re
import stat
import time
import uuid
from pathlib import Path

from .. import __version__
from ..client import private_json
from ..config import fingerprint
from ..errors import Refused
from ..models import Proposal
from .gate import LocalGate, target_name
from .observation import checked_path, read_regular, reviewed, tree_digest


def _bytes(value):
    return (json.dumps(value, indent=2, allow_nan=False) + "\n").encode()


def validate_creation(engine, graph, owner, hours, retain, with_dependencies):
    if not isinstance(engine.gate, LocalGate) or not with_dependencies:
        raise Refused("Static previews require local mode and their complete dependency graph", 422)
    if not isinstance(owner, str) or not owner.strip() or len(owner) > 200:
        raise Refused("Supply a preview owner label of 1 to 200 characters", 422)
    if type(hours) is not int or not 1 <= hours <= 72 or type(retain) is not bool:
        raise Refused("Preview expiry must be 1 to 72 hours with an explicit retention choice", 422)
    adapters = [node["adapter"] for node in graph["components"]]
    if adapters.count("static") != 1 or adapters.count("directory") > 1 or set(adapters) - {"static", "directory"}:
        raise Refused("A preview supports one static application and at most one directory foundation", 422)
    for node in graph["components"]:
        component = engine.spec.component(node["name"], graph["environment"])
        destination = component.options.get("destination")
        if destination is not None and not (isinstance(destination, str) and re.fullmatch(
                r"\$\{outputs\.[a-z][a-z0-9-]*\.path\}", destination)):
            raise Refused("Preview destinations must be derived from their own dependency or the isolated default", 422)
    return {"owner": owner.strip(), "expires_at": int(time.time()) + hours * 3600,
            "retain_data": retain, "contract": "generated-static-v1"}


def initialize(engine, release):
    metadata = release["preview"]
    metadata.update(id=release["id"], environment=release["environment"], config_digest=release["config_digest"],
                    source_digest=release["source_digest"])
    parent = checked_path(engine.state, engine.state / "previews")
    parent.mkdir(exist_ok=True, mode=0o700)
    root = parent / release["id"]
    root.mkdir(mode=0o700)
    # Ledger serialization sorts keys; keep the ownership bytes stable across a restart.
    release["preview"] = dict(sorted(metadata.items()))
    private_json(root / ".ownership.json", release["preview"])


def namespace(engine, release):
    metadata = release.get("preview")
    if not metadata or metadata.get("id") != release["id"] or metadata.get("config_digest") != release["config_digest"]:
        raise Refused("This release does not own an isolated preview")
    root = checked_path(engine.state, engine.state / "previews" / release["id"])
    if read_regular(root / ".ownership.json", 65536) != _bytes(metadata):
        raise Refused("Preview ownership evidence changed")
    return root


def active(engine, release):
    namespace(engine, release)
    if release.get("preview_retiring") or int(time.time()) >= release["preview"]["expires_at"]:
        raise Refused("This preview is expired or retiring. Create a fresh isolated preview")


def list_previews(engine):
    with engine.ledger.store.connect() as connection:
        releases = [json.loads(row[0]) for row in connection.execute("SELECT data FROM releases ORDER BY rowid DESC")]
    return [{"release_id": release["id"], "deployment_status": release["status"], **release["preview"],
             "expired": int(time.time()) >= release["preview"]["expires_at"], "cleanup": release.get("cleanup")}
            for release in releases if release.get("preview")]


def _scan(root):
    files, directories, total = {}, [], 0
    def walk(folder):
        nonlocal total
        with os.scandir(folder) as entries:
            for entry in entries:
                if len(files) + len(directories) >= 10000:
                    raise Refused("Preview inventory exceeds 10,000 entries")
                path = Path(entry.path)
                relative = path.relative_to(root).as_posix()
                mode = entry.stat(follow_symlinks=False).st_mode
                if stat.S_ISDIR(mode):
                    directories.append(relative)
                    walk(path)
                elif stat.S_ISREG(mode):
                    content = read_regular(checked_path(root, path), 100_000_000 - total)
                    total += len(content)
                    files[relative] = hashlib.sha256(content).hexdigest()
                else:
                    raise Refused("Preview contains a symbolic link or unsupported entry; inspect it before cleanup")
    walk(root)
    return {"files": dict(sorted(files.items())), "directories": sorted(directories)}


def inventory(engine, release):
    root = namespace(engine, release)
    actual = _scan(root)
    expected = {".ownership.json": hashlib.sha256(_bytes(release["preview"])).hexdigest()}
    expected_dirs = set()
    def add(path, digest):
        relative = checked_path(root, path).relative_to(root)
        if relative.as_posix() in expected and expected[relative.as_posix()] != digest:
            raise Refused("Preview components share conflicting target evidence")
        expected[relative.as_posix()] = digest
        expected_dirs.update(parent.as_posix() for parent in relative.parents if str(parent) != ".")
    for name, node in release["nodes"].items():
        if node.get("status") != "succeeded":
            continue
        envelope = reviewed(engine, release, name)
        if envelope["config_digest"] != release["config_digest"] or envelope["adapter"] != node["adapter"]:
            raise Refused("Preview baseline differs from its registered contract")
        prepared = envelope["prepared"]
        if node["adapter"] == "directory":
            marker = root / release["environment"] / prepared["name"] / "resource.json"
            add(marker, hashlib.sha256(_bytes({"name": prepared["name"], "environment": release["environment"]})).hexdigest())
        elif node["adapter"] == "static":
            destination = checked_path(root, prepared["destination"])
            deployed = destination / "releases" / release["id"]
            artifacts = checked_path(engine.state, engine.state / "releases" / release["id"] / name / "artifacts")
            if tree_digest(artifacts) != envelope["artifact_tree"] or tree_digest(artifacts / "site") != prepared["site_digest"]:
                raise Refused("Retained static artifact differs from its reviewed evidence")
            source = _scan(artifacts / "site")
            for relative, digest in source["files"].items():
                add(deployed / relative, digest)
            expected_dirs.add(deployed.relative_to(root).as_posix())
            for relative in source["directories"]:
                expected_dirs.add((deployed / relative).relative_to(root).as_posix())
            pointer = {"release": release["id"], "path": str(deployed), "digest": prepared["site_digest"]}
            add(destination / "current.json", hashlib.sha256(_bytes(pointer)).hexdigest())
        else:
            raise Refused("This preview adapter has no cleanup contract")
    retained = []
    delete = []
    for path, digest in actual["files"].items():
        if path == ".ownership.json":
            continue
        if release["preview"]["retain_data"] or expected.get(path) != digest:
            retained.append({"path": path, "reason": "retention requested" if release["preview"]["retain_data"] else "unknown or modified file"})
        else:
            delete.append({"path": path, "digest": digest})
    for path in actual["directories"]:
        if path not in expected_dirs:
            retained.append({"path": path, "reason": "unrecognized directory"})
    remove_dirs = [path for path in actual["directories"] if path in expected_dirs and not any(
        item["path"] == path or item["path"].startswith(path + "/") for item in retained)]
    return {"schema_version": 1, "release_id": release["id"], "ownership": release["preview"],
            "observed": actual, "delete": delete, "retain": retained,
            "remove_directories": sorted(remove_dirs, key=lambda path: len(Path(path).parts), reverse=True),
            "retain_ownership_record": True}


def _eligible(engine, release):
    if not isinstance(engine.gate, LocalGate) or not release.get("preview"):
        raise Refused("Only an owned local preview can be cleaned up", 422)
    if release["config_digest"] != engine.spec.digest or not engine.spec.environments[release["environment"]].preview:
        raise Refused("Restore the preview's reviewed workspace contract before cleanup")
    if release["status"] not in {"succeeded", "failed", "cancelled"}:
        raise Refused("Finish or reconcile the preview deployment before preparing cleanup")


def prepare_cleanup(engine, release_id):
    scope = "release:" + release_id
    engine.ledger.lock(scope, release_id)
    try:
        release = engine.ledger.get(release_id)
        _eligible(engine, release)
        previous = release.get("cleanup", {})
        if previous.get("status") not in (None, "reconciled"):
            raise Refused("Inspect the existing cleanup operation before preparing another")
        plan = inventory(engine, release)
        operation = uuid.uuid4().hex
        path = engine.state / "cleanup" / operation / "prepared.json"
        private_json(path, plan)
        artifact_digest = hashlib.sha256(_bytes(plan)).hexdigest()
        # The same target/controller owns deletion; opt-in preview targets always require manual authority.
        component = release["order"][-1]
        proposal = Proposal(target=target_name(engine.spec.name, release["environment"], component),
                            engine="delivery", artifact_digest=artifact_digest, tool_version=__version__,
                            preview={"config_digest": engine.spec.digest, "preview": {
                                "changes": [{"address": release_id, "resource_type": "local_preview", "action": "delete"}],
                                "findings": [], "manual_only": True}})
        source = {**release, "id": operation, "source_digest": fingerprint(plan)}
        release["cleanup"] = {"id": operation, "status": "preparing", "artifact_digest": artifact_digest,
                              "source": source["source_digest"], "plan": plan}
        engine.ledger.save(release, "preview.cleanup.preparing", {"operation": operation})
        request = engine.gate.submit(proposal, source)
        release["cleanup"].update(status="waiting", request_id=request["id"], request_digest=request["digest"])
        return engine.ledger.save(release, "preview.cleanup.prepared", {"operation": operation})
    finally:
        engine.ledger.unlock(scope, release_id)


def approve_cleanup(engine, release_id, digest):
    release = engine.ledger.get(release_id)
    _eligible(engine, release)
    cleanup = release.get("cleanup", {})
    if cleanup.get("status") != "waiting" or digest != cleanup.get("request_digest"):
        raise Refused("Review the current cleanup request and exact digest")
    return engine.gate.approve(cleanup["request_id"], digest)


def _unlink(root, relative, digest):
    # Open parent directories without following symlinks; unlink stays inside those owned directory handles.
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        parts = Path(relative).parts
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        file = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
        with os.fdopen(file, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > 100_000_000:
                raise Refused("Cleanup entry changed type or size")
            if hashlib.sha256(stream.read(100_000_001)).hexdigest() != digest:
                raise Refused("Cleanup entry changed after approval")
        current = os.stat(parts[-1], dir_fd=descriptor, follow_symlinks=False)
        if (before.st_ino, before.st_dev, before.st_mtime_ns, before.st_size) != (current.st_ino, current.st_dev, current.st_mtime_ns, current.st_size):
            raise Refused("Cleanup entry changed during inspection")
        os.unlink(parts[-1], dir_fd=descriptor)
    finally:
        os.close(descriptor)


def _rmdir(root, relative):
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        parts = Path(relative).parts
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        os.rmdir(parts[-1], dir_fd=descriptor)
    finally:
        os.close(descriptor)


def execute_cleanup(engine, release_id):
    scope = "release:" + release_id
    engine.ledger.lock(scope, release_id)
    try:
        release = engine.ledger.get(release_id)
        _eligible(engine, release)
        cleanup = release.get("cleanup", {})
        if cleanup.get("status") != "waiting":
            raise Refused("Prepare and approve a fresh cleanup first")
        path = checked_path(engine.state, engine.state / "cleanup" / cleanup["id"] / "prepared.json")
        content = read_regular(path, 4_000_000)
        if hashlib.sha256(content).hexdigest() != cleanup["artifact_digest"]:
            raise Refused("Reviewed cleanup artifact changed")
        plan = json.loads(content)
        request = engine.gate.status(cleanup["request_id"])
        source = {**release, "id": cleanup["id"], "source_digest": cleanup["source"]}
        lease = engine.gate.claim(request, source)
        cleanup.update(status="running", worker_pid=os.getpid())
        release["preview_retiring"] = True
        engine.ledger.save(release, "preview.cleanup.running", {"operation": cleanup["id"]})
        deleted = []
        try:
            if inventory(engine, release) != plan:
                raise Refused("Preview changed after cleanup preparation")
            root = namespace(engine, release)
            for entry in plan["delete"]:
                engine.gate.heartbeat(lease)
                _unlink(root, entry["path"], entry["digest"])
                deleted.append(entry["path"])
            for relative in plan["remove_directories"]:
                engine.gate.heartbeat(lease)
                _rmdir(root, relative)
            # Keep an ownership tombstone even when all generated content is gone, so interrupted
            # completion can be inspected and replanned without adopting an unmarked directory.
            result = engine.gate.complete(lease, "succeeded")
            if result["status"] != "succeeded":
                raise Refused("Cleanup authority outcome needs reconciliation")
            cleanup.update(status="retired-retained" if plan["retain"] else "retired", deleted=deleted,
                           completed_at=int(time.time()))
            return engine.ledger.save(release, "preview.retired", {"operation": cleanup["id"], "retained": plan["retain"]})
        except Exception as error:
            reason = str(error) if isinstance(error, Refused) else type(error).__name__
            cleanup.update(status="uncertain", deleted=deleted, reason=reason)
            engine.ledger.save(release, "preview.cleanup.uncertain", {"operation": cleanup["id"], "deleted": deleted, "reason": reason})
            try:
                engine.gate.complete(lease, "aborted")
            except Exception:
                pass
            raise Refused("Cleanup is uncertain. Inspect remaining files and reconcile before preparing another plan") from None
    finally:
        engine.ledger.unlock(scope, release_id)


def reconcile_cleanup(engine, release_id, note, confirm_removed=False):
    if not isinstance(note, str) or not 10 <= len(note.strip()) <= 500:
        raise Refused("Record an inspection note of 10 to 500 characters", 422)
    scope = "release:" + release_id
    engine.ledger.recovery_lock(scope, release_id)
    try:
        release = engine.ledger.get(release_id)
        _eligible(engine, release)
        cleanup = release.get("cleanup", {})
        if cleanup.get("status") not in {"preparing", "waiting", "running", "uncertain"}:
            raise Refused("Only pending or interrupted cleanup needs reconciliation")
        if confirm_removed:
            root = checked_path(engine.state, engine.state / "previews" / release_id)
            if root.exists() or cleanup["plan"]["retain"]:
                raise Refused("Confirm removal only after inspecting an absent namespace with no retained data")
        if not cleanup.get("request_id"):
            # A crash after submit but before saving the returned ID must not leave an approvable orphan.
            with engine.ledger.store.connect() as connection:
                for row in connection.execute("SELECT data FROM requests"):
                    candidate = json.loads(row[0])
                    manifest = candidate["manifest"]
                    if manifest["source"]["run_id"] == cleanup["id"] and manifest["artifact_digest"] == cleanup["artifact_digest"]:
                        cleanup["request_id"], cleanup["request_digest"] = candidate["id"], candidate["digest"]
                        break
        if cleanup.get("request_id"):
            request = engine.gate.status(cleanup["request_id"])
            if request["status"] in {"running", "uncertain"}:
                engine.gate.recover(request["id"], note)
            elif request["status"] in {"pending", "approved"}:
                engine.gate.approve(request["id"], request["digest"], "reject")
        cleanup.update(status="retired-inspected" if confirm_removed else "reconciled", note=note)
        return engine.ledger.save(release, "preview.cleanup.reconciled", {"operation": cleanup["id"], "note": note})
    finally:
        engine.ledger.unlock(scope, release_id)
