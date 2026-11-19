# tests/test_recovery.py - Preserve uncertain ownership while exposing supported local reconciliation.


import os
import shutil
import subprocess
import sys
import time

import pytest
from fastapi.testclient import TestClient

from deploy_ops.cli import dispatch, parser
from deploy_ops.console import create_app
from deploy_ops.delivery.engine import Engine
from deploy_ops.delivery.scaffold import create
from deploy_ops.errors import Refused


# Exercise an actual admitted worker that pauses before build or provider effects
@pytest.fixture
def interrupted(tmp_path):
    engine = Engine(create(tmp_path / 'workspace'))
    release = engine.create('development', 'foundation', False)
    ready = tmp_path / 'worker-ready'
    script = '''
import sys,time
from pathlib import Path
from deploy_ops.delivery.engine import Engine
engine = Engine(Path(sys.argv[1]))
# Pause the real preparation after durable admission, without spawning a command child
def hold(*args):
    Path(sys.argv[3]).touch()
    time.sleep(60)
engine._build = hold
engine.advance(sys.argv[2])
'''
    environment = {key: value for key, value in os.environ.items() if not key.startswith('DEPLOY_OPS_')}
    worker = subprocess.Popen([sys.executable, '-c', script, str(engine.path), release['id'], str(ready)],
                              env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 10
        while not ready.exists():
            assert worker.poll() is None, worker.communicate(timeout=1)
            assert time.monotonic() < deadline
            time.sleep(.01)
        yield engine, release['id'], worker
    finally:
        if worker.poll() is None:
            worker.kill()
        worker.communicate(timeout=5)


# Live, stopped and dead are separate observations; reads and refused recovery preserve the worker lock
@pytest.mark.parametrize('status', ['running', 'cancel_requested', 'uncertain'])
def test_recovery_requires_observed_worker_exit(interrupted, status):
    engine, identifier, worker = interrupted
    release = engine.ledger.get(identifier)
    release['status'] = status
    engine.ledger.save(release)
    client = TestClient(create_app(engine, 't' * 40), base_url='http://127.0.0.1:8765',
                        headers={'Authorization': 'Bearer ' + 't' * 40})
    before = engine.ledger.events(identifier)
    capability = client.get('/api/releases/' + identifier).json()['recovery']
    assert capability['worker'] == 'live' and not capability['available']
    assert not dispatch(parser().parse_args(['--project', str(engine.path), 'recovery', identifier]))['available']
    refused = client.post('/api/releases/' + identifier + '/recover', json={'note': 'Inspected target while worker is still live'})
    assert refused.status_code == 409
    assert engine.ledger.events(identifier) == before

    # A different process cannot release the admitted worker's lock
    engine.ledger.unlock('release:' + identifier, identifier)
    assert engine.recovery_status(release)['worker'] == 'live'
    worker.kill(); worker.communicate(timeout=5)
    assert client.get('/api/releases/' + identifier).json()['recovery']['worker'] == 'dead'
    result = client.post('/api/releases/' + identifier + '/recover', json={'note': 'Inspected target after confirmed local worker exit'})
    assert result.status_code == 200
    recovered = result.json()
    assert recovered['status'] == 'failed'
    assert recovered['reconciliation']['mutation_replayed'] is False
    assert not list(engine.deployments.rglob('resource.json'))
    assert not engine.recovery_status(recovered)['available']
    events = engine.ledger.events(identifier)
    assert engine.recover(identifier, 'Repeated inspection after confirmed worker exit') == recovered
    assert engine.ledger.events(identifier) == events


# Missing legacy ownership is unknown, even when there is no retained lock
@pytest.mark.parametrize('locked', [False, True])
@pytest.mark.parametrize('status', ['running', 'cancel_requested', 'uncertain'])
def test_legacy_recovery_never_guesses_worker_death(tmp_path, locked, status):
    engine = Engine(create(tmp_path / 'workspace'))
    release = engine.create('development', 'foundation', False)
    release['status'] = status
    engine.ledger.save(release)
    if locked:
        with engine.ledger.store.transaction() as connection:
            connection.execute('INSERT INTO release_locks VALUES (?,?)', ('release:' + release['id'], release['id']))
    before = engine.ledger.store.audit()
    if not locked:
        engine.ledger.lock('release:' + release['id'], release['id'])
        engine.ledger.unlock('release:' + release['id'], release['id'])
    capability = engine.recovery_status(release)
    assert capability['worker'] == 'unknown' and not capability['available']
    with pytest.raises(Refused, match='ownership is unverified'):
        engine.recover(release['id'], 'Inspected legacy target with unknown original worker')
    with pytest.raises(Refused):
        engine.advance(release['id'])
    assert engine.ledger.store.audit() == before
    with engine.ledger.store.connect() as connection:
        assert bool(connection.execute('SELECT 1 FROM release_locks').fetchone()) == locked


# A relocated evidence copy or another host cannot inherit local stopped-worker authority
@pytest.mark.parametrize('change', ['location', 'host'])
def test_recovery_refuses_foreign_worker_evidence(tmp_path, monkeypatch, change):
    engine = Engine(create(tmp_path / 'workspace'))
    release = engine.advance(engine.create('development', 'foundation', False)['id'])
    release['status'] = 'uncertain'
    engine.ledger.save(release)
    assert engine.recovery_status(release)['worker'] == 'stopped'
    if change == 'location':
        state = tmp_path / 'copied-state'
        shutil.copytree(engine.state, state)
        engine = Engine(engine.path, state)
    else:
        identity = engine.ledger._worker_identity()
        monkeypatch.setattr(engine.ledger, '_worker_identity', lambda: identity | {'host_id': 'different-host'})
    observed = engine.recovery_status(release)
    assert observed['worker'] == 'unknown' and not observed['available']
    with pytest.raises(Refused, match='another host, state location or protocol'):
        engine.recover(release['id'], 'Inspected copied evidence without original worker ownership')


# Missing local credentials do not turn another operation mode into a local recovery owner
def test_recovery_routes_other_modes_to_original_owner(tmp_path):
    engine = Engine(create(tmp_path / 'workspace'))
    release = engine.advance(engine.create('development', 'foundation', False)['id'])
    release['status'] = 'uncertain'
    engine.ledger.save(release)
    engine.gate = object()
    assert not engine.recovery_status(release)['available']
    with pytest.raises(Refused, match='original remote or isolated operation owner'):
        engine.recover(release['id'], 'Inspected a target controlled by another operation owner')


# A recovery process can die between controller resolution and its local journal save without replaying a mutation
def test_recovery_rejoins_terminal_controller_after_its_own_process_death(tmp_path, monkeypatch):
    from deploy_ops.delivery.adapters import local
    engine = Engine(create(tmp_path / 'workspace'))
    apply = local.apply

    # Lose the acknowledgement after the real local mutation so recovery has an actual uncertain target
    def lost(context, prepared):
        apply(context, prepared)
        raise Refused('Injected lost local mutation acknowledgement')

    monkeypatch.setattr(local, 'apply', lost)
    release = engine.advance(engine.create('development', 'foundation', False)['id'])
    node = release['nodes']['foundation']
    engine.approve(release['id'], 'foundation', node['request_digest'])
    release = engine.advance(release['id'])
    assert release['status'] == 'uncertain'
    targets = {str(path): path.read_bytes() for path in engine.deployments.rglob('resource.json')}
    assert len(targets) == 1
    script = '''
import os,sys
from pathlib import Path
from deploy_ops.delivery.engine import Engine
engine = Engine(Path(sys.argv[1]))
save = engine.ledger.save
# Stop after terminal controller resolution but before the local reconciliation save
def cut(release, event=None, detail=None):
    if event == 'release.reconciled':
        os._exit(74)
    return save(release, event, detail)
engine.ledger.save = cut
engine.recover(sys.argv[2], 'Inspected original target after stopped local supervisor')
'''
    result = subprocess.run([sys.executable, '-c', script, str(engine.path), release['id']],
                            capture_output=True, timeout=15)
    assert result.returncode == 74, result.stderr
    assert engine.gate.status(node['request_id'])['status'] == 'failed'
    assert engine.recovery_status(engine.ledger.get(release['id']))['worker'] == 'dead'
    audit = engine.ledger.store.audit()
    recovered = engine.recover(release['id'], 'Inspected retained target and terminal controller after interrupted recovery')
    assert recovered['status'] == 'failed'
    assert recovered['reconciliation']['controller_outcomes'] == {node['request_id']: 'failed'}
    assert engine.ledger.store.audit()[:len(audit)] == audit
    assert targets == {str(path): path.read_bytes() for path in engine.deployments.rglob('resource.json')}
    assert len([event for event in engine.ledger.events(release['id']) if event['name'] == 'release.reconciled']) == 1


# Supervisor death does not authorize recovery while an actual owned command group can still work
@pytest.mark.skipif(os.name != 'posix', reason='POSIX command group observation')
def test_orphaned_command_blocks_recovery(tmp_path):
    import signal
    import tomlkit
    path = create(tmp_path / 'workspace')
    document = tomlkit.parse(path.read_text())
    document['components']['foundation']['build'] = {'command': [sys.executable, '-c',
        "from pathlib import Path; import time; Path('command-ready').touch(); time.sleep(60)"]}
    path.write_text(tomlkit.dumps(document))
    engine = Engine(path)
    release = engine.create('development', 'foundation', False)
    script = 'import sys; from pathlib import Path; from deploy_ops.delivery.engine import Engine; Engine(Path(sys.argv[1])).advance(sys.argv[2])'
    worker = subprocess.Popen([sys.executable, '-c', script, str(path), release['id']], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    group = None
    try:
        deadline = time.monotonic() + 10
        ready = engine.state / 'releases' / release['id'] / 'foundation' / 'source' / 'command-ready'
        while not ready.exists():
            assert worker.poll() is None, worker.communicate(timeout=1)
            assert time.monotonic() < deadline
            time.sleep(.01)
        launches = [event for event in engine.ledger.events(release['id']) if event['name'] == 'command.launched']
        group = launches[-1]['detail']['process_group']
        worker.kill(); worker.communicate(timeout=5)
        capability = engine.recovery_status(engine.ledger.get(release['id']))
        assert capability['worker'] == 'command_live' and not capability['available']
        before = engine.ledger.store.audit()
        with pytest.raises(Refused, match='command group still exists'):
            engine.recover(release['id'], 'Inspected supervisor death with its original command still running')
        assert engine.ledger.store.audit() == before
    finally:
        if group:
            try:
                os.killpg(group, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if worker.poll() is None:
            worker.kill()
        worker.communicate(timeout=5)


# A late local attempt cannot certify an execution admitted outside the tracked worker protocol
def test_legacy_claim_cannot_borrow_a_later_workers_stop_fact(tmp_path):
    from deploy_ops.delivery.operator import Operator
    engine = Engine(create(tmp_path / 'workspace'))
    release = engine.advance(engine.create('development', 'foundation', False)['id'])
    node = release['nodes']['foundation']
    engine.approve(release['id'], 'foundation', node['request_digest'])
    engine.gate.claim(engine.gate.status(node['request_id']), release)
    later = engine.advance(release['id'])
    assert later['status'] == 'uncertain'
    capability = engine.recovery_status(later)
    assert capability['worker'] == 'unknown' and not capability['available']
    before = engine.ledger.store.audit()
    with pytest.raises(Refused, match='no matching local worker admission'):
        engine.recover(release['id'], 'Inspected an older execution whose original worker is unknown')
    with pytest.raises(Refused, match='no matching local worker admission'):
        Operator(engine).call('resolve', node['request_id'],
            {'outcome': 'failed', 'note': 'A later read or local attempt is not proof of original worker death'})
    assert engine.gate.status(node['request_id'])['status'] == 'running'
    assert engine.ledger.store.audit() == before
