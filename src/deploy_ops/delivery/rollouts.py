# delivery/rollouts.py - Stage local static candidates and separately authorize routing and retirement.


import hashlib
import errno
import json
import time
import uuid
from pathlib import Path

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from typing import Annotated, Literal

from .. import __version__
from ..client import private_json
from ..config import fingerprint
from ..errors import Refused
from ..models import Proposal
from .configuration import boundary
from .files import tree_fingerprint
from .gate import LocalGate, target_name
from .observation import activation, checked_path, read_regular, reviewed, tree_digest
from .previews import _rmdir, _scan, _unlink
from .serving import Sessions


class Check(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    kind: Literal['local-static-http'] = 'local-static-http'
    status: Literal['matching', 'unavailable', 'not-observed', 'unknown']
    observed_at: int | None = None
    http_status: Literal[200] | None = None
    path: Literal['/index.html'] | None = None
    body_sha256: Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')] | None = None
    bytes: Annotated[int, Field(ge=0, le=100_000_000)] | None = None
    duration_ms: Annotated[int, Field(ge=0)] | None = None

    # An absent observation can never be normalized into a successful measured check
    @model_validator(mode='after')
    def measured(self):
        if self.status == 'matching' and any(value is None for value in
                (self.observed_at, self.http_status, self.path, self.body_sha256, self.bytes, self.duration_ms)):
            raise ValueError('Matching HTTP checks need exact measured fields')
        return self


class Snapshot(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    format: Literal['deploy-ops.local-rollout-inspection'] = 'deploy-ops.local-rollout-inspection'
    version: Literal[1] = 1
    release_id: Annotated[str, Field(pattern=r'^[0-9a-f]{32}$')]
    status: Literal['not-prepared', 'preparing', 'waiting', 'running', 'post-check', 'uncertain',
                    'succeeded', 'reconciled', 'switched-inspected']
    observed_at: int
    authority: Literal['inspection-only'] = 'inspection-only'
    controller_status_kind: Literal['recorded-local-store'] = 'recorded-local-store'
    controller_completion: Literal['not-prepared', 'pending', 'approved', 'running', 'uncertain',
                                   'succeeded', 'failed', 'rejected', 'expired', 'denied', 'cancelled', 'unavailable'] = 'not-prepared'
    switch_request: Annotated[str, Field(pattern=r'^[0-9a-f]{32}$')] | None = None
    retirement: Literal['not-prepared', 'preparing', 'waiting', 'running', 'uncertain', 'reconciled',
                        'retired', 'retired-retained'] = 'not-prepared'
    route: Literal['candidate', 'baseline', 'changed', 'unknown'] = 'unknown'
    candidate: Literal['matching', 'drifted', 'unknown'] = 'unknown'
    baseline: Literal['matching', 'changed-or-retired', 'missing', 'unknown'] = 'unknown'
    post_check: Check | None = None
    sha256: Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]


# Checksum the exact explicit public extent; this is no signer, authority or restorable state
def seal(value):
    try:
        document = Snapshot.model_validate(value | {'sha256': '0' * 64}).model_dump(mode='json')
        document['sha256'] = fingerprint({key: item for key, item in document.items() if key != 'sha256'})
        return document
    except (ValidationError, TypeError, ValueError):
        raise Refused('Retained rollout inspection fields are malformed or unsupported', 422) from None


# Offline read validates only the captured extent; no recorded path or release is dereferenced
def read_snapshot(path):
    try:
        value = json.loads(read_regular(Path(path), 2_000_000))
        normalized = Snapshot.model_validate(value).model_dump(mode='json')
        if normalized != value or type(value['version']) is not int or value['sha256'] != fingerprint(
                {key: item for key, item in value.items() if key != 'sha256'}):
            raise ValueError('extent')
        return normalized
    except (OSError, ValidationError, ValueError, TypeError, KeyError):
        raise Refused('Unsupported or changed rollout snapshot; no state or native command was opened', 422) from None


# Keep route facts independent of the deployment's successful candidate staging
def candidate(engine, release_id, component):
    release, site, digest = Sessions.selection(engine, release_id, component)
    envelope = reviewed(engine, release, component)
    binding = {'path': str(site.parent.parent / 'current.json'),
               'owner': [release['project'], release['environment'], component]}
    if (release.get('preview') or envelope.get('adapter_contract_version') != 3
            or envelope.get('physical_target') != binding
            or release['nodes'][component].get('physical_target') != binding
            or release['nodes'][component]['outputs'].get('activation') != 'candidate'):
        raise Refused('Choose a separately staged local static candidate', 422)
    return release, site, digest, envelope, binding


# Fresh writes use the original current contract, local controller and canonical physical owner
def eligible(engine, release, component):
    if not isinstance(component, str) or component not in release['nodes']:
        raise Refused('Choose the staged rollout component', 422)
    if (not isinstance(engine.gate, LocalGate) or release['config_digest'] != engine.spec.digest
            or engine.spec.environments[release['environment']].provider != 'local'):
        raise Refused('Restore the candidate workspace and local controller before preparing rollout work', 422)
    from .configuration import require_saved
    require_saved(engine.path, engine.state)
    envelope = reviewed(engine, release, component)
    if envelope.get('adapter') != 'static' or envelope.get('adapter_contract_version') != 3:
        raise Refused('Choose a static candidate prepared with separate activation', 422)
    engine._check_local_target(release, component, envelope['physical_target'])


# A static readiness check proves loopback HTTP and exact reviewed index bytes, not JavaScript behavior
def http_check(engine, release_id, component, mode='revision'):
    _, site, _ = Sessions.selection(engine, release_id, component)
    try:
        expected = hashlib.sha256(read_regular(checked_path(site, site / 'index.html'), 100_000_000)).hexdigest()
    except OSError as error:
        raise Refused('Static readiness needs a readable reviewed /index.html; no switch request was prepared', 422) from error
    sessions = Sessions()
    started = time.monotonic()
    try:
        session = sessions.start(engine, release_id, component, mode=mode)
        # The time limit covers the transfer, not starting the local server
        transfer_started = time.monotonic()
        with httpx.Client(trust_env=False, timeout=5, follow_redirects=False) as client:
            with client.stream('GET', session['url'] + '/index.html') as response:
                digest, size = hashlib.sha256(), 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > 100_000_000 or time.monotonic() - transfer_started > 10:
                        raise Refused('Static HTTP check exceeded its byte or time limit')
                    digest.update(chunk)
                if response.status_code != 200 or digest.hexdigest() != expected:
                    raise Refused('Static HTTP check did not return the reviewed index bytes')
        return {'kind': 'local-static-http', 'status': 'matching', 'observed_at': int(time.time()),
                'http_status': 200, 'path': '/index.html', 'body_sha256': expected,
                'bytes': size, 'duration_ms': round((time.monotonic() - started) * 1000)}
    except (httpx.HTTPError, OSError) as error:
        raise Refused('Static HTTP readiness is unavailable; no route was admitted') from error
    finally:
        sessions.close()


# Authenticate the candidate, baseline capacity and current pointer before an authority claim
def switch_scope(engine, release_id, component):
    release, site, digest, envelope, binding = candidate(engine, release_id, component)
    pointer = checked_path(engine.state, binding['path'])
    current = json.loads(read_regular(pointer, 65536))
    baseline = envelope['prepared']['previous_pointer']
    if current != baseline or activation(engine, pointer) != baseline:
        raise Refused('Active baseline changed; stage and review a fresh candidate')
    if not isinstance(baseline, dict) or not isinstance(baseline.get('release'), str):
        raise Refused('Baseline activation evidence is unavailable')
    old_binding, old_site, old_digest = Sessions.target(engine, baseline['release'], component)
    if old_binding != binding or baseline != {'release': baseline['release'], 'path': str(old_site), 'digest': old_digest}:
        raise Refused('Baseline belongs to another target')
    if tree_digest(site) != digest or tree_digest(old_site) != old_digest:
        raise Refused('Candidate or retained baseline capacity differs from reviewed bytes')
    return {'format': 'deploy-ops.local-route', 'version': 1, 'component': component,
            'physical_target': binding, 'baseline': baseline,
            'candidate': {'release': release_id, 'path': str(site), 'digest': digest},
            'prepared_sha256': release['nodes'][component]['artifact_digest']}


# Preserve unknown or modified old files; deletion is bounded to the original reviewed inventory
def retirement_scope(engine, release_id, component):
    release, _, _, _, binding = candidate(engine, release_id, component)
    rollout = release.get('rollout', {})
    if (rollout.get('component') != component or rollout.get('status') not in {'succeeded', 'switched-inspected'}
            or rollout.get('post_check', {}).get('status') != 'matching'
            or not 0 <= int(time.time()) - rollout['post_check']['observed_at'] <= 300):
        raise Refused('Observe a completed switch and matching post-check before preparing retirement')
    switch = load_plan(engine, rollout)
    if json.loads(read_regular(checked_path(engine.state, binding['path']), 65536)) != switch['candidate']:
        raise Refused('Current route changed; previous capacity cannot be retired')
    if activation(engine, Path(binding['path'])) != switch['candidate']:
        raise Refused('Current successful activation evidence changed')
    if tree_digest(Path(switch['candidate']['path'])) != switch['candidate']['digest']:
        raise Refused('Active candidate capacity changed; do not retire the baseline')
    old = switch['baseline']
    old_release, old_site, old_digest = Sessions.selection(engine, old['release'], component)
    if old_site == Path(switch['candidate']['path']) or old_digest != old['digest']:
        raise Refused('Retirement must name separate retained baseline capacity')
    envelope = reviewed(engine, old_release, component)
    frozen = checked_path(engine.state, engine.state / 'releases' / old['release'] / component / 'artifacts' / 'site')
    expected = _scan(frozen)
    if tree_fingerprint([[name, digest] for name, digest in expected['files'].items()]) != old_digest:
        raise Refused('Original baseline artifact evidence changed')
    actual = _scan(old_site)
    delete = [{'path': name, 'digest': digest} for name, digest in sorted(expected['files'].items())
              if actual['files'].get(name) == digest]
    retain = [{'path': name, 'reason': 'modified' if name in expected['files'] else 'unrecognized'}
              for name in sorted(actual['files']) if name not in {item['path'] for item in delete}]
    return {'format': 'deploy-ops.local-retirement', 'version': 1, 'component': component,
            'physical_target': binding, 'baseline': old, 'candidate': switch['candidate'],
            'delete': delete, 'retain': retain,
            'post_check': rollout['post_check'],
            'missing': sorted(set(expected['files']) - set(actual['files'])),
            'remove_directories': sorted(set(actual['directories']) & set(expected['directories']),
                                       key=lambda name: (len(Path(name).parts), name), reverse=True),
            'unrecognized_directories': sorted(set(actual['directories']) - set(expected['directories']))}


# Retain exact plans under operation IDs; later requests never overwrite older approved bytes
def load_plan(engine, operation):
    path = checked_path(engine.state, engine.state / 'rollouts' / operation['id'] / 'prepared.json')
    content = read_regular(path, 4_000_000)
    if hashlib.sha256(content).hexdigest() != operation['artifact_digest']:
        raise Refused('Reviewed rollout plan changed')
    return json.loads(content)


# One manual proposal owner serves switches and deliberate old-capacity retirement
def prepare(engine, release_id, component, retirement=False):
    scope = 'release:' + release_id
    engine.ledger.lock(scope, release_id)
    try:
        with boundary(engine):
            release = engine.ledger.get(release_id)
            eligible(engine, release, component)
            previous = release.get('rollout', {}).get('retirement', {}) if retirement else release.get('rollout', {})
            if previous.get('status') not in (None, 'reconciled'):
                raise Refused('Inspect the existing rollout operation before preparing another')
            if retirement:
                plan = retirement_scope(engine, release_id, component)
            else:
                plan = switch_scope(engine, release_id, component)
                plan['readiness'] = http_check(engine, release_id, component)
            if len(json.dumps(plan, indent=2, allow_nan=False).encode()) > 4_000_000:
                raise Refused('Rollout plan exceeds the retained evidence limit', 422)
            engine.ledger.start_worker(scope, release_id, 'rollout')
            operation_id = uuid.uuid4().hex
            private_json(engine.state / 'rollouts' / operation_id / 'prepared.json', plan, durable=True)
            # Bind the actual file representation rather than assuming a serializer's whitespace
            digest = hashlib.sha256(read_regular(engine.state / 'rollouts' / operation_id / 'prepared.json', 4_000_000)).hexdigest()
            operation = {'id': operation_id, 'status': 'preparing', 'component': component,
                         'kind': 'retirement' if retirement else 'switch', 'artifact_digest': digest,
                         'source': fingerprint(plan), 'plan': plan}
            if retirement:
                release['rollout']['retirement'] = operation
            else:
                release['rollout'] = operation
            engine.ledger.save(release, 'static.rollout.preparing', {'operation': operation_id, 'kind': operation['kind']})
            proposal = Proposal(target=target_name(release['project'], release['environment'], component),
                engine='delivery', artifact_digest=digest, tool_version=__version__,
                preview={'config_digest': engine.spec.digest, 'preview': {'manual_only': True, 'findings': [],
                    'changes': [{'address': component, 'resource_type': 'local_static_retirement' if retirement else 'local_static_route',
                                 'action': 'delete' if retirement else 'update'}]}})
            source = {**release, 'id': operation_id, 'source_digest': operation['source']}
            request = engine.gate.submit(proposal, source)
            operation.update(status='waiting', request_id=request['id'], request_digest=request['digest'])
            return engine.ledger.save(release, 'static.rollout.prepared', {'operation': operation_id, 'kind': operation['kind']})
    finally:
        engine.ledger.unlock(scope, release_id)


# Approval stays separate from both switch execution and old-capacity deletion
def approve(engine, release_id, digest, retirement=False):
    release = engine.ledger.get(release_id)
    operation = release.get('rollout', {}).get('retirement', {}) if retirement else release.get('rollout', {})
    eligible(engine, release, operation.get('component'))
    if operation.get('status') != 'waiting' or digest != operation.get('request_digest'):
        raise Refused('Review the current rollout request and exact digest')
    return engine.gate.approve(operation['request_id'], digest)


# Withdrawing an unclaimed request requires no invented failure or inspection note
def cancel(engine, release_id, digest, retirement=False):
    scope = 'release:' + release_id
    engine.ledger.lock(scope, release_id)
    try:
        release = engine.ledger.get(release_id)
        operation = release.get('rollout', {}).get('retirement', {}) if retirement else release.get('rollout', {})
        if operation.get('status') != 'waiting' or digest != operation.get('request_digest'):
            raise Refused('Choose the exact current unclaimed rollout request')
        request = engine.gate.status(operation['request_id'])
        if request['status'] not in {'pending', 'approved'}:
            raise Refused('The request is no longer unclaimed; inspect its actual outcome')
        engine.gate.approve(request['id'], digest, 'reject')
        operation.update(status='reconciled', withdrawn=True)
        return engine.ledger.save(release, 'static.rollout.withdrawn', {'operation': operation['id'], 'kind': operation['kind']})
    finally:
        engine.ledger.unlock(scope, release_id)


# Recheck a plan's immutable scope before consuming its fresh target authority
def recheck(engine, release_id, operation, plan):
    if operation['kind'] == 'retirement':
        if retirement_scope(engine, release_id, operation['component']) != plan:
            raise Refused('Previous capacity changed after retirement preparation')
    else:
        if not 0 <= int(time.time()) - plan['readiness']['observed_at'] <= 300:
            raise Refused('Readiness observation is older than five minutes; reconcile and prepare a fresh switch')
        if switch_scope(engine, release_id, operation['component']) != {key: value for key, value in plan.items() if key != 'readiness'}:
            raise Refused('Route scope changed after switch preparation')


# Journal before native mutation, never replay an admitted operation, and retain old capacity on failure
def execute(engine, release_id, retirement=False):
    scope = 'release:' + release_id
    engine.ledger.lock(scope, release_id)
    try:
        with boundary(engine):
            release = engine.ledger.get(release_id)
            operation = release.get('rollout', {}).get('retirement', {}) if retirement else release.get('rollout', {})
            eligible(engine, release, operation.get('component'))
            if operation.get('status') != 'waiting':
                raise Refused('Prepare and approve fresh rollout authority first')
            plan = load_plan(engine, operation)
            recheck(engine, release_id, operation, plan)
            http_check(engine, release_id, operation['component'], 'target' if retirement else 'revision')
            request = engine.gate.status(operation['request_id'])
            source = {**release, 'id': operation['id'], 'source_digest': operation['source']}
            engine.ledger.start_worker(scope, release_id, 'rollout')

            def admitted():
                recheck(engine, release_id, operation, plan)
                return engine.ledger.worker_binding(scope, release_id)

            lease = engine.gate.claim(request, source, local_input_check=admitted)
            operation.update(status='running', phase='before-retirement' if retirement else 'before-switch')
            engine.ledger.save(release, 'static.rollout.running', {'operation': operation['id']})
            deleted = []
            try:
                engine.gate.heartbeat(lease)
                recheck(engine, release_id, operation, plan)
                if retirement:
                    root = checked_path(engine.state, plan['baseline']['path'])
                    for entry in plan['delete']:
                        engine.gate.heartbeat(lease)
                        _unlink(root, entry['path'], entry['digest'])
                        deleted.append(entry['path'])
                    nonempty = []
                    for relative in plan['remove_directories']:
                        engine.gate.heartbeat(lease)
                        try:
                            _rmdir(root, relative)
                        except OSError as error:
                            # Unknown or modified retained children keep their parent directory
                            if error.errno in {errno.ENOTEMPTY, errno.EEXIST} and (
                                    any(Path(entry['path']).is_relative_to(relative) for entry in plan['retain'])
                                    or any(Path(name).is_relative_to(relative) for name in plan['unrecognized_directories'])):
                                nonempty.append(relative)
                            else:
                                raise
                    operation.update(deleted=deleted, retained=plan['retain'], retained_directories=nonempty)
                else:
                    pointer = checked_path(engine.state, plan['physical_target']['path'])
                    private_json(pointer, plan['candidate'], durable=True)
                    operation.update(status='post-check', phase='route-replaced')
                    release['nodes'][operation['component']]['outputs']['pointer'] = str(pointer)
                    engine.ledger.save(release, 'static.route.replaced', {'operation': operation['id'], 'component': operation['component']})
                    operation['post_check'] = http_check(engine, release_id, operation['component'], 'target')
                engine.gate.heartbeat(lease)
                result = engine.gate.complete(lease, 'succeeded')
                if result['status'] != 'succeeded':
                    raise Refused('Rollout controller completion is not confirmed')
                operation.update(status=('retired-retained' if plan['retain'] or plan['unrecognized_directories'] else 'retired') if retirement else 'succeeded',
                                 phase='completed', completed_at=int(time.time()))
                return engine.ledger.save(release, 'static.rollout.completed', {'operation': operation['id'], 'kind': operation['kind']})
            except Exception as error:
                if retirement:
                    operation['deleted'] = deleted
                operation.update(status='uncertain', reason=str(error) if isinstance(error, Refused) else type(error).__name__)
                engine.ledger.save(release, 'static.rollout.uncertain', {'operation': operation['id'], 'phase': operation['phase']})
                try:
                    engine.gate.complete(lease, 'aborted')
                except Exception:
                    pass
                raise Refused('Rollout outcome is uncertain. Inspect the actual route and retained capacity; no mutation was replayed') from None
    finally:
        engine.ledger.unlock(scope, release_id)


# Read actual current state after restart without constructing authority or replaying a switch
def inspect(engine, release_id):
    release = engine.ledger.get(release_id)
    operation = release.get('rollout', {})
    if not operation:
        return seal({'release_id': release_id, 'status': 'not-prepared', 'observed_at': int(time.time())})
    result = {'release_id': release_id, 'status': operation['status'], 'observed_at': int(time.time()),
              'authority': 'inspection-only', 'controller_completion': 'unavailable',
              'switch_request': operation.get('request_id'), 'retirement': operation.get('retirement', {}).get('status', 'not-prepared')}
    try:
        if operation.get('request_id'):
            with engine.ledger.store.connect() as connection:
                result['controller_completion'] = engine.ledger.store.request(connection, operation['request_id'])['status']
    except (Refused, OSError, ValueError, TypeError, KeyError):
        pass
    try:
        plan = load_plan(engine, operation)
        current = json.loads(read_regular(checked_path(engine.state, plan['physical_target']['path']), 65536))
        result['route'] = 'candidate' if current == plan['candidate'] else 'baseline' if current == plan['baseline'] else 'changed'
        result['candidate'] = 'matching' if tree_digest(checked_path(engine.state, plan['candidate']['path'])) == plan['candidate']['digest'] else 'drifted'
        try:
            result['baseline'] = 'matching' if tree_digest(checked_path(engine.state, plan['baseline']['path'])) == plan['baseline']['digest'] else 'changed-or-retired'
        except FileNotFoundError:
            result['baseline'] = 'missing'
        check = operation.get('post_check', {'status': 'not-observed'})
        result['post_check'] = {key: value for key, value in check.items() if key in Check.model_fields}
    except (Refused, OSError, ValueError, TypeError, KeyError):
        result.update(route='unknown', candidate='unknown', baseline='unknown')
    return seal(result)


# Recovery uses the same host/state/liveness and admitted-controller binding as other local workers
def recovery_status(engine, release_id):
    release = engine.ledger.get(release_id)
    rollout = release.get('rollout', {})
    operation = rollout.get('retirement', {}) if rollout.get('retirement', {}).get('status') in {'preparing', 'waiting', 'running', 'uncertain'} else rollout
    if operation.get('status') not in {'preparing', 'waiting', 'running', 'post-check', 'uncertain'}:
        return {'available': False, 'reason': 'No pending or interrupted rollout requires reconciliation'}
    worker = engine.ledger.worker_status('release:' + release_id, release_id, kind='rollout')
    return {'available': worker['state'] in {'dead', 'stopped'}, 'worker': worker['state'], 'reason': worker['reason']}


# Explicit inspection resolves authority as failed, never fabricates a successful prior operation
def reconcile(engine, release_id, note):
    if not isinstance(note, str) or not 10 <= len(note.strip()) <= 500:
        raise Refused('Record an inspection note of 10 to 500 characters', 422)
    available = recovery_status(engine, release_id)
    if not available['available']:
        raise Refused(available['reason'])
    scope = 'release:' + release_id
    engine.ledger.recovery_lock(scope, release_id, kind='rollout')
    try:
        with boundary(engine):
            release = engine.ledger.get(release_id)
            rollout = release['rollout']
            operation = rollout.get('retirement', {}) if rollout.get('retirement', {}).get('status') in {'preparing', 'waiting', 'running', 'uncertain'} else rollout
            eligible(engine, release, operation['component'])
            if not operation.get('request_id'):
                with engine.ledger.store.connect() as connection:
                    for row in connection.execute('SELECT data FROM requests'):
                        request = json.loads(row[0])
                        if (request['manifest']['source']['run_id'] == operation['id']
                                and request['manifest']['artifact_digest'] == operation['artifact_digest']):
                            operation.update(request_id=request['id'], request_digest=request['digest'])
                            break
            admitted = False
            if operation.get('request_id'):
                request = engine.gate.status(operation['request_id'])
                admitted = bool(request.get('execution')) and operation.get('phase') in {'before-switch', 'route-replaced', 'completed'}
                if request['status'] in {'running', 'uncertain'}:
                    engine.gate.recover(request['id'], note)
                elif request['status'] in {'pending', 'approved'}:
                    engine.gate.approve(request['id'], request['digest'], 'reject')
            observation = inspect(engine, release_id)
            operation.update(status='reconciled', note=note, inspection=observation)
            # A written and inspected route can be read; this grants no switch replay or deletion
            if admitted and operation is rollout and observation.get('route') == 'candidate' and observation.get('candidate') == 'matching':
                operation['status'] = 'switched-inspected'
                operation['post_check'] = {'status': 'not-observed'}
                release['nodes'][operation['component']]['outputs']['pointer'] = load_plan(engine, operation)['physical_target']['path']
                engine.ledger.save(release, 'static.route.inspected', {'operation': operation['id'], 'component': operation['component']})
                operation['post_check'] = http_check(engine, release_id, operation['component'], 'target')
            return engine.ledger.save(release, 'static.rollout.reconciled', {'operation': operation['id'], 'note': note})
    finally:
        engine.ledger.unlock(scope, release_id)


# An explicit fresh measured post-check is independent of deployment and prior controller success
def check(engine, release_id):
    scope = 'release:' + release_id
    engine.ledger.lock(scope, release_id)
    try:
        with boundary(engine):
            release = engine.ledger.get(release_id)
            operation = release.get('rollout', {})
            if operation.get('status') not in {'succeeded', 'switched-inspected'}:
                raise Refused('Inspect and reconcile interrupted rollout work before checking its active route')
            if operation.get('retirement', {}).get('status') in {'preparing', 'waiting', 'running', 'uncertain'}:
                raise Refused('Finish or reconcile retirement before changing its reviewed post-check evidence')
            try:
                operation['post_check'] = http_check(engine, release_id, operation['component'], 'target')
            except Refused:
                operation['post_check'] = {'status': 'unavailable', 'observed_at': int(time.time())}
            return engine.ledger.save(release, 'static.route.checked', {'operation': operation['id'], 'status': operation['post_check']['status']})
    finally:
        engine.ledger.unlock(scope, release_id)
