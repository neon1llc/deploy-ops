# src/deploy_ops/delivery/backup.py - Capture a coherent private ledger and owned files without reviving authority.


import hashlib
import json
import os
import re
import sqlite3
import time
import uuid
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal

from pydantic import Field, ValidationError

from .. import __version__
from ..client import private_json, sync_directory
from ..config import fingerprint
from ..errors import Refused
from ..store import RESTORE_MARKER, Store
from .configuration import inspect as inspect_config, read_source, require_saved, source_hash, workspace_lock
from .evidence import Digest, Identifier, Name, Public, Text
from .files import checked_path, digest_tree, inventory_tree, location, read_regular
from .ledger import Ledger
from .retained import Retained


# Bound the first private restore contract; this is not an unbounded machine backup
MAX_BYTES = 512_000_000
MAX_FILE_BYTES = 100_000_000
MAX_ENTRIES = 20000
MAX_MANIFEST_BYTES = 16_000_000
JOURNALS = {'ecs-operation.json', 'container-apps-operation.json', 'cloud-run-operation.json', 'cloudformation-operation.json', 'arm-operation.json'}
RESTORE_MANIFEST = '.deploy-ops-backup-manifest.json'


# Suggest new private targets without replacing a path the operator has already chosen
def options(project: Path, state: Path) -> dict:
    stamp = time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8]
    root = Path.home() / '.local/share/deploy-ops'
    return {'project': str(location(project)), 'state': str(location(state)),
            'backup_directory': str(root / 'backups' / stamp), 'workspace_directory': str(root / 'restores' / stamp),
            'state_directory': '', 'max_bytes': MAX_BYTES, 'max_file_bytes': MAX_FILE_BYTES,
            'max_entries': MAX_ENTRIES, 'restore_mode': 'inspection-only', 'execution_admitted': False}


# A missing current file permits narrower retained history without inventing a configuration
def read_workspace(project: Path) -> str | None:
    try:
        return read_source(project)
    except FileNotFoundError:
        return None


# Bind each exact copied file while retaining executable intent with private permissions
class File(Public):
    path: Text
    sha256: Digest
    size: Annotated[int, Field(ge=0, le=MAX_FILE_BYTES)]
    executable: bool


# Retain the same audit verifier's exact prefix identity
class Audit(Public):
    events: Annotated[int, Field(ge=0)]
    head: Digest
    format: Digest


# Keep restore location, completeness and authority admission independent
class Manifest(Public):
    format: Literal['deploy-ops.backup'] = 'deploy-ops.backup'
    version: Literal[1] = 1
    id: Identifier
    toolkit_version: Name
    captured_at: int
    project_path: Text
    state_path: Text
    workspace_sha256: Digest | None
    config_digest: Digest | None
    audit: Audit
    release_count: Annotated[int, Field(ge=0, le=10000)]
    capture: Literal['complete', 'partial']
    authority: Literal['historical-only; execution-not-admitted'] = 'historical-only; execution-not-admitted'
    consistency: Literal['reserved-ledger-snapshot-and-owned-files'] = 'reserved-ledger-snapshot-and-owned-files'
    files: Annotated[list[File], Field(min_length=1, max_length=MAX_ENTRIES)]
    directories: Annotated[list[Text], Field(max_length=MAX_ENTRIES)]
    missing: Annotated[list[Text], Field(max_length=MAX_ENTRIES)]
    unavailable: Annotated[list[Text], Field(max_length=MAX_ENTRIES)]
    omitted: Annotated[list[Text], Field(max_length=MAX_ENTRIES)]
    sha256: Digest


# Bind a restored copy to explicit local targets without making historical rows current authority
class RestoreReceipt(Public):
    version: Literal[1] = 1
    backup_id: Identifier
    manifest_sha256: Digest
    original_project: Text
    original_state: Text
    restored_project: Text
    restored_state: Text
    mode: Literal['inspection-only'] = 'inspection-only'
    authority: Literal['not-admitted'] = 'not-admitted'


