# tests/test_save_recovery.py - Preserve authored configuration across file and audit interruption boundaries.


import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from deploy_ops.cli import dispatch, parser
from deploy_ops.console import create_app
from deploy_ops.delivery import configuration
from deploy_ops.delivery.engine import Engine
from deploy_ops.delivery.scaffold import create
from deploy_ops.errors import Refused
from deploy_ops.store import Store


# Interrupt the actual Save owner after selected durable boundaries, without changing global machine state
SCRIPT = '''
from pathlib import Path
from contextlib import contextmanager
import os,sys,time
from deploy_ops.delivery.engine import Engine
from deploy_ops.delivery import configuration as config
engine=Engine(Path(sys.argv[1])); phase=sys.argv[3]
source=Path(sys.argv[2]).read_text(); current=engine.path.read_text()
backup=config._backup
def stop_backup(path, content):
    if phase == 'intent': os._exit(73)
    backup(path,content)
    if phase == 'backup': os._exit(73)
    if phase == 'external': engine.path.write_text(current+'\\n# external owner edit\\n')
    if phase == 'active':
        Path(sys.argv[4]).touch(); time.sleep(60)
config._backup=stop_backup
replace=os.replace
def stop_replace(src,dst):
    if phase == 'backup-before' and Path(dst).name=='previous-workspace.toml': os._exit(73)
    replace(src,dst)
    if phase == 'replace' and Path(dst)==engine.path: os._exit(73)
os.replace=stop_replace
event=engine.ledger.store.event
def stop_event(*args):
    event(*args)
    if phase == 'audit' and args[3]=='workspace.updated': os._exit(73)
engine.ledger.store.event=stop_event
transaction=engine.ledger.store.transaction
@contextmanager
def stop_commit():
    with transaction() as connection: yield connection
    if phase == 'commit': os._exit(73)
engine.ledger.store.transaction=stop_commit
config.save(engine,source,config.source_hash(current))
'''


# Keep the source, draft and initialized owner in one disposable directory
@pytest.fixture
def workspace(tmp_path):
    engine = Engine(create(tmp_path / 'workspace'))
    original = engine.path.read_text()
    draft = tmp_path / 'draft.toml'
    draft.write_text(configuration.patch(original, 'components', 'web', {'timeout_seconds': 900})['toml'])
    return engine, original, draft


# Run a child to completion while retaining only bounded synthetic diagnostics
def interrupt(workspace, phase):
    engine, original, draft = workspace
    result = subprocess.run([sys.executable, '-c', SCRIPT, str(engine.path), str(draft), phase], capture_output=True, text=True)
    assert result.returncode == 73, result.stderr
    return configuration.save_recovery(engine.path, engine.state)


# File replacement and audit commit are separate facts; every supported boundary keeps exact recovery bytes
@pytest.mark.parametrize('phase', ['intent', 'backup-before', 'backup', 'replace', 'audit', 'commit'])
def test_real_save_interruption_and_reconciliation(workspace, phase):
    engine, original, draft = workspace
    backup = engine.state / 'previous-workspace.toml'
    backup.write_text(original + '\n# previous saved version\n')
    status = interrupt(workspace, phase)
    before_replace = phase in {'intent', 'backup-before', 'backup'}
    expected = original if before_replace else draft.read_text()
    assert backup.read_text() == (original + '\n# previous saved version\n' if phase in {'intent', 'backup-before'} else original)
    assert engine.path.read_text() == expected
    assert status['available'] and status['status'] == ('before_replace' if before_replace else 'after_replace')
    assert status['audit'] == ('committed' if phase == 'commit' else 'not_recorded')
    copies = Path(status['recovery_directory'])
    assert (copies / 'before.toml').read_text() == original
    assert (copies / 'draft.toml').read_text() == draft.read_text()
    for path in copies.iterdir():
        assert path.stat().st_mode & 0o077 == 0
    with pytest.raises(Refused, match='interrupted configuration Save'):
        Engine(engine.path).create('development')
    with pytest.raises(Refused, match='changed'):
        configuration.reconcile_save(engine.path, engine.state, status['operation_id'], '0' * 64, 'Inspected this interrupted Save')

    result = configuration.reconcile_save(engine.path, engine.state, status['operation_id'], status['source_hash'],
                                           'Inspected exact current file and retained authored copies')
    assert result['status'] == 'reconciled' and result['configuration_changed'] is False
    assert engine.path.read_text() == expected and not engine.path.with_suffix('.pending').exists()
    assert configuration.save_recovery(engine.path, engine.state)['status'] == 'none'
    events = [json.loads(row['payload']) for row in engine.ledger.store.audit()]
    updates = [item for item in events if item['event'] == 'workspace.updated']
    assert len(updates) == (1 if phase == 'commit' else 0)
    reconciled = [item for item in events if item['event'] == 'workspace.save_reconciled']
    assert len(reconciled) == 1 and reconciled[0]['detail']['original_audit'] == status['audit']
    before = engine.ledger.store.audit()
    assert configuration.reconcile_save(engine.path, engine.state, status['operation_id'], status['source_hash'],
                                         'Repeat inspection of the same Save')['repeat'] is True
    assert engine.ledger.store.audit() == before
    assert Engine(engine.path).create('development')['status'] == 'queued'


