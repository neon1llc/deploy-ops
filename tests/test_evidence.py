# tests/test_evidence.py - Inspect interrupted native intent without retaining a worker or its secrets.


import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from deploy_ops.cli import dispatch, main, parser
from deploy_ops.config import fingerprint
from deploy_ops.console import create_app
from deploy_ops.delivery import evidence
from deploy_ops.delivery.adapters.cloud import durable_record
from deploy_ops.delivery.engine import Engine
from deploy_ops.delivery.scaffold import create
from deploy_ops.errors import Refused


# Leave a real durable journal at a selected write boundary, without making any cloud call
@pytest.fixture
def runner(tmp_path):
    engine = Engine(create(tmp_path / 'runner'))
    release = engine.advance(engine.create('development', 'foundation', False)['id'])
    node = release['nodes']['foundation']
    node.update(adapter='ecs', status='uncertain')
    release['status'] = 'uncertain'
    release['private_note'] = 'PRIVATE-RELEASE-SENTINEL'
    engine.ledger.save(release)
    folder = engine.state / 'releases' / release['id'] / 'foundation'
    scope = {'release_id': release['id'], 'component': 'foundation', 'environment': 'development',
             'region': 'us-east-1', 'cluster': 'cluster-fixture', 'service': 'service-fixture'}
    record = {'scope': scope, 'status': 'register-submitted', 'family': 'fixture-family',
              'operation_id': 'a' * 64, 'request_digest': 'b' * 64,
              'image': 'registry.example/app@sha256:' + 'c' * 64,
              'previous_revision': 'fixture:1', 'private_note': 'PRIVATE-JOURNAL-SENTINEL'}
    return engine, release, folder, record


# Kill the fixture worker after durable intent/acknowledgement, export, discard it, then read elsewhere
@pytest.mark.parametrize('adapter,phase', [('ecs', 'register-submitted'), ('ecs', 'update-acknowledged'),
                                          ('container-apps', 'update-submitted'), ('container-apps', 'update-acknowledged')])
