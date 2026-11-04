# src/deploy_ops/delivery/files.py - Freeze source inputs and keep paths inside an owned workspace.


import hashlib
import io
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

from ..config import fingerprint
from ..errors import Refused
from .spec import Repository

# Exclude transient working-tree files only before creating a reviewed snapshot
IGNORED = {".git", ".deploy-ops", ".terraform", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache"}


# Resolve paths before enforcing the registered workspace boundary
def inside(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise Refused("Path escapes its registered workspace", 422)
    return path


# Bind every retained file while rejecting links and oversized snapshots
def digest_tree(root: Path, *, source_exclusions: bool = False) -> str:
    entries = []
    size = 0
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        # Exclusions apply only before copying a working tree, never to reviewed evidence
        if source_exclusions and any(part in IGNORED for part in relative.parts):
            continue
        if path.is_symlink():
            raise Refused("Source snapshots cannot contain symbolic links", 422)
        if path.is_file():
            size += path.stat().st_size
            if size > 100_000_000 or len(entries) >= 10000:
                raise Refused("Source snapshot exceeds the 100 MB or 10,000-file limit", 422)
            entries.append([str(relative), hashlib.sha256(path.read_bytes()).hexdigest()])
    return fingerprint(entries)


# Copy immutable source bytes into a newly owned directory
def snapshot(repository: Repository, base: Path, destination: Path) -> dict:
    """Use committed Git contents by default, with explicit local-only working-tree support."""
    source = inside(base, repository.path)
    if not source.is_dir():
        raise Refused("Repository directory is missing", 422)
    if destination.exists():
        raise Refused("Refusing to overwrite a source snapshot", 409)
    destination.mkdir(parents=True, mode=0o700)
    if (source / ".git").exists() and not repository.allow_uncommitted:
        revision = subprocess.run(
            ["git", "rev-parse", "--verify", repository.revision + "^{commit}"],
            cwd=source, capture_output=True, text=True, check=True,
        ).stdout.strip()
        archive = subprocess.run(["git", "archive", revision], cwd=source,
                                 capture_output=True, check=True).stdout
        if len(archive) > 110_000_000:
            raise Refused("Repository archive exceeds the snapshot limit", 422)
        # Extract only regular committed files within the snapshot boundary
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            total = 0
            for member in tar:
                target = inside(destination, member.name)
                if member.issym() or member.islnk() or not (member.isdir() or member.isfile()):
                    raise Refused("Repository archive contains an unsupported file type", 422)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                total += member.size
                if total > 100_000_000:
                    raise Refused("Expanded repository exceeds the snapshot limit", 422)
                target.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(member) as handle:
                    target.write_bytes(handle.read())
                target.chmod(0o700 if member.mode & 0o111 else 0o600)
    elif repository.allow_uncommitted:
        revision = "working-tree"
        digest_tree(source, source_exclusions=True)
        shutil.copytree(source, destination, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns(*IGNORED, ".env", ".env.*", "*.pem", "*.key"))
    else:
        raise Refused("Initialize Git or explicitly enable local working-tree snapshots", 422)
    return {"revision": revision, "digest": digest_tree(destination)}


# Require owner-only access even when the directory already exists
def private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700)
