# tests/test_cache_cleanup.py - Preserve primary results while reporting private runtime-cache removal.


import json
import os

import pytest

from deploy_ops.delivery.adapters import REGISTRY
from deploy_ops.delivery.engine import Engine
from deploy_ops.delivery.files import remove_private_cache
from deploy_ops.delivery.scaffold import create
from deploy_ops.errors import Refused


# Use real local source capture, preparation, controller and ledger ownership
@pytest.fixture
def owner(tmp_path):
    return Engine(create(tmp_path / 'workspace'))


# Fail only unlink of a harmless marker under its exact known cache inode
def blocked_cache(owner, monkeypatch, category):
    original_context, original_unlink = owner._context, os.unlink
    inodes = set()
    markers = []

    # Keep the native context and paths; add no credential or provider fixture
    def context(*args, **kwargs):
        result = original_context(*args, **kwargs)
        folder = owner.state / 'releases' / result.release_id / result.name / category
        marker = folder / 'owned-cache-cleanup-sentinel'
        marker.write_text('Harmless disposable marker')
        marker.chmod(0o600)
        metadata = folder.stat()
        inodes.add((metadata.st_dev, metadata.st_ino))
        markers.append(marker)
        return result

    # Real rmtree still runs; only this selected owned unlink is denied without chmod tricks
    def unlink(path, *args, **kwargs):
        descriptor = kwargs.get('dir_fd')
        if str(path) == 'owned-cache-cleanup-sentinel' and descriptor is not None:
            metadata = os.fstat(descriptor)
            if (metadata.st_dev, metadata.st_ino) in inodes:
                raise PermissionError('synthetic-private-exception-sentinel')
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(owner, '_context', context)
    monkeypatch.setattr(os, 'unlink', unlink)
    return markers


# A private removal failure does not transform waiting or successful deployment into a retryable failure
@pytest.mark.parametrize('category', ['home', 'build-home'])
def test_cleanup_failure_preserves_waiting_then_success(owner, monkeypatch, category):
    markers = blocked_cache(owner, monkeypatch, category)
    waiting = owner.advance(owner.create('development', 'foundation')['id'])
    identifier = waiting['id']
    assert waiting['status'] == 'waiting'
    assert waiting['nodes']['foundation']['private_cache_cleanup']['categories'][category] == 'failed'
    node = waiting['nodes']['foundation']
    owner.approve(identifier, 'foundation', node['request_digest'])
    success = owner.advance(identifier)
    cleanup = success['nodes']['foundation']['private_cache_cleanup']
    assert success['status'] == 'succeeded' and cleanup['state'] == 'failed'
    assert cleanup['recording'] == 'recorded' and 'do not repeat a deployment' in cleanup['warning']
    assert markers[-1].read_text() == 'Harmless disposable marker'
    assert markers[-1].stat().st_mode & 0o777 == 0o600
    other = 'build-home' if category == 'home' else 'home'
    assert cleanup['categories'][other] == 'removed'
    assert Engine(owner.path).ledger.get(identifier)['nodes']['foundation']['private_cache_cleanup'] == cleanup
    public = json.dumps(owner.ledger.events(identifier)) + json.dumps(success)
    assert 'synthetic-private-exception-sentinel' not in public and 'owned-cache-cleanup-sentinel' not in public
    with owner.ledger.store.connect() as connection:
        assert connection.execute('SELECT count(*) FROM release_locks').fetchone()[0] == 0


# Readback keeps its own result and unchanged release/controller state despite separate cleanup failure
def test_readback_cleanup_is_separate_from_primary_observation(owner, monkeypatch):
    release = owner.advance(owner.create('development', 'foundation')['id'])
    markers = blocked_cache(owner, monkeypatch, 'home')
    monkeypatch.setattr(REGISTRY['directory'], 'inspect', lambda context, prepared: {'status': 'matching'}, raising=False)
    observed = owner.provider_status(release['id'], 'foundation')
    assert observed['status'] == 'matching' and observed['private_cache_cleanup']['state'] == 'failed'
    reopened = Engine(owner.path).ledger.get(release['id'])
    assert reopened['status'] == 'waiting'
    assert owner.gate.status(release['nodes']['foundation']['request_id'])['status'] == 'pending'
    assert markers[-1].is_file()
    events = owner.ledger.events(release['id'])
    assert any(row['name'] == 'provider.observed' and row['detail']['result'] == {'status': 'matching'} for row in events)
    assert events[-1]['name'] == 'private_cache.observed'


