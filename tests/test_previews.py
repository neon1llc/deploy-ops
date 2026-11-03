# tests/test_previews.py - Verify isolated preview ownership and exact retained-data cleanup.

import json
from pathlib import Path

import pytest

from deploy_ops.config import fingerprint
from deploy_ops.delivery.demo import finish
from deploy_ops.delivery.engine import Engine
from deploy_ops.delivery.previews import (approve_cleanup, execute_cleanup, list_previews,
                                          namespace, prepare_cleanup, reconcile_cleanup)
from deploy_ops.delivery.scaffold import create
from deploy_ops.delivery.spec import load
from deploy_ops.errors import Refused


@pytest.fixture
def preview_engine(tmp_path):
    path = create(tmp_path / 'workspace')
    path.write_text(path.read_text().replace('[environments.development]\nprovider = "local"',
                                           '[environments.development]\nprovider = "local"\npreview = true'))
    return Engine(path)


def deploy(engine, retain=False):
    return finish(engine, engine.create('development', preview_owner='local-operations', retain_preview_data=retain))


def approve(engine, release):
    prepared = prepare_cleanup(engine, release['id'])
    approve_cleanup(engine, release['id'], prepared['cleanup']['request_digest'])
    return prepared


def test_isolated_preview_delivery_and_exact_cleanup(preview_engine):
    engine = preview_engine
    release = deploy(engine)
    assert release['status'] == 'succeeded'
    root = namespace(engine, release)
    assert root.is_relative_to(engine.state / 'previews') and not root.is_relative_to(engine.deployments)
    assert Path(release['nodes']['web']['outputs']['path']).is_relative_to(root)
    assert engine.observe(release['id'])['status'] == 'matching'
    plan = prepare_cleanup(engine, release['id'])
    with pytest.raises(Refused):
        execute_cleanup(engine, release['id'])
    assert root.exists()
    approve_cleanup(engine, release['id'], plan['cleanup']['request_digest'])
    retired = execute_cleanup(engine, release['id'])
    assert retired['status'] == 'succeeded' and retired['cleanup']['status'] == 'retired'
    assert list(root.iterdir()) == [root / '.ownership.json']
    assert (engine.state / 'releases' / release['id'] / 'web' / 'prepared.json').is_file()
    assert list_previews(engine)[0]['cleanup']['status'] == 'retired'
    assert engine.observe(release['id'])['status'] == 'unknown'
    assert 'retirement' in engine.observe(release['id'])['components']['web']['reason']


@pytest.mark.parametrize('retain', [False, True])
def test_retained_modified_and_unknown_files_survive(preview_engine, retain):
    engine = preview_engine
    release = deploy(engine, retain)
    root = namespace(engine, release)
    site = Path(release['nodes']['web']['outputs']['path'])
    (site / 'index.html').write_text('retained changed page')
    (root / 'database.bin').write_bytes(b'important retained bytes')
    (root / 'unknown-empty').mkdir()
    prepared = approve(engine, release)
    assert prepared['cleanup']['plan']['retain']
    result = execute_cleanup(engine, release['id'])
    assert result['cleanup']['status'] == 'retired-retained'
    assert (site / 'index.html').read_text() == 'retained changed page'
    assert (root / 'database.bin').read_bytes() == b'important retained bytes'
    assert (root / 'unknown-empty').is_dir() and (root / '.ownership.json').is_file()


def test_cleanup_rechecks_target_after_authority_and_recovers(preview_engine):
    engine = preview_engine
    release = deploy(engine)
    root = namespace(engine, release)
    approve(engine, release)
    (root / 'later-data').write_text('preserve after approval')
    with pytest.raises(Refused, match='uncertain'):
        execute_cleanup(engine, release['id'])
    assert engine.ledger.get(release['id'])['cleanup']['deleted'] == []
    reconcile_cleanup(engine, release['id'], 'Inspected namespace; later-data must remain')
    fresh = approve(engine, release)
    result = execute_cleanup(engine, release['id'])
    assert fresh['cleanup']['plan']['retain'] and result['cleanup']['status'] == 'retired-retained'
    assert (root / 'later-data').read_text() == 'preserve after approval'


def test_partial_cleanup_needs_new_plan_and_authority(preview_engine, monkeypatch):
    from deploy_ops.delivery import previews
    engine = preview_engine
    release = deploy(engine)
    approve(engine, release)
    original = previews._unlink
    count = [0]
    def interrupted(*args):
        count[0] += 1
        if count[0] == 2:
            raise OSError('simulated interruption')
        original(*args)
    monkeypatch.setattr(previews, '_unlink', interrupted)
    with pytest.raises(Refused, match='uncertain'):
        execute_cleanup(engine, release['id'])
    assert len(engine.ledger.get(release['id'])['cleanup']['deleted']) == 1
    monkeypatch.setattr(previews, '_unlink', original)
    reconcile_cleanup(engine, release['id'], 'Inspected remaining files after interruption')
    approve(engine, release)
    assert execute_cleanup(engine, release['id'])['cleanup']['status'] == 'retired'