# Accept only normalized relative payload names, never paths supplied as restoration destinations
def relative(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if (path.is_absolute() or str(path) != value or not path.parts or '..' in path.parts
            or '\\' in value or len(path.parts) > 128):
        raise Refused('Backup contains an unsafe relative path', 422)
    return path


# Preserve the narrow owned-file contract even if an imported manifest was rehashed
def allowed(value: str) -> bool:
    parts = relative(value).parts
    if parts == ('workspace.toml',) or parts == ('state',):
        return True
    if parts[0] != 'state' or len(parts) < 2:
        return False
    if parts[1] in {'state.db', 'previous-workspace.toml'}:
        return len(parts) == 2
    if parts[1] in {'deployments', 'previews'}:
        return True
    if parts[1] in {'cleanup', 'workspace-saves'}:
        if len(parts) == 2:
            return True
        if not re.fullmatch(r'[0-9a-f]{32}', parts[2]):
            return False
        return len(parts) == 3 or (len(parts) == 4 and parts[3] in
            ({'prepared.json'} if parts[1] == 'cleanup' else {'before.toml', 'draft.toml'}))
    if parts[1] != 'releases':
        return False
    if len(parts) == 2:
        return True
    if not re.fullmatch(r'[0-9a-f]{32}', parts[2]):
        return False
    if len(parts) == 3:
        return True
    if not re.fullmatch(r'[a-z][a-z0-9-]{0,47}', parts[3]):
        return False
    if len(parts) == 4 or parts[3] == 'repositories':
        return True
    return parts[4] in {'source', 'artifacts'} or (len(parts) == 5 and parts[4] in {'prepared.json', *JOURNALS})


# Create new private bytes only; existing files are never restoration targets
def write_file(path: Path, content: bytes, executable=False):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o700 if executable else 0o600)
    with os.fdopen(descriptor, 'wb') as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


# Refuse unknown live ownership rather than clearing a lock to obtain a backup
def quiescent(connection):
    if not {'scope', 'release_id'}.issubset({row['name'] for row in connection.execute('PRAGMA table_info(release_locks)')}):
        raise Refused('Retained state has no supported lock inventory for coherent capture; history remains readable', 422)
    if connection.execute('SELECT 1 FROM release_locks LIMIT 1').fetchone():
        raise Refused('A retained operation lock exists. Inspect it through its original owner before coherent capture')
    if connection.execute("SELECT 1 FROM releases WHERE status IN ('running','preparing','cancel_requested') OR "
                          "json_extract(data,'$.cleanup.status') IN ('preparing','running') LIMIT 1").fetchone():
        raise Refused('A worker outcome is not quiescent. Finish or reconcile it before coherent capture')
    if connection.execute("SELECT 1 FROM requests WHERE status='running' LIMIT 1").fetchone():
        raise Refused('A controller execution is still running; no coherent capture was admitted')
    if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='notification_obligations'").fetchone():
        if connection.execute("SELECT 1 FROM notification_obligations WHERE claim!='' LIMIT 1").fetchone():
            raise Refused('A paging claim is retained. Inspect its remote outcome before coherent capture')


