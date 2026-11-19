# tests/test_target_serving.py - Verify stable local routing across exact approved activations.


import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from deploy_ops.console import create_app
from deploy_ops.delivery.demo import finish
from deploy_ops.delivery.engine import Engine
from deploy_ops.delivery.retained import Retained
from deploy_ops.delivery.scaffold import create
from deploy_ops.delivery.serving import Sessions
from deploy_ops.errors import Refused


@pytest.fixture
def deployed(tmp_path):
    engine = Engine(create(tmp_path / 'workspace'))
    release = finish(engine, engine.create('development'))
    return engine, release


# A waiting or approved-but-held update leaves routing unchanged; execution changes only target mode
def test_target_follows_activation_and_retains_fixed_revision(deployed):
    engine, first = deployed
    sessions = Sessions()
    try:
        fixed = sessions.start(engine, first['id'], 'web')
        target = sessions.start(engine, first['id'], 'web', mode='target')
        before = httpx.get(target['url']).content
        source = engine.path.parent / 'application' / 'index.html'
        source.write_text(source.read_text().replace('Deployed with DeployOps', 'Approved second revision'))
        second = engine.advance(engine.create('development', 'web', False)['id'])
        assert second['status'] == 'waiting'
        assert httpx.get(target['url']).content == before
        engine.approve(second['id'], 'web', second['nodes']['web']['request_digest'])
        assert httpx.get(target['url']).content == before
        second = engine.advance(second['id'])
        assert second['status'] == 'succeeded'
        response = httpx.get(target['url'])
        assert response.status_code == 200 and b'Approved second revision' in response.content
        assert httpx.get(fixed['url']).content == before
        assert httpx.head(target['url']).content == b''
        current = sessions.start(engine, second['id'], 'web', mode='target')
        assert current['id'] == target['id'] and current['active_release'] == second['id']
        assert current['active_digest'] == second['nodes']['web']['outputs']['digest']
        assert len(sessions.list()) == 2
        assert httpx.get(target['url'], headers={'Host': 'outside.example'}).status_code == 403
        assert httpx.get(target['url'] + '/prepared.json').status_code == 404
        artifact = Path(second['nodes']['web']['outputs']['path']) / 'index.html'
        artifact.write_text('unreviewed content')
        refused = httpx.get(target['url'])
        assert refused.status_code == 410 and 'unreviewed content' not in refused.text
    finally:
        sessions.close()
    with pytest.raises(httpx.ConnectError):
        httpx.get(target['url'], timeout=1)


# A pointer is not approval: external rollback, missing facts and cross-environment injection fail closed
def test_target_refuses_unverified_pointer_without_falling_back(deployed):
    engine, first = deployed
    sessions = Sessions()
    try:
        target = sessions.start(engine, first['id'], 'web', mode='target')
        pointer = Path(first['nodes']['web']['outputs']['pointer'])
        old = pointer.read_bytes()
        second = finish(engine, engine.create('development', 'web', False))
        current = pointer.read_bytes()
        pointer.write_bytes(old)
        assert httpx.get(target['url']).status_code == 410
        assert sessions.list()[0]['status'] == 'evidence-unavailable'
        for value in ({}, {'release': '0' * 32, 'path': '/private', 'digest': '0' * 64}, []):
            pointer.write_text(json.dumps(value))
            assert httpx.get(target['url']).status_code == 410
        pointer.unlink()
        assert httpx.get(target['url']).status_code == 410
        pointer.write_bytes(current)
        assert httpx.get(target['url']).status_code == 200
        staging = finish(engine, engine.create('staging'))
        pointer.write_bytes(Path(staging['nodes']['web']['outputs']['pointer']).read_bytes())
        assert httpx.get(target['url']).status_code == 410
        pointer.write_bytes(current)
        record = engine.state / 'releases' / second['id'] / 'web' / 'prepared.json'
        record.write_text('{}')
        assert httpx.get(target['url']).status_code == 410
    finally:
        sessions.close()


# Whole-inventory authentication happens before a newly active revision can serve any requested file
def test_target_checks_new_revision_inventory_and_completion(deployed):
    engine, first = deployed
    sessions = Sessions()
    try:
        target = sessions.start(engine, first['id'], 'web', mode='target')
        second = finish(engine, engine.create('development', 'web', False))
        site = Path(second['nodes']['web']['outputs']['path'])
        extra = site / 'extra.txt'; extra.write_text('unreviewed')
        assert httpx.get(target['url']).status_code == 410
        extra.unlink()
        saved = second['nodes']['web']['status']
        second['nodes']['web']['status'] = 'uncertain'
        engine.ledger.save(second, 'test.interrupted')
        assert httpx.get(target['url']).status_code == 410
        second['nodes']['web']['status'] = saved
        engine.ledger.save(second, 'test.recovered')
        assert httpx.get(target['url']).status_code == 200
    finally:
        sessions.close()


# Retained serving needs no current TOML, execution registration or new writable state
def test_target_retained_read_and_no_restart_adoption(deployed):
    engine, first = deployed
    engine.path.write_text('invalid = [')
    owner = Retained(engine.state)
    sessions = Sessions()
    try:
        target = sessions.start(owner, first['id'], 'web', mode='target')
        assert httpx.get(target['url']).status_code == 200
        assert Sessions().list() == []
        with pytest.raises(Refused):
            sessions.start(owner, first['id'], 'web', mode='automatic')
        record = engine.state / 'releases' / first['id'] / 'web' / 'prepared.json'
        data = json.loads(record.read_text())
        data.pop('physical_target')
        record.write_text(json.dumps(data))
        assert httpx.get(target['url']).status_code == 410
    finally:
        sessions.close()


# Console transport keeps authentication and strict inputs while sharing the same routing owner
def test_target_console_contract(deployed):
    engine, first = deployed
    token = 'fixture-' + 'x' * 40
    headers = {'Authorization': 'Bearer ' + token}
    with TestClient(create_app(engine, token), base_url='http://127.0.0.1:8765') as client:
        body = {'release': first['id'], 'component': 'web', 'mode': 'target'}
        assert client.post('/api/serving/start', json=body).status_code == 401
        for invalid in ('automatic', {}, True, None):
            assert client.post('/api/serving/start', headers=headers, json=body | {'mode': invalid}).status_code == 422
        target = client.post('/api/serving/start', headers=headers, json=body).json()
        assert target['mode'] == 'target' and httpx.get(target['url']).status_code == 200
        second = finish(engine, engine.create('development', 'web', False))
        detail = client.get('/api/releases/' + second['id'], headers=headers).json()
        assert detail['serving_sessions'][0]['id'] == target['id']
        assert detail['serving_sessions'][0]['active_release'] == second['id']
    with pytest.raises(httpx.ConnectError):
        httpx.get(target['url'], timeout=1)
