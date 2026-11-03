# tests/test_observation.py - Observe actual local drift while preserving deployments and original outcomes.

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from deploy_ops.console import create_app
from deploy_ops.delivery.demo import finish
from deploy_ops.delivery.engine import Engine
from deploy_ops.delivery.files import digest_tree
from deploy_ops.delivery.observation import tree_digest
from deploy_ops.delivery.scaffold import create


@pytest.fixture
def deployed(tmp_path):
    engine = Engine(create(tmp_path / 'workspace'))
    release = finish(engine, engine.create('development'))
    assert release['status'] == 'succeeded'
    return engine, release


def test_complete_local_observation_records_no_repair_or_release_mutation(deployed):
    engine, release = deployed
    before = digest_tree(engine.deployments)
    result = engine.observe(release['id'])
    assert result['status'] == 'matching'
    assert result['repairs'] is False
    assert engine.ledger.get(release['id']) == release
    assert digest_tree(engine.deployments) == before
    assert engine.ledger.events(release['id'])[-1]['detail'] == result
    assert engine.ledger.store.verify()['events'] > 0


@pytest.mark.parametrize('change,status', [('file', 'drifted'), ('missing-file', 'drifted'), ('missing-pointer', 'drifted'),
                                         ('pointer', 'drifted'), ('symlink', 'unknown'), ('root-symlink', 'unknown'),
                                         ('fifo', 'unknown'), ('malformed', 'unknown'), ('missing-baseline', 'unknown'),
                                         ('symlink-baseline', 'unknown')])
def test_observation_distinguishes_drift_from_unreadable_evidence(deployed, tmp_path, change, status):
    engine, release = deployed
    outputs = release['nodes']['web']['outputs']
    site, pointer = Path(outputs['path']), Path(outputs['pointer'])
    if change == 'file':
        (site / 'index.html').write_text('private changed content')
    elif change == 'missing-file':
        (site / 'index.html').unlink()
    elif change == 'missing-pointer':
        pointer.unlink()
    elif change == 'pointer':
        pointer.write_text('{}')
    elif change == 'symlink':
        (site / 'outside').symlink_to(tmp_path)
    elif change == 'root-symlink':
        moved = site.with_name('moved')
        site.rename(moved)
        site.symlink_to(moved, target_is_directory=True)
    elif change == 'fifo':
        os.mkfifo(site / 'unsupported')
    elif change == 'malformed':
        pointer.write_text('{')
    elif change == 'symlink-baseline':
        baseline = engine.state / 'releases' / release['id'] / 'web' / 'prepared.json'
        saved = tmp_path / 'copied-baseline.json'
        saved.write_bytes(baseline.read_bytes())
        baseline.unlink()
        baseline.symlink_to(saved)
    else:
        (engine.state / 'releases' / release['id'] / 'web' / 'prepared.json').unlink()
    result = engine.observe(release['id'])
    assert result['components']['web']['status'] == status
    assert 'private changed content' not in json.dumps(result)
    assert engine.ledger.get(release['id'])['status'] == 'succeeded'


def test_superseded_artifact_and_last_activation_follow_completion_order(deployed):
    engine, original = deployed
    older = engine.create('development', 'web', False)
    newer = finish(engine, engine.create('development', 'web', False))
    latest = finish(engine, older)
    assert latest['status'] == 'succeeded'
    result = engine.observe(newer['id'])
    assert result['components']['web']['status'] == 'superseded'
    assert result['components']['web']['activation']['expected_release'] == older['id']
    # More than the normal history list limit must not hide the actual activation
    for _ in range(202):
        record = engine.create('staging', 'foundation')
        engine.cancel(record['id'])
    assert engine.observe(latest['id'])['status'] == 'matching'
    (Path(newer['nodes']['web']['outputs']['path']) / 'index.html').write_text('changed old artifact')
    damaged = engine.observe(newer['id'])['components']['web']
    assert damaged['status'] == damaged['artifact']['status'] == 'drifted'
    assert damaged['activation']['status'] == 'matching'


def test_unreadable_or_changing_target_is_unknown(deployed, monkeypatch):
    from deploy_ops.delivery import observation
    engine, release = deployed
    original = observation.tree_digest
    def unreadable(_):
        raise PermissionError('private path must not be exposed')
    monkeypatch.setattr(observation, 'tree_digest', unreadable)
    result = engine.observe(release['id'])
    assert result['components']['web']['status'] == 'unknown'
    assert 'private path' not in json.dumps(result)
    def concurrent(path):
        engine.ledger.event(release['id'], 'component.running', {'component': 'web'})
        return original(path)
    monkeypatch.setattr(observation, 'tree_digest', concurrent)
    assert engine.observe(release['id'])['status'] == 'unknown'


def test_directory_marker_scope_and_unsupported_adapters(deployed):
    engine, release = deployed
    marker = Path(release['nodes']['foundation']['outputs']['path']) / 'resource.json'
    marker.write_text('{"name":"different"}')
    assert engine.observe(release['id'])['components']['foundation']['status'] == 'drifted'
    release['nodes']['web']['adapter'] = 'ecs'
    engine.ledger.save(release)
    assert engine.observe(release['id'])['components']['web']['status'] == 'unsupported'


def test_artifact_order_matches_existing_digest_contract(tmp_path):
    (tmp_path / 'a').mkdir()
    (tmp_path / 'a' / 'index').write_text('nested')
    (tmp_path / 'a.txt').write_text('sibling')
    assert tree_digest(tmp_path) == digest_tree(tmp_path)


def test_console_observation_requires_operator_and_survives_restart(deployed):
    engine, release = deployed
    client = TestClient(create_app(engine, 't' * 40), base_url='http://127.0.0.1:8765')
    route = '/api/releases/' + release['id'] + '/observe'
    assert client.post(route, json={}).status_code == 401
    assert client.post(route, json={}, headers={'Authorization': 'Bearer ' + 't' * 40}).json()['status'] == 'matching'
    restarted = Engine(engine.path)
    assert restarted.ledger.events(release['id'])[-1]['name'] == 'release.observed'


def test_active_or_uncertain_execution_prevents_stable_observation(deployed):
    engine, original = deployed
    release = engine.advance(engine.create('development', 'web', False)['id'])
    node = release['nodes']['web']
    engine.approve(release['id'], 'web', node['request_digest'])
    request = engine.gate.status(node['request_id'])
    lease = engine.gate.claim(request, release)
    assert engine.observe(original['id'])['status'] == 'unknown'
    engine.gate.complete(lease, 'aborted')
    assert engine.observe(original['id'])['status'] == 'unknown'


def test_changed_activation_baseline_does_not_validate_an_old_release(deployed):
    engine, original = deployed
    latest = finish(engine, engine.create('development', 'web', False))
    (engine.state / 'releases' / latest['id'] / 'web' / 'prepared.json').write_text('{}')
    assert engine.observe(original['id'])['components']['web']['status'] == 'unknown'
