# delivery/captures.py - Inspect source capture attempts without registering deployments or granting continuation.


import hashlib
import json
import os
import re
import sqlite3
import stat
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from .. import __version__
from ..client import private_json
from ..config import canonical
from ..errors import Refused
from ..store import Store
from .files import checked_path, read_regular
from .spec import Repository

# Keep record identity aligned with runtime component and release identifiers
Name = Annotated[str, StringConstraints(pattern=r'^[a-z][a-z0-9-]{0,47}$')]
Identifier = Annotated[str, StringConstraints(pattern=r'^[0-9a-f]{32}$')]
Digest = Annotated[str, StringConstraints(pattern=r'^[0-9a-f]{64}$')]
Phase = Literal['started', 'capturing-repository', 'copying-component', 'validating-workspace',
                'registration-requested', 'refused', 'interrupted']
FILENAME = 'capture.json'


# Keep successful repository identity aligned with the existing snapshot result
class SourceIdentity(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    revision: str
    digest: Digest


# Reject unsupported metadata rather than adopting a directory from its name
class Record(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    format: Literal['deploy-ops.source-capture'] = 'deploy-ops.source-capture'
    version: Literal[1] = 1
    toolkit_version: str
    id: Identifier
    project: str
    environment: Name
    config_digest: Digest
    components: list[Name]
    repositories: dict[Name, Repository]
    origin_release: Identifier | None = None
    reused_component: Name | None = None
    owner_pid: int = Field(ge=1)
    created_at: int
    updated_at: int
    phase: Phase = 'started'
    repository: Name | None = None
    component: Name | None = None
    captured_repositories: dict[Name, SourceIdentity] = Field(default_factory=dict)
    copied_components: list[Name] = Field(default_factory=list)
    source_digest: Digest | None = None
    stopped_during: str = ''
    reason: str = ''


# Reserve a new private root exclusively; an existing directory is never a capture to adopt
def begin(state, identifier, project, environment, config_digest, components, repositories,
          origin_release=None, reused_component=None):
    now = int(time.time())
    record = Record(toolkit_version=__version__, id=identifier, project=project, environment=environment,
                    config_digest=config_digest, components=components, repositories=repositories,
                    origin_release=origin_release, reused_component=reused_component,
                    owner_pid=os.getpid(), created_at=now, updated_at=now).model_dump()
    root = checked_path(state, state / 'releases' / identifier)
    root.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        root.mkdir(mode=0o700)
    except FileExistsError:
        raise Refused('Source capture ID already exists; its directory was not adopted', 409) from None
    private_json(root / FILENAME, record, durable=True)
    return root, record


# Publish complete phase metadata atomically before the associated capture operation
def update(root, record, phase, **changes):
    value = Record.model_validate({**record, **changes, 'phase': phase,
                                   'updated_at': max(record['updated_at'], int(time.time()))}).model_dump()
    private_json(root / FILENAME, value, durable=True)
    record.clear()
    record.update(value)


# Read only this record's bounded private bytes; no source tree, cleanup or authority is opened
def load_record(state: Path, identifier: str):
    if not isinstance(identifier, str) or not re.fullmatch(r'[0-9a-f]{32}', identifier):
        raise Refused('Choose a recorded source capture ID', 422)
    path = checked_path(state, state / 'releases' / identifier / FILENAME)
    try:
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
            raise ValueError('private regular record')
        content = read_regular(path)
        raw = json.loads(content)
        record = Record.model_validate(raw).model_dump()
        if (type(raw.get('version')) is not int or record['id'] != identifier or canonical(raw) != canonical(record)
                or len(set(record['components'])) != len(record['components'])
                or len(set(record['copied_components'])) != len(record['copied_components'])
                or not set(record['copied_components']) <= set(record['components'])
                or not set(record['captured_repositories']) <= set(record['repositories'])
                or (record['origin_release'] is None) != (record['reused_component'] is None)
                or (record['reused_component'] is not None and record['reused_component'] not in record['components'])
                or (record['source_digest'] is not None and record['copied_components'] != record['components'])
                or (record['phase'] == 'registration-requested' and record['source_digest'] is None)):
            raise ValueError('record identity')
    except (OSError, ValueError, TypeError, ValidationError):
        raise Refused('Source capture record is unavailable or unsupported; no directory was adopted', 422) from None
    return record, hashlib.sha256(content).hexdigest()


# Inspect existing database state without creating tables, repairing files or requiring current configuration
@contextmanager
def registration_reader(state):
    with ExitStack() as stack:
        try:
            store = Store.open_existing(state / 'state.db')
            connection = stack.enter_context(store.connect())
            connection.execute('BEGIN')
            connection.execute('SELECT id FROM releases LIMIT 1').fetchone()
        except (Refused, sqlite3.Error, OSError):
            connection = None
        yield connection


# A database observation links the same ID only when source and selection bindings agree
def registration(connection, record):
    if connection is None:
        return {'state': 'unavailable', 'reason': 'Retained registration could not be read'}
    try:
        row = connection.execute('SELECT data FROM releases WHERE id=?', (record['id'],)).fetchone()
        if row is None:
            return {'state': 'not-registered'}
        release = json.loads(row[0])
        if (release['project'], release['environment'], release['config_digest'], release['order'], release['source_digest'], release.get('origin_release')) != (
                record['project'], record['environment'], record['config_digest'], record['components'], record['source_digest'], record['origin_release']):
            return {'state': 'unavailable', 'reason': 'Registration differs from the recorded capture bindings'}
        return {'state': 'registered', 'release_id': record['id'], 'release_status': release['status']}
    except (sqlite3.Error, ValueError, KeyError, TypeError):
        return {'state': 'unavailable', 'reason': 'Retained registration could not be read'}


# Marker phase and registration are separate observations, never a claim that the creator is stopped
def _observed(state, identifier, connection):
    record, digest = load_record(state, identifier)
    return {'id': identifier, 'available': True, 'state_directory': str(state), 'record': record, 'record_sha256': digest,
            'registration': registration(connection, record),
            'scope': 'Recorded source capture only; creator PID is not liveness proof. No resume, cleanup or execution authority.'}


# Exact-ID inspection remains useful without current configuration or readable database registration
def inspect(state, identifier):
    state = Path(state).resolve()
    with registration_reader(state) as connection:
        return {**_observed(state, identifier, connection), 'observed_at': int(time.time())}


# Scan one bounded directory level and only named marker locations; unknown directories remain untouched
def paths(state):
    root = checked_path(state, state / 'releases')
    if not root.exists():
        return []
    result = []
    with os.scandir(root) as entries:
        for count, entry in enumerate(entries, 1):
            if count > 10000:
                raise Refused('Capture history exceeds the scan bound; inspect an exact capture ID', 422)
            if not re.fullmatch(r'[0-9a-f]{32}', entry.name) or not entry.is_dir(follow_symlinks=False):
                continue
            path = Path(entry.path) / FILENAME
            try:
                metadata = path.lstat()
            except FileNotFoundError:
                continue
            result.append((metadata.st_mtime_ns, entry.name))
    return sorted(result, reverse=True)


# Page observed records without pretending that incomplete folders are registered releases
def list_records(state, offset=0, limit=20):
    state = Path(state).resolve()
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
        raise Refused('Choose a nonnegative offset and a capture page size of 1 to 100', 422)
    candidates = paths(state)
    records = []
    with registration_reader(state) as connection:
        for _, identifier in candidates[offset:offset + limit]:
            try:
                records.append(_observed(state, identifier, connection))
            except Refused as error:
                records.append({'id': identifier, 'available': False, 'reason': str(error)})
    return {'records': records, 'offset': offset, 'limit': limit, 'total': len(candidates),
            'has_more': offset + limit < len(candidates), 'observed_at': int(time.time()),
            'order': 'record-file modification time, newest first',
            'scope': 'Individual marker and retained-registration observations; unrecorded directories are not listed or adopted.'}