# Select known owned trees without traversing credential homes or arbitrary state directories
def inventory(project: Path, state: Path, releases: list[dict]) -> dict:
    files, directories, missing, unavailable, omitted = {}, {'state'}, [], [], []
    total = 0
    retained = None
    if not project.exists() and not project.is_symlink():
        missing.append('workspace.toml')

    # Retain empty directories as well as immutable bytes and local runtime data
    def tree(path, payload):
        nonlocal total
        if not path.exists() and not path.is_symlink():
            missing.append(payload)
            return
        checked_path(state, path)
        result = inventory_tree(path, MAX_BYTES - total, MAX_ENTRIES - len(files) - len(directories))
        directories.add(payload)
        directories.update(payload + '/' + name for name in result['directories'])
        for name, item in result['files'].items():
            files[payload + '/' + name] = item
        total += result['bytes']

    # A missing expected file is explicit partial history, not silently successful restore evidence
    def file(path, payload, expected=True):
        nonlocal total
        if not path.exists() and not path.is_symlink():
            if expected:
                missing.append(payload)
            return
        checked_path(state, path)
        data = read_regular(path, min(MAX_FILE_BYTES, MAX_BYTES - total))
        total += len(data)
        files[payload] = {'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data),
                          'executable': bool(path.stat().st_mode & 0o111)}
        directories.update(str(parent) for parent in PurePosixPath(payload).parents if str(parent) != '.')

    for release in releases:
        identifier = release['id']
        if not re.fullmatch(r'[0-9a-f]{32}', identifier):
            raise Refused('Unsupported retained release identity', 422)
        root = state / 'releases' / identifier
        payload = 'state/releases/' + identifier
        directories.update({'state/releases', payload})
        if (root / 'repositories').exists() or (root / 'repositories').is_symlink():
            if 'repositories' not in release['nodes']:
                tree(root / 'repositories', payload + '/repositories')
            else:
                # A legacy component can share this namespace; never sweep its runtime homes into source
                repositories = checked_path(state, root / 'repositories')
                directories.add(payload + '/repositories')
                unavailable.append(payload + '/repositories')
                contexts = {'source', 'artifacts', 'prepared.json', 'home', 'build-home', *JOURNALS}
                for child in repositories.iterdir():
                    if child.name in contexts:
                        continue
                    if re.fullmatch(r'[a-z][a-z0-9-]{0,47}', child.name) and child.is_dir() and not child.is_symlink():
                        tree(child, payload + '/repositories/' + child.name)
                    else:
                        omitted.append(payload + '/repositories/' + child.name)
        for name, node in release['nodes'].items():
            if not re.fullmatch(r'[a-z][a-z0-9-]{0,47}', name):
                raise Refused('Unsupported retained component identity', 422)
            folder = root / name
            prefix = payload + '/' + name
            directories.add(prefix)
            tree(folder / 'source', prefix + '/source')
            if node.get('artifact_digest'):
                file(folder / 'prepared.json', prefix + '/prepared.json')
                tree(folder / 'artifacts', prefix + '/artifacts')
                prepared_path = prefix + '/prepared.json'
                if prepared_path in files:
                    if files[prepared_path]['sha256'] != node['artifact_digest']:
                        unavailable.append(prepared_path)
                    else:
                        try:
                            retained = retained or Retained(state)
                            envelope = retained._reviewed_envelope(release, name,
                                lambda path: read_regular(checked_path(state, path), MAX_FILE_BYTES))
                            for tree_name, digest_name in (('source', 'source_digest'), ('artifacts', 'artifact_tree')):
                                if (folder / tree_name).is_dir() and digest_tree(folder / tree_name) != envelope.get(digest_name):
                                    unavailable.append(prefix + '/' + tree_name)
                        except (ValueError, TypeError, AttributeError, Refused):
                            unavailable.append(prepared_path)
            for journal in sorted(JOURNALS):
                file(folder / journal, prefix + '/' + journal, False)
            for home in ('home', 'build-home'):
                if (folder / home).exists() or (folder / home).is_symlink():
                    omitted.append(prefix + '/' + home)
            known = {'source', 'artifacts', 'prepared.json', 'home', 'build-home', *JOURNALS}
            if folder.exists():
                omitted.extend(prefix + '/' + child.name for child in folder.iterdir() if child.name not in known)
        if root.exists():
            omitted.extend(payload + '/' + child.name for child in root.iterdir()
                           if child.name not in {*release['nodes'], 'repositories'})
    for name in ('deployments', 'previews'):
        if (state / name).exists() or (state / name).is_symlink():
            tree(state / name, 'state/' + name)
    for name in ('cleanup', 'workspace-saves'):
        root = state / name
        if root.exists() or root.is_symlink():
            checked_path(state, root)
            if root.is_symlink() or not root.is_dir():
                raise Refused('Retained operation directory is not ordinary', 422)
            directories.add('state/' + name)
            for child in root.iterdir():
                if not re.fullmatch(r'[0-9a-f]{32}', child.name) or child.is_symlink() or not child.is_dir():
                    raise Refused('Retained operation directory has unknown ownership', 422)
                directories.add('state/' + name + '/' + child.name)
                for filename in (('prepared.json',) if name == 'cleanup' else ('before.toml', 'draft.toml')):
                    file(child / filename, 'state/' + name + '/' + child.name + '/' + filename)
    file(state / 'previous-workspace.toml', 'state/previous-workspace.toml', False)
    known = {'state.db', 'state.db-wal', 'state.db-shm', 'workspace.lock', 'releases', 'deployments', 'previews',
             'cleanup', 'workspace-saves', 'previous-workspace.toml'}
    omitted.extend('state/' + path.name for path in state.iterdir() if path.name not in known)
    if (state / 'releases').exists():
        checked_path(state, state / 'releases')
        known_releases = {release['id'] for release in releases}
        omitted.extend('state/releases/' + path.name for path in (state / 'releases').iterdir() if path.name not in known_releases)
    if len(files) + len(directories) > MAX_ENTRIES or total > MAX_BYTES:
        raise Refused('Private backup exceeds its declared size or entry bound', 422)
    return {'files': dict(sorted(files.items())), 'directories': sorted(directories), 'missing': sorted(set(missing)),
            'unavailable': sorted(set(unavailable)), 'omitted': sorted(set(omitted)), 'bytes': total}


