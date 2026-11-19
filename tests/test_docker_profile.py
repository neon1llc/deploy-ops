# tests/test_docker_profile.py - Exercise explicit Docker admission without running a daemon or containers.


import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import tempfile

from fastapi.testclient import TestClient
import pytest

from deploy_ops.cli import dispatch, parser
from deploy_ops.console import create_app
from deploy_ops.delivery import docker
from deploy_ops.delivery.engine import Engine, require_execution_contract
from deploy_ops.delivery.process import Process
from deploy_ops.errors import Refused


# Tools and socket here are identity fixtures, never native executable/daemon proof
@pytest.fixture
def selected(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    shutil.copytree(root / 'examples/local-compose', tmp_path / 'workspace')
    project = tmp_path / 'workspace/deploy-ops.toml'
    project.write_text(project.read_text().replace('needs = ["foundation"]', 'needs = []'))
    engine = Engine(project)
    operator = tmp_path / 'operator'; operator.mkdir()
    tools = {}
    for name in ('docker', 'compose'):
        path = operator / name; path.write_text('Identity fixture only'); path.chmod(0o700); tools[name] = str(path)
    monkeypatch.setattr(docker, 'native_tool', lambda path: str(Path(path).resolve()))
    with tempfile.TemporaryDirectory(prefix='do-socket-') as directory:
        endpoint = Path(directory).resolve() / 'socket'
        handle = socket.socket(socket.AF_UNIX); handle.bind(str(endpoint))
        profile = {'schema_version': 1, 'workspace_digest': engine.spec.digest,
            'docker_executable': tools['docker'], 'compose_executable': tools['compose'],
            'endpoint': 'unix://' + str(endpoint), 'endpoint_owner_uid': os.getuid(),
            'daemon_id': 'fixture-daemon', 'projects': {'development/web': 'deployops-local-example'}}
        path = operator / 'profile.json'; path.write_text(json.dumps(profile)); path.chmod(0o600)
        monkeypatch.setenv('DEPLOY_OPS_DOCKER_PROFILE_FILE', str(path))
        try:
            yield engine, path, profile, endpoint
        finally:
            handle.close()


# Metadata readiness is identical across CLI/API and cannot execute a native command
def test_readiness_has_no_daemon_authority(selected, monkeypatch):
    engine, path, profile, endpoint = selected

    def forbidden(*args, **kwargs):
        raise AssertionError('Native command during metadata readiness')

    monkeypatch.setattr(Process, 'run', forbidden)
    result = docker.readiness(engine.path)
    assert result['state'] == 'metadata-ready' and not result['daemon_verified']
    assert profile['daemon_id'] not in json.dumps(result) and str(path) not in json.dumps(result)
    cli = dispatch(parser().parse_args(['--project', str(engine.path), 'doctor']))
    client = TestClient(create_app(engine, 't' * 40), base_url='http://127.0.0.1:8765')
    assert client.get('/api/docker/readiness').status_code == 401
    response = client.get('/api/docker/readiness', headers={'Authorization': 'Bearer ' + 't' * 40})
    assert response.json() == cli['docker_readiness'] == result


# Missing configuration/socket stops creation before source capture or dependency effects
@pytest.mark.parametrize('missing', ['profile', 'socket'])
def test_missing_prerequisite_stops_before_capture(selected, monkeypatch, missing):
    engine, path, profile, endpoint = selected
    if missing == 'profile':
        monkeypatch.delenv('DEPLOY_OPS_DOCKER_PROFILE_FILE')
    else:
        endpoint.unlink()
    with pytest.raises(Refused):
        engine.create('development', 'web')
    assert not (engine.state / 'captures').exists()
    assert not (engine.state / 'releases').exists()
    assert not list(engine.deployments.rglob('resource.json'))


# A binding notices profile bytes, tool bytes and replaced endpoint identity
@pytest.mark.parametrize('changed', ['profile', 'tool', 'socket', 'namespace'])
def test_frozen_selection_refuses_drift(selected, changed):
    engine, path, profile, endpoint = selected
    values = engine.spec.component('web', 'development').options
    frozen = docker.binding(engine.path, engine.spec, 'development', 'web', values)
    replacement = None
    try:
        if changed == 'profile':
            path.write_text(path.read_text() + '\n')
        elif changed == 'tool':
            Path(profile['compose_executable']).write_text('Different identity fixture')
        elif changed == 'namespace':
            values = {**values, 'project': 'another-project'}
        else:
            endpoint.unlink()
            replacement = socket.socket(socket.AF_UNIX); replacement.bind(str(endpoint))
        with pytest.raises(Refused):
            docker.revalidate(engine.path, engine.spec, 'development', 'web', values, frozen)
    finally:
        if replacement:
            replacement.close()


# Closed fields and source separation cannot become implicit Docker configuration
@pytest.mark.parametrize('change', ['extra', 'source-profile', 'source-tool', 'workspace', 'endpoint-file', 'world-readable'])
def test_invalid_profile_refuses(selected, monkeypatch, change):
    engine, path, profile, endpoint = selected
    if change == 'extra':
        profile['DOCKER_CONFIG'] = '/personal/config'
    elif change == 'source-profile':
        source = engine.path.parent / 'profile.json'; source.write_text(path.read_text()); source.chmod(0o600)
        monkeypatch.setenv('DEPLOY_OPS_DOCKER_PROFILE_FILE', str(source))
    elif change == 'source-tool':
        profile['compose_executable'] = str(engine.path)
    elif change == 'workspace':
        profile['workspace_digest'] = 'a' * 64
    elif change == 'endpoint-file':
        endpoint.unlink(); endpoint.write_text('Not a daemon socket')
    else:
        path.chmod(0o644)
    path.write_text(json.dumps(profile))
    with pytest.raises(Refused):
        docker.binding(engine.path, engine.spec, 'development', 'web')


# Read legacy bytes without granting them a new Docker execution binding
def test_old_reader_and_new_execution_floor(selected):
    engine, path, profile, endpoint = selected
    content = json.dumps({'schema_version': 2, 'adapter_contract_version': 1, 'adapter': 'compose',
        'prepared': {'project': 'old-project'}}).encode()
    release = {'id': 'e' * 32, 'nodes': {'web': {'adapter': 'compose', 'artifact_digest': hashlib.sha256(content).hexdigest()}}}
    folder = engine.state / 'releases' / release['id'] / 'web'; folder.mkdir(parents=True)
    (folder / 'prepared.json').write_bytes(content)
    envelope = engine._reviewed_envelope(release, 'web')
    with pytest.raises(Refused, match='Legacy Compose'):
        require_execution_contract(envelope)
    assert (folder / 'prepared.json').read_bytes() == content


# Existing Engine/claim admission refuses changed selection before consuming approval
def test_wrong_daemon_refuses_before_compose_render(selected, monkeypatch):
    engine, path, profile, endpoint = selected
    calls = []

    def command(self, argv, *args, **kwargs):
        calls.append(argv)
        return json.dumps('different-fixture-daemon')

    monkeypatch.setattr(Process, 'run', command)
    release = engine.advance(engine.create('development', 'web', False)['id'])
    assert release['status'] == 'uncertain'
    assert len(calls) == 1 and calls[0][1] == 'info'
    assert not release['nodes']['web'].get('request_id')
    assert not (engine.state / 'releases' / release['id'] / 'web/artifacts/compose.json').exists()


# Existing Engine/claim admission refuses changed selection before consuming approval
def test_changed_profile_does_not_consume_approval(selected, monkeypatch):
    engine, path, profile, endpoint = selected
    calls = []

    def command(self, argv, *args, **kwargs):
        calls.append(argv)
        if argv[1] == 'info':
            return json.dumps('fixture-daemon')
        assert argv[-3:] == ['config', '--format', 'json']
        return json.dumps({'services': {'web': {'image': 'registry.example/web@sha256:' + 'a' * 64}}})

    monkeypatch.setattr(Process, 'run', command)
    release = engine.advance(engine.create('development', 'web', False)['id'])
    assert release['status'] == 'waiting', release.get('reason')
    envelope = engine._reviewed_envelope(release, 'web')
    assert envelope['adapter_contract_version'] == 4
    assert envelope['prepared']['docker_binding']['daemon_id'] == 'fixture-daemon'
    node = release['nodes']['web']; engine.approve(release['id'], 'web', node['request_digest'])
    path.write_text(path.read_text() + '\n')
    refused = engine.advance(release['id'])
    assert refused['status'] == 'failed' and len(calls) == 2
    assert engine.gate.status(node['request_id'])['status'] == 'approved'
    assert engine._reviewed_envelope(release, 'web') == envelope