def test_ownership_symlinks_and_baseline_tampering_refused(preview_engine):
    engine = preview_engine
    release = deploy(engine)
    root = namespace(engine, release)
    (root / 'link').symlink_to(engine.path.parent)
    with pytest.raises(Refused, match='symbolic link'):
        prepare_cleanup(engine, release['id'])
    (root / 'link').unlink()
    marker = root / '.ownership.json'
    marker.write_text('{}')
    with pytest.raises(Refused, match='ownership'):
        prepare_cleanup(engine, release['id'])


def test_ordinary_target_and_preview_expiry_boundaries(tmp_path, preview_engine, monkeypatch):
    ordinary = Engine(create(tmp_path / 'ordinary'))
    old_data = ordinary.spec.model_dump()
    for environment in old_data['environments'].values():
        environment.pop('preview')
    assert ordinary.spec.digest == fingerprint(old_data)
    release = finish(ordinary, ordinary.create('development'))
    with pytest.raises(Refused, match='owned local preview'):
        prepare_cleanup(ordinary, release['id'])
    target = next(iter(ordinary.gate.controller.settings.targets.values()))
    assert 'delete' not in target.policy.allowed_actions
    preview = deploy(preview_engine)
    from deploy_ops.delivery.previews import active
    expiry = preview['preview']['expires_at']
    monkeypatch.setattr('deploy_ops.delivery.previews.time.time', lambda: expiry)
    assert list_previews(preview_engine)[0]['expired']
    assert namespace(preview_engine, preview).exists()
    with pytest.raises(Refused, match='expired'):
        active(preview_engine, preview)


def test_preview_refuses_shared_inputs_and_delegation(preview_engine):
    engine = preview_engine
    with pytest.raises(Refused, match='owner'):
        engine.create('development')
    with pytest.raises(Refused, match='complete dependency'):
        engine.create('development', 'web', False, preview_owner='ops')
    with pytest.raises(Refused, match='disabled'):
        engine.gate.delegate('development', 1, 1)
    release = deploy(engine)
    with pytest.raises(Refused, match='fresh isolated preview'):
        engine.create('staging', 'web', False, promote=release['id'])
    document = engine.path.read_text().replace('destination = "${outputs.foundation.path}"', 'destination = "shared"')
    engine.path.write_text(document)
    with pytest.raises(Refused, match='destinations'):
        Engine(engine.path).create('development', preview_owner='ops')


def test_preview_contract_rejects_production_and_cloud(preview_engine):
    source = preview_engine.path.read_text()
    for replacement in ['provider = "aws"', 'provider = "local"\nproduction = true']:
        preview_engine.path.write_text(source.replace('provider = "local"', replacement, 1))
        with pytest.raises(ValueError, match='local and nonproduction'):
            load(preview_engine.path)


def test_cleanup_artifact_tampering_and_namespace_swap_refused(preview_engine):
    engine = preview_engine
    release = deploy(engine)
    root = namespace(engine, release)
    prepared = approve(engine, release)
    artifact = engine.state / 'cleanup' / prepared['cleanup']['id'] / 'prepared.json'
    original = artifact.read_bytes()
    artifact.write_text('{}')
    with pytest.raises(Refused, match='artifact changed'):
        execute_cleanup(engine, release['id'])
    artifact.write_bytes(original)
    parked = root.with_name(root.name + '-preserved')
    root.rename(parked)
    root.symlink_to(engine.path.parent, target_is_directory=True)
    with pytest.raises(Refused, match='uncertain'):
        execute_cleanup(engine, release['id'])
    assert engine.path.is_file() and (parked / '.ownership.json').exists()
    root.unlink()
    parked.rename(root)


def test_active_cleanup_cannot_be_reconciled_or_claimed_twice(preview_engine, monkeypatch):
    import concurrent.futures
    import threading
    from deploy_ops.delivery import previews
    engine = preview_engine
    release = deploy(engine)
    approve(engine, release)
    entered, proceed = threading.Event(), threading.Event()
    original = previews._unlink
    def paused(*args):
        entered.set()
        assert proceed.wait(5)
        return original(*args)
    monkeypatch.setattr(previews, '_unlink', paused)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        task = pool.submit(execute_cleanup, engine, release['id'])
        assert entered.wait(5)
        with pytest.raises(Refused, match='worker process still exists'):
            reconcile_cleanup(engine, release['id'], 'Cannot reconcile a live cleanup worker')
        with pytest.raises(Refused, match='Another release'):
            execute_cleanup(engine, release['id'])
        proceed.set()
        assert task.result()['cleanup']['status'] == 'retired'