# Verify normalized extent and allowlist before reading any imported payload paths
def validate(document: dict) -> dict:
    try:
        value = Manifest.model_validate(document)
        if len((json.dumps(document, indent=2) + '\n').encode()) > MAX_MANIFEST_BYTES:
            raise ValueError('manifest bound')
        normalized = value.model_dump(mode='json')
        if type(document['version']) is not int or normalized != document or value.sha256 != fingerprint(
                {key: item for key, item in document.items() if key != 'sha256'}):
            raise ValueError('extent')
        if any(not Path(path).is_absolute() or '..' in Path(path).parts for path in (value.project_path, value.state_path)):
            raise ValueError('original locations')
        names = [item.path for item in value.files]
        if len(set(names)) != len(names) or len(set(value.directories)) != len(value.directories):
            raise ValueError('duplicates')
        if 'state/state.db' not in names or set(names) & set(value.directories):
            raise ValueError('required payload')
        if (value.workspace_sha256 is None) != ('workspace.toml' in value.missing) or (value.workspace_sha256 is not None) != ('workspace.toml' in names):
            raise ValueError('workspace extent')
        if any(name == missing or name.startswith(missing + '/') for name in [*names, *value.directories] for missing in value.missing):
            raise ValueError('missing payload')
        for name in names:
            if any(str(parent) not in value.directories for parent in PurePosixPath(name).parents if str(parent) != '.'):
                raise ValueError('parent inventory')
        if sum(item.size for item in value.files) > MAX_BYTES or len(names) + len(value.directories) > MAX_ENTRIES:
            raise ValueError('bounds')
        for name in [*names, *value.directories, *value.missing, *value.unavailable]:
            if not allowed(name):
                raise ValueError('allowlist')
        if value.capture != ('partial' if value.missing or value.unavailable or value.config_digest is None else 'complete'):
            raise ValueError('completeness')
        return normalized
    except (ValidationError, ValueError, TypeError, KeyError):
        raise Refused('Unsupported, changed or contradictory backup manifest; no state was imported', 422) from None


