# tests/test_signatures.py - Check selected-file/key scope without treating fixture processes as cryptographic proof.


import hashlib
import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from deploy_ops.cli import main
from deploy_ops.console import create_app
from deploy_ops.delivery import signatures
from deploy_ops.delivery.engine import Engine
from deploy_ops.delivery.scaffold import create
from deploy_ops.errors import Refused


@pytest.fixture
def frozen(tmp_path):
    engine = Engine(create(tmp_path / 'workspace'))
    release = engine.advance(engine.create('development')['id'])
    engine.approve(release['id'], 'foundation', release['nodes']['foundation']['request_digest'])
    release = engine.advance(release['id'])
    assert release['nodes']['web']['status'] == 'waiting'
    key = tmp_path / 'public.pem'; key.write_text('-----BEGIN PUBLIC KEY-----\nZmFrZQ==\n-----END PUBLIC KEY-----\n')
    bundle = tmp_path / 'bundle.json'; bundle.write_text(json.dumps({'mediaType': 'application/vnd.dev.sigstore.bundle.v0.3+json'}))
    return engine, release, key, bundle


# This fake exercises dispatch, scope and failure contracts only; native Cosign is qualified separately
def process_fixture(monkeypatch, outcome='verified', callback=None):
    calls = []

    class FixtureProcess:
        def __init__(self, home, emit):
            assert Path(home).name == 'tool-home'

        def run(self, argv, cwd, timeout, capture=False):
            calls.append(argv)
            assert capture and 10 <= timeout <= 300
            if argv[1:3] == ['version', '--json']:
                if outcome == 'missing':
                    raise FileNotFoundError('Fixture missing native executable')
                return json.dumps({'gitVersion': 'v2.4.3' if outcome == 'unsupported' else 'v3.1.4'})
            assert argv[:3] == ['cosign', '--timeout', str(timeout) + 's']
            assert '--insecure-ignore-tlog=true' in argv and '--offline' not in argv
            assert Path(argv[-1]) == Path(cwd) / 'subject.blob'
            assert '--key' in argv and '--bundle' in argv
            if callback:
                callback(argv)
            if outcome == 'not_verified':
                raise Refused('Fixture native verification did not succeed')
            return ''

    monkeypatch.setattr(signatures, 'Process', FixtureProcess)
    return calls


@pytest.mark.parametrize('outcome,status,exit_code', [('verified', 'verified', 0), ('not_verified', 'not_verified', 1),
                                                   ('missing', 'unavailable', 3), ('unsupported', 'unavailable', 3)])
def test_signature_scope_held_approval_and_cli_exit(frozen, tmp_path, monkeypatch, capsys, outcome, status, exit_code):
    engine, release, key, bundle = frozen
    process_fixture(monkeypatch, outcome)
    audit = engine.ledger.store.audit()
    engine.path.write_text('invalid current TOML = [')
    report = signatures.verify(engine.state, release['id'], 'web', 'index.html', key, bundle,
                               tmp_path / 'report', signatures.Profile(), engine.path.parent)['report']
    assert report['status'] == status and report['scope'] == 'selected-frozen-file-content-only'
    assert report['policy'] == 'not_evaluated' and report['authority'] == 'observation-only'
    assert report['signer_trust'] == 'operator-selected-key-only'
    assert report['transparency'] == report['timestamp'] == report['revocation'] == 'not_checked'
    assert report['selected_file_sha256'] == hashlib.sha256((tmp_path / 'report/subject.blob').read_bytes()).hexdigest()
    assert report['selected_key_sha256'] == hashlib.sha256(key.read_bytes()).hexdigest()
    assert engine.ledger.get(release['id']) == release and engine.ledger.store.audit() == audit
    args = ['--project', str(engine.path), 'artifact', 'verify', release['id'], '--component', 'web', '--file', 'index.html',
            '--key', str(key), '--bundle', str(bundle), '--out', str(tmp_path / 'cli-report')]
    assert main(args) == exit_code
    assert json.loads(capsys.readouterr().out)['report']['status'] == status


