# tests/test_rollouts.py - Exercise complete local candidate, switch and retained-capacity decisions.


import json
import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
import tomlkit
from fastapi.testclient import TestClient

from deploy_ops.delivery import rollouts
from deploy_ops.delivery.demo import finish
from deploy_ops.delivery.engine import Engine
from deploy_ops.delivery.retained import Retained
from deploy_ops.delivery.scaffold import create
from deploy_ops.delivery.serving import Sessions
from deploy_ops.errors import Refused
from deploy_ops.console import create_app


# Use an independent app so candidate staging does not invent infrastructure dependencies
@pytest.fixture
def staged(tmp_path):
    project = create(tmp_path / 'workspace')
    document = tomlkit.parse(project.read_text())
    del document['components']['foundation']
    del document['components']['web']['needs']
    del document['components']['web']['options']['destination']
    project.write_text(tomlkit.dumps(document))
    engine = Engine(project)
    first = finish(engine, engine.create('development', 'web', False))
    document['components']['web']['options']['activation'] = 'separate'
    project.write_text(tomlkit.dumps(document))
    source = project.parent / 'application/index.html'
    source.write_text(source.read_text().replace('Deployed with DeployOps', 'Measured candidate revision'))
    engine = Engine(project)
    second = finish(engine, engine.create('development', 'web', False))
    assert second['nodes']['web']['outputs']['activation'] == 'candidate'
    assert 'pointer' not in second['nodes']['web']['outputs']
    assert json.loads(Path(first['nodes']['web']['outputs']['pointer']).read_text())['release'] == first['id']
    return engine, first, second


def switch(engine, release):
    release = rollouts.prepare(engine, release['id'], 'web')
    rollouts.approve(engine, release['id'], release['rollout']['request_digest'])
    return rollouts.execute(engine, release['id'])


# Candidate readiness, separate switch authority, measured route and explicit retirement form one journey
def test_local_bluegreen_complete_retained_journey(staged):
    engine, first, second = staged
    sessions = Sessions()
    try:
        fixed = sessions.start(engine, first['id'], 'web')
        target = sessions.start(engine, first['id'], 'web', mode='target')
        before = httpx.get(target['url']).content
        candidate = sessions.start(engine, second['id'], 'web')
        assert b'Measured candidate revision' in httpx.get(candidate['url']).content
        second = rollouts.prepare(engine, second['id'], 'web')
        assert second['rollout']['plan']['readiness']['status'] == 'matching'
        assert httpx.get(target['url']).content == before
        rollouts.approve(engine, second['id'], second['rollout']['request_digest'])
        assert httpx.get(target['url']).content == before
        second = rollouts.execute(engine, second['id'])
        assert second['rollout']['status'] == 'succeeded'
        assert second['rollout']['post_check']['http_status'] == 200
        assert b'Measured candidate revision' in httpx.get(target['url']).content
        assert httpx.get(fixed['url']).content == before
        restarted = Engine(engine.path)
        observed = rollouts.inspect(restarted, second['id'])
        assert observed['route'] == 'candidate' and observed['baseline'] == 'matching'
        assert observed['controller_completion'] == 'succeeded'
        second = rollouts.prepare(restarted, second['id'], 'web', retirement=True)
        assert httpx.get(fixed['url']).status_code == 200
        rollouts.approve(restarted, second['id'], second['rollout']['retirement']['request_digest'], retirement=True)
        assert httpx.get(fixed['url']).status_code == 200
        second = rollouts.execute(restarted, second['id'], retirement=True)
        assert second['rollout']['retirement']['status'] == 'retired'
        assert httpx.get(fixed['url']).status_code == 410
        assert httpx.get(target['url']).status_code == 200
        assert restarted.ledger.get(first['id'])['status'] == 'succeeded'
        assert Path(first['nodes']['web']['outputs']['path']).is_dir()
        assert (engine.state / 'rollouts' / second['rollout']['id'] / 'prepared.json').is_file()
    finally:
        sessions.close()


