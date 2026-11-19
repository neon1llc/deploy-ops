# src/deploy_ops/delivery/evidence.py - Capture portable inspection facts without exporting execution authority.


import hashlib
import html
import json
import os
import re
import time
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from .. import __version__
from ..config import fingerprint
from ..errors import Refused
from .files import MAX_READ_BYTES as MAX_BYTES, read_regular as read_file
from .ledger import Ledger


# Bound imported identities and reject fields outside the public inspection contract
Text = Annotated[str, StringConstraints(min_length=1, max_length=1024, pattern=r'^[^\x00-\x1f\x7f]+$')]
Name = Annotated[str, StringConstraints(min_length=1, max_length=128, pattern=r'^[A-Za-z0-9_.-]+$')]
Digest = Annotated[str, StringConstraints(pattern=r'^[0-9a-f]{64}$')]
Identifier = Annotated[str, StringConstraints(pattern=r'^[0-9a-f]{32}$')]
Outcome = Literal['queued', 'preparing', 'prepared', 'waiting', 'running', 'cancel_requested',
                  'cancelled', 'succeeded', 'failed', 'uncertain', 'skipped', 'blocked']


# Reject extra keys and coercion at every imported object boundary
class Public(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


# Preserve provider scope separately from mutable operation observations
class Scope(Public):
    release_id: Identifier
    component: Name
    environment: Name
    region: Text | None = None
    cluster: Text | None = None
    service: Text | None = None
    subscription_id: Text | None = None
    resource_group: Text | None = None
    name: Text | None = None
    app_id: Text | None = None


# Keep only the documented public readback fields, never arbitrary provider messages
class Observation(Public):
    status: Text | None = None
    revision: Text | None = None
    previous_revision: Text | None = None
    image: Text | None = None
    desired_count: int | None = None
    running_count: int | None = None
    pending_count: int | None = None
    active: bool | None = None
    no_op: bool | None = None


# Retain durable native intent even when submission acknowledgement is missing
class Native(Public):
    journal_sha256: Digest
    provider: Literal['aws', 'azure']
    phase: Literal['register-submitted', 'registered', 'update-submitted', 'update-acknowledged',
                   'converged', 'not-converged', 'no-op']
    scope: Scope
    request_digest: Digest
    request_digest_kind: Literal['sha256-file', 'sha256-canonical-json']
    operation_id: Digest | None = None
    image: Text
    revision: Text | None = None
    previous_revision: Text | None = None
    recorded_at: int | None = None
    observed_at: int | None = None
    observation: Observation | None = None
    candidate_revisions: Annotated[list[Text], Field(max_length=100)] | None = None
    discovery_truncated: bool | None = None


# Distinguish the immutable controller request from the provider request digest
class Controller(Public):
    request_id: Identifier
    request_digest: Digest
    request_digest_kind: Literal['sha256-canonical-manifest'] = 'sha256-canonical-manifest'


# Missing journals mean unknown submission, including when another component completed
class Component(Public):
    name: Name
    adapter: Name
    outcome: Outcome
    controller: Controller | None = None
    artifact_sha256: Digest | None = None
    artifact_status: Literal['present', 'unavailable']
    controller_status: Literal['present', 'unavailable']
    journal: Literal['present', 'unavailable', 'not_applicable']
    native: Native | None = None


# Hash the exact allowlisted extent, not a restorable database or an approval credential
class Summary(Public):
    format: Literal['deploy-ops.inspection'] = 'deploy-ops.inspection'
    version: Literal[1] = 1
    toolkit_version: Name
    captured_at: int
    run_id: Annotated[str, StringConstraints(pattern=r'^[0-9]{1,24}$')] | None = None
    run_attempt: Annotated[str, StringConstraints(pattern=r'^[0-9]{1,12}$')] | None = None
    release_id: Identifier
    project: Name
    environment: Name
    config_digest: Digest
    source_digest: Digest
    outcome: Outcome
    release_record_sha256: Digest
    capture: Literal['complete', 'partial']
    upload: Literal['not_recorded'] = 'not_recorded'
    extent: Literal['allowlisted-inspection-only'] = 'allowlisted-inspection-only'
    consistency: Literal['release-snapshot-and-individual-journal-reads'] = 'release-snapshot-and-individual-journal-reads'
    components: Annotated[list[Component], Field(min_length=1, max_length=200)]
    omitted: Literal['source,credentials,authority,raw-state,raw-provider-responses'] = 'source,credentials,authority,raw-state,raw-provider-responses'
    sha256: Digest


# Reject parent links before looking at a known journal; never traverse arbitrary release paths
def journal(state: Path, release_id: str, name: str, adapter: str):
    relative = Path('releases') / release_id / name
    current = state
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise Refused('Inspection journal parents must not be symbolic links', 422)
    filename = 'ecs-operation.json' if adapter == 'ecs' else 'container-apps-operation.json'
    path = current / filename
    if not path.exists() and not path.is_symlink():
        return None
    content = read_file(path)
    record = json.loads(content)
    request = record if adapter == 'ecs' else record['request']
    scope = request['scope']
    # These identities are facts from the captured release, never inferred from the current workspace
    if scope['release_id'] != release_id or scope['component'] != name:
        raise Refused('Inspection journal belongs to a different release or component', 409)
    if adapter == 'container-apps' and fingerprint(request) != record['request_digest']:
        raise Refused('Container Apps inspection request digest does not match its retained record', 409)
    fields = ('region', 'cluster', 'service') if adapter == 'ecs' else ('subscription_id', 'resource_group', 'name', 'app_id')
    public_scope = {key: scope[key] for key in ('release_id', 'component', 'environment', *fields)}
    observed = record.get('observed')
    observation = None if observed is None else {key: observed[key] for key in Observation.model_fields if key in observed}
    return Native(journal_sha256=hashlib.sha256(content).hexdigest(), provider='aws' if adapter == 'ecs' else 'azure',
                  phase=record['status'], scope=public_scope, request_digest=record['request_digest'],
                  request_digest_kind='sha256-file' if adapter == 'ecs' else 'sha256-canonical-json',
                  operation_id=record.get('operation_id'), image=request['image'], revision=request.get('revision'),
                  previous_revision=request.get('previous_revision'), recorded_at=record.get('recorded_at'),
                  observed_at=record.get('observed_at'), observation=observation,
                  candidate_revisions=record.get('candidate_revisions'), discovery_truncated=record.get('discovery_truncated'))


# Capture retained facts without loading a workspace, current grants, cloud clients or Engine
def capture(state: Path, release_id: str, run_id=None, run_attempt=None) -> dict:
    if not re.fullmatch(r'[0-9a-f]{32}', release_id):
        raise Refused('Choose an exact release ID for inspection', 422)
    if bool(run_id) != bool(run_attempt):
        raise Refused('Inspection run ID and attempt must be supplied together', 422)
    state = state.resolve()
    release = Ledger.open_existing(state).get(release_id)
    components = []
    try:
        for name, node in release['nodes'].items():
            if not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', name) or name in {'.', '..'}:
                raise Refused('Retained component identity is unsafe for inspection', 422)
            native = journal(state, release_id, name, node['adapter']) if node['adapter'] in {'ecs', 'container-apps'} else None
            if native and native.scope.environment != release['environment']:
                raise Refused('Inspection journal belongs to a different environment', 409)
            controller = Controller(request_id=node['request_id'], request_digest=node['request_digest']) if node.get('request_id') else None
            components.append(Component(name=name, adapter=node['adapter'], outcome=node['status'], controller=controller,
                artifact_sha256=node.get('artifact_digest'), artifact_status='present' if node.get('artifact_digest') else 'unavailable',
                controller_status='present' if controller else 'unavailable', journal='present' if native else
                'unavailable' if node['adapter'] in {'ecs', 'container-apps'} else 'not_applicable', native=native))
        value = Summary(toolkit_version=__version__, captured_at=int(time.time()), run_id=run_id, run_attempt=run_attempt,
            release_id=release_id, project=release['project'], environment=release['environment'],
            config_digest=release['config_digest'], source_digest=release['source_digest'], outcome=release['status'],
            release_record_sha256=fingerprint(release), components=components,
            capture='partial' if any('unavailable' in (item.journal, item.artifact_status, item.controller_status)
                                    for item in components) else 'complete', sha256='0' * 64)
        document = value.model_dump(mode='json')
        document['sha256'] = fingerprint({key: value for key, value in document.items() if key != 'sha256'})
        return validate(document)
    except (ValidationError, KeyError, TypeError, ValueError):
        raise Refused('Retained inspection facts are malformed or unsupported; no raw records were exported', 422) from None


# A checksum detects changed extent, not authenticity, current provider health or authority
def validate(document: dict) -> dict:
    try:
        value = Summary.model_validate(document)
        normalized = value.model_dump(mode='json')
        if normalized != document or value.sha256 != fingerprint({key: item for key, item in document.items() if key != 'sha256'}):
            raise ValueError('extent')
        if bool(value.run_id) != bool(value.run_attempt):
            raise ValueError('run')
        names = set()
        for component in value.components:
            if component.name in names or (component.journal == 'present') != (component.native is not None):
                raise ValueError('component')
            names.add(component.name)
            if ((component.controller_status == 'present') != bool(component.controller)
                    or (component.artifact_status == 'present') != bool(component.artifact_sha256)
                    or (component.journal == 'not_applicable') != (component.adapter not in {'ecs', 'container-apps'})):
                raise ValueError('completeness')
            native = component.native
            if native and (native.scope.release_id != value.release_id or native.scope.component != component.name
                           or native.scope.environment != value.environment
                           or (native.provider, component.adapter) not in {('aws', 'ecs'), ('azure', 'container-apps')}):
                raise ValueError('scope')
            if native:
                required = ('region', 'cluster', 'service') if native.provider == 'aws' else ('subscription_id', 'resource_group', 'name', 'app_id')
                forbidden = ('subscription_id', 'resource_group', 'name', 'app_id') if native.provider == 'aws' else ('region', 'cluster', 'service')
                kind = 'sha256-file' if native.provider == 'aws' else 'sha256-canonical-json'
                if (any(not getattr(native.scope, key) for key in required)
                        or any(getattr(native.scope, key) is not None for key in forbidden)
                        or native.request_digest_kind != kind):
                    raise ValueError('provider scope')
        expected = 'partial' if any('unavailable' in (item.journal, item.artifact_status, item.controller_status)
                                   for item in value.components) else 'complete'
        if value.capture != expected:
            raise ValueError('completeness')
        return normalized
    except (ValidationError, ValueError, TypeError):
        raise Refused('Unsupported, incomplete or changed inspection summary; retain the original file', 422) from None


# Import only the inspection format, without opening any paths contained in it
def inspect(path: Path) -> dict:
    try:
        return validate(json.loads(read_file(path)))
    except (ValueError, UnicodeError):
        raise Refused('Inspection summary is not valid UTF-8 JSON', 422) from None


# Preserve existing output files and keep new portable artifacts private by default
def write(path: Path, content: str):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


# Render escaped facts with native disclosures; no scripts, external resources or execution controls
def report(document: dict) -> str:
    value = validate(document)

    # Keep labels and values readable without using provider content as markup
    def facts(items):
        return '<dl>' + ''.join('<dt>' + html.escape(key) + '</dt><dd>' + html.escape(str(item)) + '</dd>'
                               for key, item in items if item is not None) + '</dl>'

    sections = []
    for component in value['components']:
        native = component['native']
        detail = facts([('Adapter', component['adapter']), ('Recorded outcome', component['outcome']),
                        ('Expected controller request', component['controller_status']),
                        ('Expected artifact identity', component['artifact_status']), ('Native operation journal', component['journal'])])
        if native:
            detail += facts([('Native durable phase', native['phase']), ('Recorded at (Unix seconds)', native['recorded_at']),
                             ('Last observation at (Unix seconds)', native['observed_at'])])
            detail += '<h3>Exact recorded scope</h3>' + facts(native['scope'].items())
            detail += '<h3>Last recorded observation</h3>' + (facts(native['observation'].items()) if native['observation'] else '<p>Unavailable. Current cloud outcome is unknown.</p>')
        elif component['journal'] == 'unavailable':
            detail += '<p>No retained operation record. Submission and current cloud outcome are unknown.</p>'
        else:
            detail += '<p>This format has no native operation journal contract for this adapter.</p>'
        sections.append('<section><h2>' + html.escape(component['name']) + '</h2>' + detail + '</section>')
    return ('<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">'
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'">'
        '<title>DeployOps inspection</title><style>body{font:1rem/1.6 system-ui,sans-serif;margin:0;color:#172b3a;background:#f5f7fa}'
        'main{max-width:76rem;margin:auto;padding:2rem}section,header,details{background:white;border:1px solid #ccd4df;border-radius:.6rem;padding:1.4rem;margin-bottom:1rem}'
        'dl{display:grid;grid-template-columns:minmax(10rem,1fr) 3fr;gap:.5rem 1rem}dt{font-weight:600}dd{margin:0;overflow-wrap:anywhere}'
        'pre{white-space:pre-wrap;overflow-wrap:anywhere}summary{cursor:pointer;font-weight:600}summary:focus-visible,pre:focus-visible{outline:3px solid #1859bd;outline-offset:4px}'
        '@media(max-width:40rem){main{padding:.75rem}dl{grid-template-columns:1fr;gap:.25rem}dd{margin-bottom:.8rem}}'
        '@media print{body{background:white}section,header,details{break-inside:avoid}}</style></head><body><main><header>'
        '<h1>Deployment inspection</h1><p>Recorded evidence for offline review. No live cloud check was performed. '
        'This file grants no approval, recovery, replay or cleanup authority.</p>' + facts([
        ('Recorded release outcome', value['outcome']), ('Capture', value['capture']), ('Upload', 'Not recorded in this file'),
        ('Release', value['release_id']), ('Workspace', value['project']), ('Environment', value['environment']),
        ('Captured at (Unix seconds)', value['captured_at']), ('Toolkit', value['toolkit_version']),
        ('Run / attempt', (value['run_id'] + ' / ' + value['run_attempt']) if value['run_id'] else 'Unavailable'),
        ('Summary SHA-256', value['sha256'])]) + '</header>' + ''.join(sections) +
        '<section><h2>Extent and limits</h2><p>Capture completeness covers the allowlisted inspection contract only. '
        'Journal reads are individual observations, not a coherent database backup. A missing journal does not prove '
        'that nothing was submitted. No source, credentials, authority, raw state or raw provider responses are included. '
        'Hashes detect changes to the recorded extent; they do not authenticate its author. '
        'For current health or recovery, use the original operation owner with fresh scope and authority checks.</p></section>'
        '<details><summary>Exact allowlisted summary</summary><pre tabindex="0"><code>' +
        html.escape(json.dumps(value, indent=2)) + '</code></pre></details></main></body></html>')