# Key selection cannot quietly change its pin or accept private material or a remote key reference
def test_signature_key_and_path_preconditions_preserve_output(frozen, tmp_path, monkeypatch):
    engine, release, key, bundle = frozen
    calls = process_fixture(monkeypatch)
    output = tmp_path / 'existing'; output.mkdir(); (output / 'owner.txt').write_text('keep')
    with pytest.raises(Refused):
        signatures.verify(engine.state, release['id'], 'web', 'index.html', key, bundle, output, signatures.Profile())
    assert (output / 'owner.txt').read_text() == 'keep' and not calls
    with pytest.raises(Refused, match='pin'):
        signatures.public_key(key, '0' * 64)
    key.write_text('-----BEGIN PRIVATE KEY-----\nnot public\n-----END PRIVATE KEY-----\n')
    with pytest.raises(Refused):
        signatures.key_info(key)
    for path in ('../prepared.json', '/private/file', './index.html', 'missing.html'):
        with pytest.raises(Refused):
            signatures.selection(engine.state, release['id'], 'web', path)
    assert not calls


# Native verification uses a bounded private copy and cannot bless a changed original artifact
def test_signature_changed_subject_never_finishes_report(frozen, tmp_path, monkeypatch):
    engine, release, key, bundle = frozen
    original = engine.state / 'releases' / release['id'] / 'web' / 'artifacts' / 'site' / 'index.html'
    captured = original.read_bytes()

    def mutate(argv):
        assert Path(argv[-1]).read_bytes() == captured
        original.write_text('changed after verification started')

    process_fixture(monkeypatch, callback=mutate)
    out = tmp_path / 'incomplete'
    with pytest.raises(Refused):
        signatures.verify(engine.state, release['id'], 'web', 'index.html', key, bundle, out, signatures.Profile())
    assert not (out / 'report.json').exists() and (out / 'subject.blob').is_file()


# Offline inspection checks the fixed captured extent only, not recorded paths or claimed signer authority
def test_signature_report_offline_integrity_and_changed_file(frozen, tmp_path, monkeypatch):
    engine, release, key, bundle = frozen
    process_fixture(monkeypatch)
    out = tmp_path / 'report'
    value = signatures.verify(engine.state, release['id'], 'web', 'index.html', key, bundle, out, signatures.Profile())

    def forbidden(*args, **kwargs):
        raise AssertionError('Offline inspect attempted native verification or authority')

    monkeypatch.setattr(signatures, 'Process', forbidden)
    monkeypatch.setattr(Engine, '__init__', forbidden)
    monkeypatch.setattr(signatures, 'selection', forbidden)
    assert signatures.inspect(out) == value
    (out / 'subject.blob').write_text('different captured bytes')
    with pytest.raises(Refused):
        signatures.inspect(out)


# File search and authenticated console routes share the same frozen subject and exact result identities
def test_signature_files_and_api_contract(frozen, tmp_path, monkeypatch):
    engine, release, key, bundle = frozen
    process_fixture(monkeypatch)
    result = signatures.files(engine.state, release['id'], 'web', 'INDEX')
    assert result['total'] == 1 and result['files'][0]['name'] == 'index.html'
    assert signatures.files(engine.state, release['id'], 'web', 'missing')['files'] == []
    token = 'x' * 43; headers = {'Authorization': 'Bearer ' + token}
    client = TestClient(create_app(engine, token), base_url='http://127.0.0.1:8765')
    path = '/api/releases/' + release['id'] + '/signatures/'
    body = {'component': 'web', 'output_directory': str(tmp_path / 'api-report'), 'selected_file': 'index.html',
            'key_file': str(key), 'bundle_file': str(bundle)}
    assert client.post(path + 'verify', json=body).status_code == 401
    assert client.post(path + 'verify', headers=headers, json=body | {'key_file': 'kms://key'}).status_code == 422
    assert client.post(path + 'verify', headers=headers, json=body | {'key_file': str(tmp_path / 'missing-key')}).status_code == 422
    assert client.post(path + 'verify', headers=headers, json=body | {'bundle_file': str(tmp_path / 'missing-bundle')}).status_code == 422
    assert client.post(path + 'verify', headers=headers, json=body | {'raw': 'private-sentinel'}).status_code == 422
    response = client.post(path + 'verify', headers=headers, json=body)
    assert response.status_code == 200 and response.json()['report']['status'] == 'verified'
    assert response.json()['integrity_verified'] is True and 'verified' not in response.json()
    inspected = client.post(path + 'inspect', headers=headers, json={key: body[key] for key in ('component', 'output_directory')})
    assert inspected.status_code == 200 and inspected.json() == response.json()
    assert client.post('/api/releases/' + 'f' * 32 + '/signatures/inspect', headers=headers,
                       json={key: body[key] for key in ('component', 'output_directory')}).status_code == 409


def test_signature_ui_contract():
    subprocess.run(['node', 'tests/signature_ui.cjs'], cwd=Path(__file__).resolve().parents[1], check=True, timeout=30)