# Unknown and modified old files remain, independently of successful route activation
def test_retirement_preserves_modified_and_unrecognized_data(staged):
    engine, first, second = staged
    second = switch(engine, second)
    old = Path(first['nodes']['web']['outputs']['path'])
    (old / 'index.html').write_text('owner changed content')
    (old / 'notes.txt').write_text('owner data')
    (old / 'owner-directory').mkdir()
    second = rollouts.prepare(engine, second['id'], 'web', retirement=True)
    plan = second['rollout']['retirement']['plan']
    assert not plan['delete'] and {entry['path'] for entry in plan['retain']} == {'index.html', 'notes.txt'}
    rollouts.approve(engine, second['id'], second['rollout']['retirement']['request_digest'], retirement=True)
    second = rollouts.execute(engine, second['id'], retirement=True)
    assert second['rollout']['retirement']['status'] == 'retired-retained'
    assert (old / 'index.html').read_text() == 'owner changed content'
    assert (old / 'notes.txt').read_text() == 'owner data' and (old / 'owner-directory').is_dir()


# A native pointer replacement followed by process interruption is observed, never automatically replayed
def test_switch_interruption_before_effect_receipt_requires_inspected_recovery(staged, monkeypatch):
    engine, first, second = staged
    second = rollouts.prepare(engine, second['id'], 'web')
    rollouts.approve(engine, second['id'], second['rollout']['request_digest'])
    actual = rollouts.private_json

    def interrupted(path, value, **kwargs):
        actual(path, value, **kwargs)
        if Path(path).name == 'current.json':
            raise SystemExit('synthetic process boundary after pointer replacement')

    with monkeypatch.context() as patch:
        patch.setattr(rollouts, 'private_json', interrupted)
        with pytest.raises(SystemExit):
            rollouts.execute(engine, second['id'])
    current = engine.ledger.get(second['id'])
    assert current['rollout']['status'] == 'running'
    assert rollouts.inspect(Retained(engine.state), second['id'])['route'] == 'candidate'
    with pytest.raises(Refused):
        rollouts.execute(engine, second['id'])
    assert rollouts.recovery_status(engine, second['id'])['available']
    recovered = rollouts.reconcile(engine, second['id'], 'Inspected actual pointer and both retained revisions')
    assert recovered['rollout']['status'] == 'switched-inspected'
    assert recovered['rollout']['post_check']['status'] == 'matching'
    assert engine.gate.status(second['rollout']['request_id'])['status'] == 'failed'
    assert rollouts.inspect(engine, second['id'])['controller_completion'] == 'failed'
    assert Path(first['nodes']['web']['outputs']['path'], 'index.html').is_file()


# A failed measured post-check leaves the actual route uncertain and preserves baseline capacity
def test_failed_postcheck_is_not_automatic_rollback_or_retirement(staged, monkeypatch):
    engine, first, second = staged
    second = rollouts.prepare(engine, second['id'], 'web')
    rollouts.approve(engine, second['id'], second['rollout']['request_digest'])
    actual = rollouts.http_check

    def failed(owner, release, component, mode='revision'):
        if mode == 'target':
            raise Refused('Synthetic post-check unavailable')
        return actual(owner, release, component, mode)

    with monkeypatch.context() as patch:
        patch.setattr(rollouts, 'http_check', failed)
        with pytest.raises(Refused, match='uncertain'):
            rollouts.execute(engine, second['id'])
    assert rollouts.inspect(engine, second['id'])['route'] == 'candidate'
    assert engine.ledger.get(second['id'])['rollout']['status'] == 'uncertain'
    assert Path(first['nodes']['web']['outputs']['path'], 'index.html').exists()
    with pytest.raises(Refused):
        rollouts.prepare(engine, second['id'], 'web', retirement=True)
    sessions = Sessions()
    try:
        with pytest.raises(Refused):
            sessions.start(engine, second['id'], 'web', mode='target')
    finally:
        sessions.close()
    recovered = rollouts.reconcile(engine, second['id'], 'Inspected switched pointer after failed post-check')
    assert recovered['rollout']['status'] == 'switched-inspected'


