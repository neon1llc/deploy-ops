# src/deploy_ops/delivery/files.py - Freeze source inputs and keep paths inside an owned workspace.


import hashlib
import io
import os
import shutil
import stat
import subprocess
import tarfile
from pathlib import Path

from ..config import fingerprint
from ..errors import Refused
from .spec import Repository

# Exclude transient working-tree files only before creating a reviewed snapshot
IGNORED = {".git", ".deploy-ops", ".terraform", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache"}

# Bound ordinary retained reads independently of source snapshot limits
MAX_READ_BYTES = 2 * 1024 * 1024


# Read one bounded ordinary file without following its final link or accepting an in-place write
def read_regular(path: Path, limit: int = MAX_READ_BYTES) -> bytes:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, 'rb') as handle:
            before = os.fstat(handle.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
                raise Refused('Retained input must be a bounded regular file', 422)
            content = handle.read(limit + 1)
            after = os.fstat(handle.fileno())
            current = path.lstat()
            keys = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
            if len(content) > limit or any(getattr(before, key) != getattr(item, key) for item in (after, current) for key in keys):
                raise Refused('Retained input changed during inspection; read it again', 409)
            return content
    except FileNotFoundError:
        # Observation distinguishes a missing deployed file from unreadable evidence
        raise
    except OSError:
        raise Refused('Retained input is unavailable; no repair was attempted', 422) from None


# Reject path escapes and linked ancestors without rewriting retained absolute paths
def checked_path(root: Path, value: str | Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = root / path
    if not path.is_relative_to(root) or '..' in path.parts:
        raise Refused('Observed path is outside the deployment root')
    for item in [root, *reversed(path.parents), path]:
        if item.is_relative_to(root) and item.is_symlink():
            raise Refused('Symbolic links are not supported in observed paths')
    return path


# Inventory bytes and empty directories for preview retirement and coherent private capture
def inventory_tree(root: Path, max_bytes: int = 100_000_000, max_entries: int = 10000) -> dict:
    files, directories, total = {}, [], 0
    if not stat.S_ISDIR(root.lstat().st_mode):
        raise Refused('Retained tree is not an ordinary directory')

    # Propagate inaccessible or changing entries rather than silently omitting them
    def walk(folder):
        nonlocal total
        with os.scandir(folder) as entries:
            for entry in entries:
                if len(files) + len(directories) >= max_entries:
                    raise Refused('Retained inventory exceeds its entry limit')
                metadata = entry.stat(follow_symlinks=False)
                if not (stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode)):
                    raise Refused('Retained tree contains a symbolic link or unsupported file type')
                path = checked_path(root, Path(entry.path))
                relative = path.relative_to(root).as_posix()
                if stat.S_ISDIR(metadata.st_mode):
                    directories.append(relative)
                    walk(path)
                elif stat.S_ISREG(metadata.st_mode):
                    content = read_regular(path, min(100_000_000, max_bytes - total))
                    total += len(content)
                    files[relative] = {'sha256': hashlib.sha256(content).hexdigest(), 'size': len(content),
                                       'executable': bool(metadata.st_mode & 0o111)}
                else:
                    raise Refused('Retained tree contains a link or unsupported file type')
    walk(root)
    return {'files': dict(sorted(files.items())), 'directories': sorted(directories), 'bytes': total}


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
        if source_exclusions and any(part in IGNORED or part == '.env' or part.startswith('.env.')
                                     or part.endswith(('.pem', '.key')) for part in relative.parts):
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
        before = digest_tree(source, source_exclusions=True)
        shutil.copytree(source, destination, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns(*IGNORED, ".env", ".env.*", "*.pem", "*.key"))
        if before != digest_tree(source, source_exclusions=True) or before != digest_tree(destination):
            raise Refused("Current local files changed during capture. Retry after edits finish", 409)
    else:
        raise Refused("Initialize Git or explicitly enable local working-tree snapshots", 422)
    return {"revision": revision, "digest": digest_tree(destination)}


# Require owner-only access even when the directory already exists
def private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700)