# Capture a native SQLite snapshot while cooperative database and workspace writers are excluded
def capture(project: Path, state: Path, target: Path) -> dict:
    project, state, target = location(project), location(state), location(target)
    if target.exists() or target.is_relative_to(state) or target.is_relative_to(project.parent):
        raise Refused('Choose a new private backup directory outside the workspace and state', 422)
    retained = Ledger.open_existing(state).store
    with workspace_lock(state):
        require_saved(project, state)
        writer = Store.open_existing(state / 'state.db', writable=True)
        with writer.transaction() as reservation, retained.connect() as reader:
            quiescent(reservation)
            reader.execute('BEGIN')
            releases = [json.loads(row[0]) for row in reader.execute('SELECT data FROM releases ORDER BY id LIMIT 10001')]
            if len(releases) > 10000:
                raise Refused('This backup format supports at most 10,000 retained releases', 422)
            audit = Store.verify_rows([dict(row) for row in reader.execute('SELECT * FROM audit ORDER BY sequence')])
            source = read_workspace(project)
            try:
                config_digest = inspect_config(source)['digest'] if source is not None else None
            except Refused:
                config_digest = None
            before = inventory(project, state, releases)
            extra_files = 1 + int(source is not None)
            database_size = reader.execute('PRAGMA page_count').fetchone()[0] * reader.execute('PRAGMA page_size').fetchone()[0]
            if (database_size > MAX_FILE_BYTES or before['bytes'] + database_size + len((source or '').encode()) > MAX_BYTES
                    or len(before['files']) + len(before['directories']) + extra_files > MAX_ENTRIES):
                raise Refused('Private backup exceeds its database, payload size or entry bound', 422)
            capture_id = uuid.uuid4().hex
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            target.mkdir(mode=0o700)
            # The snapshot itself is also historical-only, even when opened directly without restoration
            private_json(target / RESTORE_MARKER, {'version': 1, 'backup_id': capture_id,
                'manifest_sha256': None, 'mode': 'backup-inspection-only'}, durable=True)
            for directory in before['directories']:
                (target / directory).mkdir(parents=True, exist_ok=True, mode=0o700)
            if source is not None:
                write_file(target / 'workspace.toml', source.encode())
            database = target / 'state/state.db'
            write_file(database, b'')
            started = time.monotonic()

            # Bound native backup retry; never use an immutable read that ignores live WAL bytes
            def progress(status, remaining, total):
                if time.monotonic() - started > 30:
                    raise Refused('Database backup exceeded its bounded retry window; preserve the incomplete directory', 503)
            destination = sqlite3.connect(database, isolation_level=None)
            try:
                reader.backup(destination, pages=128, progress=progress, sleep=.05)
                if destination.execute('PRAGMA journal_mode=DELETE').fetchone()[0] != 'delete':
                    raise Refused('Backup database could not become self-contained', 503)
            finally:
                destination.close()
            database_bytes = read_regular(database, MAX_FILE_BYTES)
            files = {'state/state.db': {'sha256': hashlib.sha256(database_bytes).hexdigest(), 'size': len(database_bytes), 'executable': False}}
            if source is not None:
                files['workspace.toml'] = {'sha256': source_hash(source), 'size': len(source.encode()), 'executable': False}
            for name, expected in before['files'].items():
                original = checked_path(state, state / PurePosixPath(name).relative_to('state'))
                content = read_regular(original, MAX_FILE_BYTES)
                if hashlib.sha256(content).hexdigest() != expected['sha256']:
                    raise Refused('Owned files changed during backup; no complete manifest was published')
                write_file(target / name, content, expected['executable'])
                files[name] = expected
            if inventory(project, state, releases) != before or read_workspace(project) != source:
                raise Refused('Workspace or owned inventory changed during capture; preserve the incomplete directory')
            omitted = [*before['omitted'], 'database WAL/SHM merged through SQLite backup', 'runtime credential homes',
                       'native lock-file identity', 'external credentials, repositories and services']
            value = Manifest(id=capture_id, toolkit_version=__version__, captured_at=int(time.time()),
                project_path=str(project), state_path=str(state), workspace_sha256=source_hash(source) if source is not None else None, config_digest=config_digest,
                audit=audit, release_count=len(releases), capture='partial' if before['missing'] or before['unavailable'] or config_digest is None else 'complete',
                files=[File(path=name, **item) for name, item in sorted(files.items())], directories=before['directories'],
                missing=before['missing'], unavailable=before['unavailable'], omitted=omitted, sha256='0' * 64)
            document = value.model_dump(mode='json')
            document['sha256'] = fingerprint({key: item for key, item in document.items() if key != 'sha256'})
            document = validate(document)
            # Publish the manifest last; a directory without it is explicitly incomplete
            for folder in sorted([target, *[target / name for name in document['directories']]], key=lambda item: len(item.parts), reverse=True):
                sync_directory(folder)
            private_json(target / RESTORE_MARKER, {'version': 1, 'backup_id': capture_id,
                'manifest_sha256': document['sha256'], 'mode': 'backup-inspection-only'}, durable=True)
            private_json(target / 'manifest.json', document, durable=True)
            sync_directory(target.parent)
    return inspect(target)