# Failed recording leaves a durable pending observation rather than an old successful cleanup claim
def test_cleanup_record_failure_preserves_primary_and_pending_evidence(owner, monkeypatch):
    release = owner.advance(owner.create('development', 'foundation')['id'])
    owner.approve(release['id'], 'foundation', release['nodes']['foundation']['request_digest'])
    write = owner.ledger.cache_observation

    # Fail the separate observation transaction only, after real primary-result storage
    def unavailable(identifier, observations, *, record_event=False):
        if record_event:
            raise OSError('synthetic-private-audit-failure')
        return write(identifier, observations, record_event=record_event)

    monkeypatch.setattr(owner.ledger, 'cache_observation', unavailable)
    success = owner.advance(release['id'])
    assert success['status'] == 'succeeded'
    assert success['nodes']['foundation']['private_cache_cleanup']['recording'] == 'unverified'
    reopened = Engine(owner.path).ledger.get(release['id'])
    assert reopened['status'] == 'succeeded'
    assert reopened['nodes']['foundation']['private_cache_cleanup']['recording'] == 'pending'
    assert 'synthetic-private-audit-failure' not in json.dumps(success)
    with owner.ledger.store.connect() as connection:
        assert connection.execute('SELECT count(*) FROM release_locks').fetchone()[0] == 0


# Cache cleanup never follows a replacement link or removes its external target
def test_cache_link_and_missing_location_are_observational(owner, tmp_path):
    release = owner.advance(owner.create('development', 'foundation')['id'])
    folder = owner.state / 'releases' / release['id'] / 'foundation'
    outside = tmp_path / 'outside'
    outside.mkdir()
    marker = outside / 'keep.txt'
    marker.write_text('Outside data remains')
    (folder / 'home').symlink_to(outside, target_is_directory=True)
    assert remove_private_cache(owner.state, release['id'], 'foundation', 'home') == 'unverified'
    assert marker.read_text() == 'Outside data remains' and (folder / 'home').is_symlink()
    missing = tmp_path / 'missing'
    assert remove_private_cache(missing, release['id'], 'foundation', 'home') == 'absent'
    assert not missing.exists()


# An existing active worker lock prevents a new caller from entering cleanup
def test_active_worker_cache_is_not_cleaned_by_another_advance(owner):
    release = owner.advance(owner.create('development', 'foundation')['id'])
    home = owner.state / 'releases' / release['id'] / 'foundation/home'
    home.mkdir(mode=0o700)
    marker = home / 'keep.txt'
    marker.write_text('Active worker cache')
    scope = 'release:' + release['id']
    owner.ledger.lock(scope, release['id'], kind='worker')
    try:
        with pytest.raises(Refused, match='Another release'):
            Engine(owner.path).advance(release['id'])
        assert marker.read_text() == 'Active worker cache'
    finally:
        owner.ledger.unlock(scope, release['id'])


# Ordinary deletion is observed without adding warnings or changing a confirmed local result
def test_successful_cleanup_is_quiet_and_retained(owner):
    release = owner.advance(owner.create('development', 'foundation')['id'])
    cleanup = release['nodes']['foundation']['private_cache_cleanup']
    assert cleanup['state'] == 'removed' and cleanup['recording'] == 'recorded'
    assert cleanup['categories'] == {'home': 'removed', 'build-home': 'removed'}
    assert 'warning' not in cleanup
    assert owner.advance(release['id'])['nodes']['foundation']['private_cache_cleanup'] == cleanup


# A later explicit operation reports observed absence/removal instead of returning stale failure metadata
def test_later_pending_observation_returns_current_cleanup(owner, monkeypatch):
    with monkeypatch.context() as patch:
        markers = blocked_cache(owner, patch, 'home')
        release = owner.advance(owner.create('development', 'foundation')['id'])
        assert release['nodes']['foundation']['private_cache_cleanup']['state'] == 'failed'
    current = owner.advance(release['id'])
    assert current['status'] == 'waiting'
    assert current['nodes']['foundation']['private_cache_cleanup']['state'] == 'removed'
    assert not markers[-1].exists()
    assert Engine(owner.path).ledger.get(release['id'])['nodes']['foundation']['private_cache_cleanup'] == current['nodes']['foundation']['private_cache_cleanup']


# A preparation refusal stays failed even when its independent cache removal also fails
def test_uncertain_primary_result_is_not_hidden_by_cleanup(owner, monkeypatch):
    blocked_cache(owner, monkeypatch, 'home')

    # Refuse only after the real context creates its private caches; no provider is involved
    def prepare(context):
        raise Refused('Controlled local preparation refusal')

    monkeypatch.setattr(REGISTRY['directory'], 'prepare', prepare)
    failed = owner.advance(owner.create('development', 'foundation')['id'])
    assert failed['status'] == 'uncertain' and failed['reason'] == 'Controlled local preparation refusal'
    assert failed['nodes']['foundation']['private_cache_cleanup']['state'] == 'failed'


# Unsupported cache entries or linked component ancestors are never followed or repaired
@pytest.mark.parametrize('replacement', ['file', 'ancestor-link'])
def test_unsupported_cache_path_preserves_unrelated_data(owner, tmp_path, replacement):
    release = owner.advance(owner.create('development', 'foundation')['id'])
    folder = owner.state / 'releases' / release['id'] / 'foundation'
    if replacement == 'file':
        (folder / 'home').write_text('Do not remove this ordinary file')
    else:
        outside = tmp_path / 'outside-component'
        (outside / 'home').mkdir(parents=True, mode=0o700)
        (outside / 'home/keep.txt').write_text('Do not remove external data')
        folder.rename(folder.with_name('retained-foundation'))
        folder.symlink_to(outside, target_is_directory=True)
    assert remove_private_cache(owner.state, release['id'], 'foundation', 'home') == 'unverified'
    if replacement == 'file':
        assert (folder / 'home').read_text() == 'Do not remove this ordinary file'
    else:
        assert (outside / 'home/keep.txt').read_text() == 'Do not remove external data'