def test_missing_namespace_is_not_implicit_cleanup_success(preview_engine, monkeypatch):
    engine = preview_engine
    release = deploy(engine)
    approve(engine, release)
    original = engine.gate.complete
    def interrupted(lease, outcome):
        if outcome == 'succeeded':
            raise OSError('controller result lost after filesystem work')
        return original(lease, outcome)
    monkeypatch.setattr(engine.gate, 'complete', interrupted)
    with pytest.raises(Refused, match='uncertain'):
        execute_cleanup(engine, release['id'])
    assert engine.ledger.get(release['id'])['cleanup']['status'] == 'uncertain'
    monkeypatch.setattr(engine.gate, 'complete', original)
    assert namespace(engine, release).is_dir()
    with pytest.raises(Refused, match='absent namespace'):
        reconcile_cleanup(engine, release['id'], 'An existing namespace must not be declared absent', confirm_removed=True)
    reconcile_cleanup(engine, release['id'], 'Inspected tombstone; generated content was removed')
    approve(engine, release)
    result = execute_cleanup(engine, release['id'])
    assert result['cleanup']['status'] == 'retired'
    assert result['status'] == 'succeeded'


def test_live_deployment_recovery_cannot_enable_cleanup(preview_engine, monkeypatch):
    import concurrent.futures
    import threading
    from deploy_ops.delivery.adapters import local
    engine = preview_engine
    release = engine.advance(engine.create('development', preview_owner='ops')['id'])
    engine.approve(release['id'], 'foundation', release['nodes']['foundation']['request_digest'])
    release = engine.advance(release['id'])
    engine.approve(release['id'], 'web', release['nodes']['web']['request_digest'])
    entered, proceed = threading.Event(), threading.Event()
    original = local.apply
    def paused(context, prepared):
        if context.name == 'web':
            entered.set()
            assert proceed.wait(5)
        return original(context, prepared)
    monkeypatch.setattr(local, 'apply', paused)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        worker = pool.submit(engine.advance, release['id'])
        assert entered.wait(5)
        with pytest.raises(Refused, match='worker process still exists'):
            engine.recover(release['id'], 'A live deployment must never be reconciled')
        with pytest.raises(Refused, match='Another release'):
            prepare_cleanup(engine, release['id'])
        proceed.set()
        completed = worker.result()
    assert completed['status'] == 'succeeded' and 'cleanup' not in completed
    assert namespace(engine, completed).exists()


def test_stale_lock_recovery_is_atomic(preview_engine, monkeypatch):
    import concurrent.futures
    import threading
    engine = preview_engine
    release = deploy(engine)
    approve(engine, release)
    scope = 'release:' + release['id']
    engine.ledger.lock(scope, release['id'])
    with engine.ledger.store.transaction() as connection:
        connection.execute('UPDATE release_lock_owners SET owner_pid=2147483647 WHERE scope=?', (scope,))
    entered, proceed = threading.Event(), threading.Event()
    original = engine.gate.status
    def paused(request):
        entered.set()
        assert proceed.wait(5)
        return original(request)
    monkeypatch.setattr(engine.gate, 'status', paused)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(reconcile_cleanup, engine, release['id'], 'Inspected and stopped the former worker')
        assert entered.wait(5)
        with pytest.raises(Refused, match='worker process still exists'):
            reconcile_cleanup(engine, release['id'], 'Second concurrent recovery must be refused')
        proceed.set()
        assert first.result()['cleanup']['status'] == 'reconciled'


def test_unattributed_legacy_lock_blocks_recovery(preview_engine):
    engine = preview_engine
    release = engine.create('development', preview_owner='ops')
    with engine.ledger.store.transaction() as connection:
        connection.execute('INSERT INTO release_locks VALUES (?,?)', ('release:' + release['id'], release['id']))
    with pytest.raises(Refused, match='ownership is unknown'):
        engine.recover(release['id'], 'Legacy owner has not been independently verified')


def test_cli_and_console_share_preview_contract(preview_engine):
    from fastapi.testclient import TestClient
    from deploy_ops.cli import dispatch, parser
    from deploy_ops.console import create_app
    engine = preview_engine
    result = dispatch(parser().parse_args(['--project',str(engine.path),'run','development','--preview-owner','CLI operator']))
    release = finish(engine, result)
    client = TestClient(create_app(engine, 'x'*32), base_url='http://127.0.0.1:8765')
    assert client.get('/api/previews').status_code == 401
    headers = {'Authorization':'Bearer '+'x'*32}
    assert client.get('/api/previews',headers=headers).json()[0]['owner'] == 'CLI operator'
    prepared = client.post('/api/previews/'+release['id']+'/prepare-cleanup',headers=headers,json={})
    assert prepared.status_code == 200
    assert client.get('/api/releases/'+release['id'],headers=headers).json()['cleanup_request']['status'] == 'pending'
    approved = client.post('/api/previews/'+release['id']+'/approve-cleanup',headers=headers,
                           json={'digest':prepared.json()['cleanup']['request_digest']})
    assert approved.status_code == 200
    cleaned = dispatch(parser().parse_args(['--project',str(engine.path),'previews','cleanup',release['id']]))
    assert cleaned['cleanup']['status'] == 'retired'