# Verify the same byte, mode, directory and audit contract in a bundle or a restored copy
def verify_payload(document, project, state):
    for directory in document['directories']:
        path = checked_path(state, state / PurePosixPath(directory).relative_to('state'))
        if not path.is_dir():
            raise Refused('A recorded backup directory is unavailable', 422)
    for item in document['files']:
        path = project if item['path'] == 'workspace.toml' else checked_path(state, state / PurePosixPath(item['path']).relative_to('state'))
        content = read_regular(path, MAX_FILE_BYTES)
        if len(content) != item['size'] or hashlib.sha256(content).hexdigest() != item['sha256']:
            raise Refused('Backup payload differs from its recorded hash or size', 422)
        if bool(path.stat().st_mode & 0o111) != item['executable']:
            raise Refused('Backup executable permission differs from its manifest', 422)
    if next((item['sha256'] for item in document['files'] if item['path'] == 'workspace.toml'), None) != document['workspace_sha256']:
        raise Refused('Backup workspace binding differs', 422)
    store = Ledger.open_existing(state).store
    if store.verify() != document['audit']:
        raise Refused('Backup audit extent differs from the captured prefix', 422)
    with store.connect() as connection:
        if connection.execute('SELECT count(*) FROM releases').fetchone()[0] != document['release_count']:
            raise Refused('Backup release inventory differs from its snapshot', 422)
        shared = {record['id'] for row in connection.execute('SELECT data FROM releases')
                  if 'repositories' in (record := json.loads(row[0]))['nodes']}
    for name in [*[item['path'] for item in document['files']], *document['directories']]:
        parts = PurePosixPath(name).parts
        if (len(parts) > 4 and parts[:2] == ('state', 'releases') and parts[2] in shared
                and parts[3] == 'repositories' and parts[4] in {'home', 'build-home'}):
            raise Refused('Backup includes a runtime home from an ambiguous legacy namespace', 422)


# Validate all included bytes without opening recorded original source locations
def inspect(bundle: Path) -> dict:
    bundle = location(bundle)
    try:
        document = validate(json.loads(read_regular(bundle / 'manifest.json', MAX_MANIFEST_BYTES).decode('utf-8')))
        marker = json.loads(read_regular(bundle / RESTORE_MARKER, 65536).decode('utf-8'))
        if marker != {'version': 1, 'backup_id': document['id'], 'manifest_sha256': document['sha256'], 'mode': 'backup-inspection-only'}:
            raise Refused('Backup inspection boundary differs from its exact manifest', 422)
        verify_payload(document, bundle / 'workspace.toml', bundle / 'state')
        return {'verified': True, 'manifest': document, 'bundle': str(bundle), 'execution_admitted': False}
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error):
        raise Refused('Backup is incomplete, unreadable or unsupported; no repair or import was attempted', 422) from None