# External edits win; the aborted draft stays recoverable and reconciliation does not restore over them
def test_external_edit_and_malformed_current_are_kept(workspace, monkeypatch):
    engine, original, draft = workspace
    result = subprocess.run([sys.executable, '-c', SCRIPT, str(engine.path), str(draft), 'external'], capture_output=True)
    assert result.returncode != 0
    assert engine.path.read_text() == original + '\n# external owner edit\n'
    engine.path.write_text('invalid TOML [')
    source = engine.path.read_bytes()
    def forbidden(*args, **kwargs):
        raise AssertionError('Retained recovery initialized Engine')
    monkeypatch.setattr(Engine, '__init__', forbidden)
    monkeypatch.setattr(Store, '__init__', forbidden)
    arguments = ['--project', str(engine.path), '--state', str(engine.state), 'config']
    status = dispatch(parser().parse_args(arguments + ['recovery']))
    assert status['status'] == 'external_change' and status['available']
    result = dispatch(parser().parse_args(arguments + ['reconcile', '--operation-id', status['operation_id'],
        '--source-hash', status['source_hash'], '--note', 'Keep externally authored current content without replay']))
    assert result['configuration_changed'] is False and engine.path.read_bytes() == source


# A live writer holds the real interprocess lock; read-only status and refused resolution cannot release it
def test_live_save_must_stop_before_reconciliation(workspace, tmp_path):
    engine, original, draft = workspace
    ready = tmp_path / 'ready'
    worker = subprocess.Popen([sys.executable, '-c', SCRIPT, str(engine.path), str(draft), 'active', str(ready)],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 10
        while not ready.exists():
            assert worker.poll() is None
            assert time.monotonic() < deadline
            time.sleep(.01)
        assert configuration.save_recovery(engine.path, engine.state)['status'] == 'active'
        intent = json.loads((engine.state / 'workspace-save.json').read_text())
        with pytest.raises(Refused, match='active'):
            configuration.reconcile_save(engine.path, engine.state, intent['id'], intent['before_hash'], 'Inspected while original writer is live')
        assert worker.poll() is None
    finally:
        worker.kill(); worker.communicate(timeout=5)
    assert configuration.save_recovery(engine.path, engine.state)['available']


# Missing or changed ownership, copies and pending drafts cannot be cleared with a force option
@pytest.mark.parametrize('change', ['lock', 'copy', 'pending', 'relocate'])
def test_unverified_recovery_refuses_without_overwriting(workspace, tmp_path, change):
    engine, original, draft = workspace
    status = interrupt(workspace, 'backup')
    if change == 'lock':
        (engine.state / 'workspace.lock').unlink(); (engine.state / 'workspace.lock').touch()
    elif change == 'copy':
        (Path(status['recovery_directory']) / 'draft.toml').write_text('unrelated retained draft')
    elif change == 'pending':
        engine.path.with_suffix('.pending').write_text('unrelated current draft')
    else:
        destination = tmp_path / 'relocated'; shutil.copytree(engine.path.parent, destination)
        engine.path = destination / engine.path.name; engine.state = destination / '.deploy-ops'
    assert not configuration.save_recovery(engine.path, engine.state)['available']
    before = engine.path.read_bytes()
    with pytest.raises(Refused):
        configuration.reconcile_save(engine.path, engine.state, status['operation_id'], status['source_hash'], 'Inspected unverifiable interrupted Save')
    assert engine.path.read_bytes() == before


# The authenticated API revalidates exact current identity and reloads only valid configuration after reconciliation
def test_api_parity_and_committed_reconciliation_retry(workspace, monkeypatch):
    engine, original, draft = workspace
    status = interrupt(workspace, 'commit')
    client = TestClient(create_app(engine, 't' * 40), base_url='http://127.0.0.1:8765')
    assert client.get('/api/config/recovery').status_code == 401
    client.headers['Authorization'] = 'Bearer ' + 't' * 40
    assert client.get('/api/config/recovery').json() == status
    body = {'operation_id': status['operation_id'], 'source_hash': status['source_hash'], 'note': 'Inspected current exact file and recovery copies'}
    response = client.post('/api/config/reconcile', json=body)
    assert response.status_code == 200 and response.json()['configuration_valid']
    assert client.get('/api/workspace').json()['source_hash'] == status['source_hash']
    assert client.post('/api/config/reconcile', json=body).json()['repeat']


# A second interruption after reconciliation commits retains that fact and cannot append a duplicate observation
@pytest.mark.parametrize('external_change', [False, True])
def test_reconciliation_crash_after_audit_commit(workspace, external_change):
    engine, original, draft = workspace
    status = interrupt(workspace, 'backup')
    script = '''
import os,sys
from pathlib import Path
from contextlib import contextmanager
from deploy_ops.store import Store
from deploy_ops.delivery.configuration import reconcile_save
transaction=Store.transaction
@contextmanager
def interrupt(self):
    with transaction(self) as connection: yield connection
    os._exit(73)
Store.transaction=interrupt
reconcile_save(Path(sys.argv[1]),Path(sys.argv[2]),sys.argv[3],sys.argv[4],'Inspected retained current file and copies')
'''
    result = subprocess.run([sys.executable, '-c', script, str(engine.path), str(engine.state), status['operation_id'],
                             status['source_hash']], capture_output=True)
    assert result.returncode == 73, result.stderr
    retained = configuration.save_recovery(engine.path, engine.state)
    assert retained['reconciliation'] == 'recorded' and retained['pending_owned']
    audit = engine.ledger.store.audit()
    if external_change:
        engine.path.write_text(original + '\n# edit after recorded reconciliation\n')
    latest = configuration.save_recovery(engine.path, engine.state)
    result = configuration.reconcile_save(engine.path, engine.state, status['operation_id'], latest['source_hash'],
                                  'Repeat inspection after confirmed reconciliation process exit')
    assert len(engine.ledger.store.audit()) == len(audit) + int(external_change)
    assert result['repeat'] is not external_change
    assert engine.ledger.store.verify()['events'] == len(audit) + int(external_change)
    assert engine.path.read_text() == (original + '\n# edit after recorded reconciliation\n' if external_change else original)


# Authored line endings are source bytes, independent of semantic TOML identity
def test_exact_crlf_source_and_recovery_copies(workspace, tmp_path):
    engine, original, draft = workspace
    authored = original.replace('\n', '\r\n')
    engine.path.write_bytes(authored.encode())
    revised = authored + '\r\n# an authored change\r\n'
    draft.write_bytes(revised.encode())
    alias = tmp_path / 'workspace-alias.toml'; alias.symlink_to(engine.path)
    inspected = dispatch(parser().parse_args(['--project', str(alias), 'config', 'inspect']))
    assert inspected['source_hash'] == configuration.source_hash(authored)
    result = dispatch(parser().parse_args(['--project', str(alias), 'config', 'save', '--file', str(draft),
        '--source-hash', inspected['source_hash']]))
    copies = Path(result['recovery_directory'])
    assert engine.path.read_bytes() == revised.encode()
    assert (copies / 'before.toml').read_bytes() == authored.encode()
    assert (copies / 'draft.toml').read_bytes() == revised.encode()
    assert (engine.state / 'previous-workspace.toml').read_bytes() == authored.encode()


# Equal before/draft bytes cannot reveal which side of replacement a crash reached
def test_unchanged_source_does_not_invent_a_replace_phase(workspace):
    engine, original, draft = workspace
    draft.write_text(original)
    status = interrupt(workspace, 'commit')
    assert status['status'] == 'same_content' and status['audit'] == 'committed'
    configuration.reconcile_save(engine.path, engine.state, status['operation_id'], status['source_hash'],
                                  'Retained identical current content; replacement phase is not inferred')
    assert engine.path.read_text() == original


# Explicit reconciliation cannot extend a damaged retained audit chain
def test_reconciliation_refuses_damaged_audit(workspace):
    engine, original, draft = workspace
    with engine.ledger.store.transaction() as connection:
        engine.ledger.store.event(connection, 123, 'fixture', 'fixture.original', 'workspace', {})
    status = interrupt(workspace, 'backup')
    with engine.ledger.store.transaction() as connection:
        connection.execute("UPDATE audit SET payload='{}' WHERE sequence=1")
    with pytest.raises(Refused, match='Audit chain'):
        configuration.reconcile_save(engine.path, engine.state, status['operation_id'], status['source_hash'],
                                      'Inspect retained files without extending a broken audit chain')
    assert engine.path.read_text() == original
    assert (engine.state / 'workspace-save.json').exists()


# A validated existing-state writer must not create a database removed before its connection opens
def test_existing_writer_never_recreates_removed_state(tmp_path):
    path = tmp_path / 'state.db'
    Store(str(path))
    writer = Store.open_existing(path, writable=True)
    path.unlink()
    with pytest.raises(sqlite3.OperationalError):
        with writer.connect():
            pass
    assert not path.exists()