# A queued component's unrecorded cache-shaped folder is not destructive authority
@pytest.mark.parametrize('category', ['home', 'build-home'])
def test_unrecorded_cache_is_not_adopted_or_deleted(owner, category):
    release = owner.create('development')
    folder = owner.state / 'releases' / release['id'] / 'web' / category
    folder.mkdir(mode=0o700)
    marker = folder / 'keep.txt'
    marker.write_text('Unknown local operator work')
    before = marker.stat()
    waiting = owner.advance(release['id'])
    assert waiting['status'] == 'waiting' and marker.read_text() == 'Unknown local operator work'
    node = waiting['nodes']['foundation']
    owner.approve(release['id'], 'foundation', node['request_digest'])
    stopped = owner.advance(release['id'])
    assert stopped['status'] == 'uncertain'
    assert 'ownership is unavailable' in stopped['reason']
    assert marker.stat().st_mtime_ns == before.st_mtime_ns
    assert 'private_cache_cleanup' not in stopped['nodes']['web']


# Actual completion before readback lock acquisition must survive pending and final cache writes
def test_readback_reloads_completed_primary_under_lock(owner, monkeypatch):
    waiting = owner.advance(owner.create('development', 'foundation')['id'])
    identifier = waiting['id']
    a, b = Engine(owner.path), Engine(owner.path)
    acquire = a.ledger.lock
    completed, during = [], []

    # B normally approves and completes through the same native lock before A acquires it
    def before_lock(scope, release_id, kind=None):
        b.approve(identifier, 'foundation', waiting['nodes']['foundation']['request_digest'])
        completed.append(b.advance(identifier))
        acquire(scope, release_id, kind)

    # Only the directory adapter's missing read-only provider leaf is supplied
    def inspect(context, prepared):
        during.append(a.ledger.get(identifier))
        return {'status': 'matching'}

    monkeypatch.setattr(a.ledger, 'lock', before_lock)
    monkeypatch.setattr(REGISTRY['directory'], 'inspect', inspect, raising=False)
    assert a.provider_status(identifier, 'foundation')['status'] == 'matching'
    after = owner.ledger.get(identifier)
    for observed in [during[0], after]:
        assert observed['status'] == observed['nodes']['foundation']['status'] == 'succeeded'
        assert observed['nodes']['foundation']['outputs'] == completed[0]['nodes']['foundation']['outputs']
        assert observed['nodes']['foundation']['outputs']
    assert owner.gate.status(waiting['nodes']['foundation']['request_id'])['status'] == 'succeeded'


# A discard must recheck eligibility after ownership, before touching a newly completed operation
def test_discard_refuses_completion_before_readback_lock(owner, monkeypatch):
    waiting = owner.advance(owner.create('development', 'foundation')['id'])
    identifier = waiting['id']
    a, b = Engine(owner.path), Engine(owner.path)
    acquire = a.ledger.lock
    completed = []

    # Deliver the actual approval/apply transition before the reader's lock is acquired
    def before_lock(scope, release_id, kind=None):
        b.approve(identifier, 'foundation', waiting['nodes']['foundation']['request_digest'])
        completed.append(b.advance(identifier))
        acquire(scope, release_id, kind)

    def forbidden(*args):
        pytest.fail('Completed operation reached native discard')

    monkeypatch.setattr(a.ledger, 'lock', before_lock)
    monkeypatch.setattr(REGISTRY['directory'], 'discard', forbidden, raising=False)
    with pytest.raises(Refused, match='Only an unexecuted'):
        a.provider_status(identifier, 'foundation', discard=True)
    assert owner.ledger.get(identifier) == completed[0]


# Both metadata write modes preserve independently stored primary fields even with an older caller
def test_cache_observation_merges_only_its_metadata(owner):
    waiting = owner.advance(owner.create('development', 'foundation')['id'])
    identifier = waiting['id']
    owner.approve(identifier, 'foundation', waiting['nodes']['foundation']['request_digest'])
    completed = owner.advance(identifier)
    for final in [False, True]:
        metadata = {'state': 'unverified', 'recording': 'pending', 'operation': 'metadata-isolation-fixture'}
        owner.ledger.cache_observation(identifier, {'foundation': metadata}, record_event=final)
        current = owner.ledger.get(identifier)
        assert current['status'] == current['nodes']['foundation']['status'] == 'succeeded'
        assert current['nodes']['foundation']['outputs'] == completed['nodes']['foundation']['outputs']
        assert current['nodes']['foundation']['private_cache_cleanup'] == metadata