# Recheck a restored extent even when the original backup directory is no longer attached
def inspect_restored(project_directory: Path, state_directory: Path | None = None) -> dict:
    project_directory = location(project_directory)
    state = location(state_directory or project_directory / '.deploy-ops')
    try:
        raw = json.loads(read_regular(state / RESTORE_MARKER, 65536).decode('utf-8'))
        receipt = RestoreReceipt.model_validate(raw)
        document = validate(json.loads(read_regular(state / RESTORE_MANIFEST, MAX_MANIFEST_BYTES).decode('utf-8')))
        project = project_directory / Path(document['project_path']).name
        if (type(raw['version']) is not int or receipt.model_dump(mode='json') != raw
                or receipt.backup_id != document['id'] or receipt.manifest_sha256 != document['sha256']
                or receipt.original_project != document['project_path'] or receipt.original_state != document['state_path']
                or receipt.restored_project != str(project) or receipt.restored_state != str(state)):
            raise Refused('Restore receipt differs from its exact manifest or selected locations; retain this copy for history inspection', 422)
        if json.loads(read_regular(project_directory / RESTORE_MARKER, 65536).decode('utf-8')) != raw:
            raise Refused('Restored workspace boundary differs from its state receipt', 422)
        verify_payload(document, project, state)
        return {'verified': True, 'status': 'restored-for-inspection', 'backup_id': document['id'],
                'manifest_sha256': document['sha256'], 'project': str(project), 'state': str(state), 'capture': document['capture'],
                'original_location': str(project) == document['project_path'] and str(state) == document['state_path'],
                'execution_admitted': False, 'paths_rewritten': False, 'audit': document['audit'], 'file_count': len(document['files']),
                'missing': document['missing'], 'unavailable': document['unavailable']}
    except (OSError, ValueError, TypeError, KeyError, ValidationError, sqlite3.Error):
        raise Refused('Restored receipt or payload is incomplete, changed or unsupported; history may still be inspected read-only', 422) from None


# Restore only new private targets; absolute paths and historical authority rows remain unchanged
def restore(bundle: Path, project_directory: Path, state_directory: Path | None = None, *, expected_digest: str | None = None) -> dict:
    result = inspect(bundle)
    document = result['manifest']
    if expected_digest is not None and expected_digest != document['sha256']:
        raise Refused('Backup extent changed since inspection. Inspect it again before restoring', 409)
    bundle = location(bundle)
    project_directory = location(project_directory)
    state_directory = location(state_directory or project_directory / '.deploy-ops')
    source = Path(document['project_path'])
    if project_directory.is_relative_to(source.parent) and project_directory != source.parent:
        raise Refused('Restore outside the original source tree, or to its empty original workspace root', 422)
    if (project_directory.exists() or state_directory.exists() or project_directory.is_relative_to(bundle)
            or state_directory.is_relative_to(bundle) or state_directory == project_directory
            or project_directory.is_relative_to(state_directory)):
        raise Refused('Restore requires new workspace and state directories outside the backup; existing work is preserved', 422)
    if source.name in {'', '.', '..'}:
        raise Refused('Backup has no supported project filename', 422)
    project_directory.mkdir(parents=True, mode=0o700)
    state_directory.mkdir(parents=True, mode=0o700)
    marker = {'version': 1, 'backup_id': document['id'], 'manifest_sha256': document['sha256'],
              'original_project': document['project_path'], 'original_state': document['state_path'],
              'restored_project': str(project_directory / source.name), 'restored_state': str(state_directory),
              'mode': 'inspection-only', 'authority': 'not-admitted'}
    private_json(state_directory / RESTORE_MARKER, marker, durable=True)
    private_json(project_directory / RESTORE_MARKER, marker, durable=True)
    private_json(state_directory / RESTORE_MANIFEST, document, durable=True)
    for name in document['directories']:
        if name != 'state':
            (state_directory / PurePosixPath(name).relative_to('state')).mkdir(parents=True, exist_ok=True, mode=0o700)
    for item in document['files']:
        content = read_regular(checked_path(bundle, bundle / item['path']), MAX_FILE_BYTES)
        if hashlib.sha256(content).hexdigest() != item['sha256']:
            raise Refused('Backup changed during restoration; restored state remains inspection-only')
        destination = project_directory / source.name if item['path'] == 'workspace.toml' else state_directory / PurePosixPath(item['path']).relative_to('state')
        write_file(destination, content, item['executable'])
    for directory in [state_directory, project_directory, state_directory.parent, project_directory.parent,
                      *[state_directory / PurePosixPath(name).relative_to('state') for name in document['directories'] if name != 'state']]:
        sync_directory(directory)
    return inspect_restored(project_directory, state_directory)