def test_interruption_portable_offline(runner, tmp_path, monkeypatch, adapter, phase):
    engine, release, folder, record = runner
    release['nodes']['foundation']['adapter'] = adapter
    engine.ledger.save(release)
    record['status'] = phase
    if adapter == 'container-apps':
        request = {'scope': {key: record['scope'][key] for key in ('release_id', 'component', 'environment')},
                   'image': record['image'], 'revision': 'fixture--do-owned', 'previous_revision': 'fixture--before'}
        request['scope'].update(subscription_id='00000000-0000-0000-0000-000000000001', resource_group='fixture-group',
                                name='fixture', app_id='/subscriptions/fixture/resourceGroups/fixture-group/providers/Microsoft.App/containerApps/fixture')
        record = {'request': request, 'request_digest': fingerprint(request), 'status': phase,
                  'private_note': 'PRIVATE-JOURNAL-SENTINEL'}
    if phase == 'update-acknowledged':
        record['observed'] = {'status': 'unknown', 'revision': 'fixture:2', 'message': 'PRIVATE-PROVIDER-SENTINEL'}
    path = folder / ('ecs-operation.json' if adapter == 'ecs' else 'container-apps-operation.json')
    script = ('import json,os,sys; from pathlib import Path; '
              'from deploy_ops.delivery.adapters.cloud import durable_record; '
              'durable_record(Path(sys.argv[1]),json.loads(sys.argv[2])); os._exit(73)')
    result = subprocess.run([sys.executable, '-c', script, str(path), json.dumps(record)], capture_output=True)
    assert result.returncode == 73, result.stderr
    journal_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    audit_before = engine.ledger.store.audit()

    # Capture cannot instantiate Engine, start a process, authenticate or contact a provider
    def forbidden(*args, **kwargs):
        raise AssertionError('Inspection attempted execution or network access')
    monkeypatch.setattr(Engine, '__init__', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(socket, 'socket', forbidden)
    document = evidence.capture(engine.state, release['id'], '123', '2')
    assert engine.ledger.store.audit() == audit_before
    native = document['components'][0]['native']
    assert native['phase'] == phase and native['journal_sha256'] == journal_hash
    assert native['scope']['release_id'] == release['id'] and native['recorded_at'] > 0
    assert native['observed_at'] is None  # Older acknowledgement alone is not an observation timestamp
    assert document['capture'] == 'complete' and document['outcome'] == 'uncertain'
    encoded = json.dumps(document)
    assert 'PRIVATE-' not in encoded and str(engine.state) not in encoded
    offline = tmp_path / 'offline'; offline.mkdir()
    summary = offline / 'inspection.json'
    evidence.write(summary, encoded)
    shutil.rmtree(engine.path.parent)
    assert dispatch(parser().parse_args(['--project', str(tmp_path / 'absent.toml'), 'evidence', 'inspect',
                                         str(summary), '--html', str(offline / 'report.html')])) == document
    report = (offline / 'report.html').read_text()
    assert 'Native durable phase' in report and phase in report and '<script' not in report
    assert set(offline.iterdir()) == {summary, offline / 'report.html'}
    assert summary.stat().st_mode & 0o077 == 0


# Missing and unsupported journals say exactly what the exporter can establish
def test_completeness_and_missing_record(runner):
    engine, release, folder, record = runner
    document = evidence.capture(engine.state, release['id'])
    assert document['capture'] == 'partial'
    assert document['components'][0]['journal'] == 'unavailable'
    assert 'Submission and current cloud outcome are unknown' in evidence.report(document)
    release['nodes']['foundation']['adapter'] = 'cloud-run'
    engine.ledger.save(release)
    document = evidence.capture(engine.state, release['id'])
    assert document['components'][0]['journal'] == 'unavailable'
    assert document['capture'] == 'partial'  # Missing newly supported native journal remains unknown


# Unsupported versions, extra data, altered extent and self-contradictory imports fail before rendering
@pytest.mark.parametrize('change', ['version', 'extra', 'hash', 'scope', 'presence'])
def test_refuse_changed_or_unsupported_summary(runner, change):
    engine, release, folder, record = runner
    durable_record(folder / 'ecs-operation.json', record)
    document = evidence.capture(engine.state, release['id'])
    if change == 'version':
        document['version'] = 99
    elif change == 'extra':
        document['components'][0]['native']['credentials'] = 'PRIVATE-TOKEN-SENTINEL'
    elif change == 'hash':
        document['outcome'] = 'succeeded'
    elif change == 'scope':
        document['components'][0]['native']['scope']['release_id'] = '0' * 32
    else:
        document['components'][0]['journal'] = 'unavailable'
    if change != 'hash':
        document['sha256'] = fingerprint({key: value for key, value in document.items() if key != 'sha256'})
    with pytest.raises(Refused):
        evidence.report(document)


# Never follow linked journals or export a mismatched operation into an apparently valid release
@pytest.mark.parametrize('change', ['link', 'parent', 'wrong-release', 'wrong-environment', 'oversize'])
def test_refuse_unsafe_journal(runner, tmp_path, change):
    engine, release, folder, record = runner
    path = folder / 'ecs-operation.json'
    if change == 'wrong-release':
        record['scope']['release_id'] = '0' * 32
    if change == 'wrong-environment':
        record['scope']['environment'] = 'other'
    durable_record(path, record)
    if change == 'link':
        destination = tmp_path / 'raw.json'; path.rename(destination); path.symlink_to(destination)
    if change == 'parent':
        destination = tmp_path / 'journal'; folder.rename(destination); folder.symlink_to(destination, target_is_directory=True)
    if change == 'oversize':
        path.write_bytes(b'x' * (evidence.MAX_BYTES + 1))
    with pytest.raises(Refused):
        evidence.capture(engine.state, release['id'])


# The authenticated console and CLI use the same capture extent and escaped offline renderer
def test_console_cli_and_safe_report(runner, tmp_path, monkeypatch):
    engine, release, folder, record = runner
    record['scope']['service'] = '<script>alert(1)</script>'
    durable_record(folder / 'ecs-operation.json', record)
    monkeypatch.setattr(evidence.time, 'time', lambda: 123456)
    client = TestClient(create_app(engine, 't' * 40), base_url='http://127.0.0.1:8765')
    endpoint = '/api/releases/' + release['id'] + '/inspection'
    assert client.get(endpoint).status_code == 401
    client.headers['Authorization'] = 'Bearer ' + 't' * 40
    expected = evidence.capture(engine.state, release['id'])
    assert client.get(endpoint).json() == expected
    report = client.get(endpoint + '?report=true').json()['html']
    assert '<script>' not in report and '&lt;script&gt;' in report
    out = tmp_path / 'inspection.json'
    args = ['--state', str(engine.state), 'evidence', 'capture', release['id'], '--out', str(out)]
    assert main(args) == 0
    assert evidence.inspect(out) == expected
    before = out.read_bytes()
    assert main(args) == 2 and out.read_bytes() == before
    assert main(['evidence', 'inspect', str(out), '--html', str(tmp_path / 'report.html')]) == 0


# Execute the shipped worker shell with a disposable command to prove capture cannot swallow exit meaning
@pytest.mark.parametrize('outcome', [0, 1, 2, 3])
def test_recipe_keeps_deployment_exit(tmp_path, outcome):
    recipe = yaml.safe_load((Path(__file__).parents[1] / 'integrations/github/delivery-worker.yml').read_text())
    steps = recipe['jobs']['deliver']['steps']
    command = next(item['run'] for item in steps if item.get('id') == 'delivery')
    executable = tmp_path / 'deploy-ops'
    executable.write_text('#!/bin/sh\nprintf \'{"id":"fixture","status":"uncertain"}\\n\'\nexit ' + str(outcome) + '\n')
    executable.chmod(0o700)
    environment = {**os.environ, 'PATH': str(tmp_path) + os.pathsep + os.environ['PATH'],
                   'DEPLOY_ENVIRONMENT': 'fixture', 'DEPLOY_IMAGE': 'fixture', 'DEPLOY_RECEIPTS': '{}', 'CALLER_LABEL': ''}
    result = subprocess.run(['bash', '-e', '-c', command], cwd=tmp_path, env=environment, capture_output=True)
    assert result.returncode == outcome, result.stderr
    assert json.loads((tmp_path / 'delivery-result.json').read_text())['status'] == 'uncertain'
    upload = next(item for item in steps if item.get('id') == 'inspection_upload')
    assert upload['with']['path'] == 'inspection.json'
    assert upload['with']['if-no-files-found'] == 'error'
    assert upload['with']['retention-days'] == 1


# Keep exact version 1 bytes readable without silently adding the version 2 project field
def test_legacy_inspection_extent_is_not_reserialized(runner):
    engine, release, folder, record = runner
    durable_record(folder / 'ecs-operation.json', record)
    document = evidence.capture(engine.state, release['id'])
    document['version'] = 1
    for component in document['components']:
        if component['native']:component['native']['scope'].pop('project_id')
    document['sha256'] = fingerprint({key:value for key,value in document.items() if key != 'sha256'})
    assert evidence.validate(document) == document
    assert 'project_id' not in evidence.validate(document)['components'][0]['native']['scope']


# Portable Cloud Run evidence binds both write phases while excluding arbitrary service/credential data
@pytest.mark.parametrize('phase', ['candidate-submitted', 'candidate-acknowledged', 'traffic-submitted', 'traffic-acknowledged'])
def test_cloud_run_portable_scope_and_phase(runner, tmp_path, phase):
    engine, release, folder, record = runner
    release['nodes']['foundation']['adapter'] = 'cloud-run'
    engine.ledger.save(release)
    selected = {'release_id':release['id'],'component':'foundation','environment':'development',
                'project_id':'fixture-project','region':'us-central1','name':'web',
                'service':'projects/fixture-project/locations/us-central1/services/web'}
    request = {'version':1,'scope':selected,'image':record['image'],'candidate':selected['service']+'/revisions/web-do-owned',
               'baseline':{'route':selected['service']+'/revisions/web-old'},'operation_id':'c'*64,
               'private_note':'PRIVATE-REQUEST-SENTINEL'}
    value = {'request':request,'request_digest':fingerprint(request),'status':phase,
             'observed':{'status':'baseline-serving','image':record['image'],'message':'PRIVATE-PROVIDER-SENTINEL'}}
    durable_record(folder/'cloud-run-operation.json',value)
    document = evidence.capture(engine.state,release['id'])
    assert document['version']==2
    native=document['components'][0]['native']
    assert native['provider']=='gcp' and native['phase']==phase and native['scope']['project_id']=='fixture-project'
    assert native['previous_revision']==request['baseline']['route'] and native['revision']==request['candidate']
    assert 'PRIVATE-' not in json.dumps(document)
    path=tmp_path/'portable.json';evidence.write(path,json.dumps(document))
    shutil.rmtree(engine.path.parent)
    assert evidence.inspect(path)==document
