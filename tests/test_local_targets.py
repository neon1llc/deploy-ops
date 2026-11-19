# tests/test_local_targets.py - Keep independent local components from sharing one mutation target.


import copy
import json
import os
import subprocess
import sys
import time

import pytest
import tomlkit

from deploy_ops.delivery.demo import finish
from deploy_ops.delivery.engine import Engine
from deploy_ops.delivery.scaffold import create


# Give two logical components independently selected source and one physical destination
def workspace(tmp_path, adapter, destination='shared'):
    path = create(tmp_path / 'workspace')
    document = tomlkit.parse(path.read_text())
    first = 'foundation' if adapter == 'directory' else 'web'
    if adapter == 'static':
        document['components'][first]['needs'] = []
        document['components'][first]['options']['destination'] = 'shared'
    document['components']['other'] = copy.deepcopy(document['components'][first])
    if adapter == 'static':
        document['components']['other']['options']['destination'] = destination
    path.write_text(tomlkit.dumps(document))
    return Engine(path), first


# A second component cannot take over an already owned directory marker or activation pointer
@pytest.mark.parametrize('adapter,destination', [('directory', 'shared'), ('static', './shared'), ('static', 'alias')])
def test_independent_local_target_alias_is_refused(tmp_path, adapter, destination):
    engine, first = workspace(tmp_path, adapter, destination)
    if destination == 'alias':
        (engine.deployments / 'alias').symlink_to(engine.deployments / 'shared', target_is_directory=True)
    original = finish(engine, engine.create('development', first, False))
    repeated = finish(engine, engine.create('development', first, False))
    assert repeated['status'] == 'succeeded'
    attempted = engine.advance(engine.create('development', 'other', False)['id'])
    assert attempted['status'] == 'failed'
    assert 'physical local target' in attempted['reason']
    assert not attempted['nodes']['other'].get('request_id')
    assert not any(event['name'] == 'command.started' for event in engine.ledger.events(attempted['id']))
    assert engine.ledger.get(original['id'])['status'] == 'succeeded'


# Existing dependency composition uses different physical mutations within one destination tree
def test_foundation_and_application_keep_separate_target_ownership(tmp_path):
    engine = Engine(create(tmp_path / 'workspace'))
    release = finish(engine, engine.create('development'))
    assert release['nodes']['foundation']['physical_target']['path'].endswith('/resource.json')
    assert release['nodes']['web']['physical_target']['path'].endswith('/current.json')


# Retargeting an alias after approval cannot borrow authority for a different physical pointer
def test_reviewed_target_refuses_symlink_drift_before_claim(tmp_path):
    engine, _ = workspace(tmp_path, 'static', 'alias')
    (engine.deployments / 'alias').symlink_to(engine.deployments / 'first', target_is_directory=True)
    release = engine.advance(engine.create('development', 'other', False)['id'])
    node = release['nodes']['other']
    engine.approve(release['id'], 'other', node['request_digest'])
    (engine.deployments / 'alias').unlink()
    (engine.deployments / 'alias').symlink_to(engine.deployments / 'second', target_is_directory=True)
    result = engine.advance(release['id'])
    assert result['status'] == 'failed'
    assert 'physical local target changed' in result['reason']
    assert engine.gate.status(node['request_id'])['status'] == 'approved'
    assert not list(engine.deployments.rglob('current.json'))


# Two processes cannot both reserve the same physical target for distinct components
def test_target_reservation_is_shared_between_processes(tmp_path):
    engine, first = workspace(tmp_path, 'directory')
    releases = [engine.create('development', name, False) for name in (first, 'other')]
    script = '''
import json, sys, time
from pathlib import Path
from deploy_ops.delivery.engine import Engine
engine = Engine(Path(sys.argv[1]))
bind = engine._bind_local_target
# Synchronize before the real shared boundary rather than replacing its checks
def barrier(*args):
    Path(sys.argv[3]).touch()
    deadline = time.monotonic() + 10
    while not Path(sys.argv[4]).exists():
        assert time.monotonic() < deadline
        time.sleep(.01)
    return bind(*args)
engine._bind_local_target = barrier
print(json.dumps(engine.advance(sys.argv[2])))
'''
    environment = {key: value for key, value in os.environ.items() if not key.startswith('DEPLOY_OPS_')}
    proceed = tmp_path / 'proceed'
    workers = [subprocess.Popen([sys.executable, '-c', script, str(engine.path), release['id'],
               str(tmp_path / ('ready-' + str(index))), str(proceed)], env=environment,
               text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE) for index, release in enumerate(releases)]
    try:
        deadline = time.monotonic() + 10
        while not all((tmp_path / ('ready-' + str(index))).exists() for index in range(2)):
            assert time.monotonic() < deadline
            time.sleep(.01)
        proceed.touch()
        results = []
        for worker in workers:
            stdout, stderr = worker.communicate(timeout=15)
            assert worker.returncode == 0, stderr
            results.append(json.loads(stdout))
        assert sorted(result['status'] for result in results) == ['failed', 'waiting']
        assert sum(bool(node.get('request_id')) for result in results for node in result['nodes'].values()) == 1
    finally:
        for worker in workers:
            if worker.poll() is None:
                worker.kill()
            worker.wait(timeout=5)


# Independent ledgers cannot place deployment targets inside another authority's state root
def test_nested_delivery_state_cannot_share_local_targets(tmp_path):
    engine, first = workspace(tmp_path, 'directory')
    nested = Engine(engine.path, engine.deployments / 'nested-state')
    result = nested.advance(nested.create('development', first, False)['id'])
    assert result['status'] == 'failed'
    assert 'another delivery state root' in result['reason']