# Stale pointers and changed candidates refuse before consuming an otherwise valid approval
def test_switch_rechecks_baseline_and_artifact_before_claim(staged):
    engine, first, second = staged
    second = rollouts.prepare(engine, second['id'], 'web')
    rollouts.approve(engine, second['id'], second['rollout']['request_digest'])
    candidate = Path(second['nodes']['web']['outputs']['path']) / 'index.html'
    saved = candidate.read_bytes(); candidate.write_text('changed candidate')
    with pytest.raises(Refused):
        rollouts.execute(engine, second['id'])
    assert engine.gate.status(second['rollout']['request_id'])['status'] == 'approved'
    candidate.write_bytes(saved)
    pointer = Path(first['nodes']['web']['outputs']['pointer'])
    pointer.write_text('{}')
    with pytest.raises(Refused):
        rollouts.execute(engine, second['id'])
    assert engine.gate.status(second['rollout']['request_id'])['status'] == 'approved'


# Strict authenticated transport cannot choose paths or combine an action with unrelated fields
def test_rollout_api_cli_and_invalid_configuration_readback(staged, monkeypatch):
    engine, first, second = staged
    headers = {'Authorization': 'Bearer ' + 'x' * 43}
    path = '/api/releases/' + second['id'] + '/rollout/'
    with TestClient(create_app(engine, 'x' * 43), base_url='http://127.0.0.1:8765') as client:
        assert client.post(path + 'prepare', json={'component': 'web'}).status_code == 401
        for body in ({'component': 'web', 'path': '/private'}, {'component': 'web', 'digest': 'unused'}, {'component': True}):
            assert client.post(path + 'prepare', headers=headers, json=body).status_code == 422
        response = client.post(path + 'prepare', headers=headers, json={'component': 'web'})
        assert response.status_code == 200
        current = response.json()
        detail = client.get('/api/releases/' + second['id'], headers=headers).json()
        assert detail['rollout_requests']['switch']['status'] == 'pending'
        assert detail['rollout_recovery']['available']
        assert client.post(path + 'approve', headers=headers, json={'digest': 'wrong'}).status_code == 409
        assert client.post(path + 'approve', headers=headers, json={'digest': current['rollout']['request_digest']}).status_code == 200
        assert client.post(path + 'switch', headers=headers, json={}).status_code == 200
    engine.path.write_text('invalid = [')
    env = {key: value for key, value in os.environ.items() if not key.startswith('DEPLOY_OPS_')}
    result = subprocess.run([sys.executable, '-m', 'deploy_ops', '--project', str(engine.path), 'rollout', 'inspect', second['id']],
                            env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0 and json.loads(result.stdout)['route'] == 'candidate'


# Unavailable current controller readback cannot hide retained rollout facts or enable execution
def test_rollout_optional_request_failure_keeps_detail_visible(staged, monkeypatch):
    engine, _, second = staged
    second = rollouts.prepare(engine, second['id'], 'web')

    def unavailable(*args):
        raise Refused('Synthetic current readback unavailable')

    monkeypatch.setattr(engine.gate, 'status', unavailable)
    client = TestClient(create_app(engine, 'x' * 43), base_url='http://127.0.0.1:8765')
    response = client.get('/api/releases/' + second['id'], headers={'Authorization': 'Bearer ' + 'x' * 43})
    assert response.status_code == 200
    assert response.json()['release']['rollout'] == second['rollout']
    assert response.json()['rollout_requests']['switch'] == {'status': 'unavailable', 'digest': None}


# A partial deletion never becomes total retirement, and changed remaining data is not replayed
def test_partial_retirement_requires_reconciliation_and_new_inventory(staged, monkeypatch):
    engine, first, second = staged
    second = switch(engine, second)
    second = rollouts.prepare(engine, second['id'], 'web', retirement=True)
    rollouts.approve(engine, second['id'], second['rollout']['retirement']['request_digest'], retirement=True)
    actual = rollouts._unlink

    def stopped(root, name, digest):
        actual(root, name, digest)
        raise Refused('Synthetic failure after one old file deletion')

    with monkeypatch.context() as patch:
        patch.setattr(rollouts, '_unlink', stopped)
        with pytest.raises(Refused, match='uncertain'):
            rollouts.execute(engine, second['id'], retirement=True)
    assert engine.ledger.get(second['id'])['rollout']['retirement']['status'] == 'uncertain'
    with pytest.raises(Refused):
        rollouts.execute(engine, second['id'], retirement=True)
    recovered = rollouts.reconcile(engine, second['id'], 'Inspected missing old index and retained active candidate')
    assert recovered['rollout']['retirement']['status'] == 'reconciled'
    fresh = rollouts.prepare(engine, second['id'], 'web', retirement=True)
    assert fresh['rollout']['retirement']['plan']['missing'] == ['index.html']
    assert fresh['rollout']['retirement']['plan']['delete'] == []


# A real exited native owner leaves its lock and lease; only verified-dead recovery can resolve them
@pytest.mark.parametrize('point', ['after-pointer', 'before-complete', 'after-complete'])
def test_actual_process_exit_requires_dead_owner_and_no_replay(staged, point):
    engine, first, second = staged
    second = rollouts.prepare(engine, second['id'], 'web')
    rollouts.approve(engine, second['id'], second['rollout']['request_digest'])
    script = '''
import os,sys
from pathlib import Path
from deploy_ops.delivery.engine import Engine
from deploy_ops.delivery import rollouts
engine=Engine(Path(sys.argv[1]))
actual=rollouts.private_json
def interrupted(path,value,**kwargs):
    actual(path,value,**kwargs)
    if Path(path).name=='current.json' and sys.argv[3]=='after-pointer':os._exit(83)
rollouts.private_json=interrupted
complete=engine.gate.complete
def completion(*args):
    if sys.argv[3]=='before-complete':os._exit(83)
    result=complete(*args)
    if sys.argv[3]=='after-complete':os._exit(83)
    return result
engine.gate.complete=completion
rollouts.execute(engine,sys.argv[2])
'''
    env = {key: value for key, value in os.environ.items() if not key.startswith('DEPLOY_OPS_')}
    result = subprocess.run([sys.executable, '-c', script, str(engine.path), second['id'], point], env=env, capture_output=True, timeout=120)
    assert result.returncode == 83
    assert rollouts.inspect(Retained(engine.state), second['id'])['route'] == 'candidate'
    status = rollouts.recovery_status(engine, second['id'])
    assert status['available'] and status['worker'] == 'dead'
    with pytest.raises(Refused):
        rollouts.execute(engine, second['id'])
    restored = rollouts.reconcile(engine, second['id'], 'Inspected actual route after the admitted process exited')
    assert restored['rollout']['status'] == 'switched-inspected'
    assert restored['rollout']['post_check']['status'] == 'matching'
    assert engine.gate.status(second['rollout']['request_id'])['status'] == ('succeeded' if point == 'after-complete' else 'failed')
    assert Path(first['nodes']['web']['outputs']['path'], 'index.html').is_file()


# The snapshot is an allowlisted checksum extent; offline reading cannot consult source, authority or URLs
def test_rollout_snapshot_offline_extent_and_legacy_export_scope(staged, tmp_path, monkeypatch):
    engine, _, second = staged
    second = switch(engine, second)
    second['rollout']['post_check']['private-native-extra'] = 'PRIVATE-EXTRA-SENTINEL'
    engine.ledger.save(second, 'test.extra-private-field')
    snapshot = rollouts.inspect(Retained(engine.state), second['id'])
    assert snapshot['format'] == 'deploy-ops.local-rollout-inspection' and snapshot['version'] == 1
    assert 'PRIVATE-EXTRA-SENTINEL' not in json.dumps(snapshot)
    from deploy_ops.delivery import evidence
    with pytest.raises(Refused, match='format 2'):
        evidence.capture(engine.state, second['id'])
    path = tmp_path / 'snapshot.json'; path.write_text(json.dumps(snapshot))

    def forbidden(*args, **kwargs):
        raise AssertionError('Offline reader attempted authority or native access')

    monkeypatch.setattr(Engine, '__init__', forbidden)
    monkeypatch.setattr(rollouts, 'candidate', forbidden)
    assert rollouts.read_snapshot(path) == snapshot
    for changed in (snapshot | {'version': 2}, snapshot | {'route': 'baseline'}, snapshot | {'raw': 'private'}):
        path.write_text(json.dumps(changed))
        with pytest.raises(Refused):
            rollouts.read_snapshot(path)


# Unclaimed withdrawal changes no route or files and needs no fabricated inspection result
def test_withdraw_switch_and_retirement_before_claim(staged):
    engine, first, second = staged
    second = rollouts.prepare(engine, second['id'], 'web')
    rollouts.approve(engine, second['id'], second['rollout']['request_digest'])
    second = rollouts.cancel(engine, second['id'], second['rollout']['request_digest'])
    assert second['rollout']['withdrawn'] and second['rollout']['status'] == 'reconciled'
    assert rollouts.inspect(engine, second['id'])['route'] == 'baseline'
    second = switch(engine, second)
    second = rollouts.prepare(engine, second['id'], 'web', retirement=True)
    second = rollouts.cancel(engine, second['id'], second['rollout']['retirement']['request_digest'], retirement=True)
    assert second['rollout']['retirement']['withdrawn']
    assert Path(first['nodes']['web']['outputs']['path'], 'index.html').is_file()


# Coherent backup includes exact separate plans; a relocated restore remains inspection-only
def test_rollout_plans_backup_and_relocated_inspection(staged, tmp_path):
    from deploy_ops.delivery import backup
    from deploy_ops.store import require_writable
    engine, _, second = staged
    second = switch(engine, second)
    bundle = tmp_path / 'bundle'
    captured = backup.capture(engine.path, engine.state, bundle)
    plan_path = 'state/rollouts/' + second['rollout']['id'] + '/prepared.json'
    assert any(record['path'] == plan_path for record in captured['manifest']['files'])
    restored = backup.restore(bundle, tmp_path / 'restored-workspace', tmp_path / 'restored-state')
    state = tmp_path / 'restored-state'
    assert (state / 'rollouts' / second['rollout']['id'] / 'prepared.json').is_file()
    assert rollouts.inspect(Retained(state), second['id'])['route'] == 'unknown'
    with pytest.raises(Refused):
        require_writable(state / 'state.db')


# A valid frozen site without the selected readiness entry is no-data, not an approvable route
def test_readiness_missing_index_creates_no_switch_request(staged):
    engine, _, _ = staged
    build = engine.path.parent / 'application/build.py'
    build.write_text(build.read_text().replace('destination / "index.html"', 'destination / "readme.txt"'))
    third = finish(engine, engine.create('development', 'web', False))
    with engine.ledger.store.connect() as connection:
        before = connection.execute('SELECT COUNT(*) FROM requests').fetchone()[0]
    with pytest.raises(Refused, match='index.html'):
        rollouts.prepare(engine, third['id'], 'web')
    with engine.ledger.store.connect() as connection:
        assert connection.execute('SELECT COUNT(*) FROM requests').fetchone()[0] == before
    assert 'rollout' not in engine.ledger.get(third['id'])
