# tests/test_backup.py - Restore exact private history while refusing imported execution authority.


import json
import shutil
import subprocess
from pathlib import Path

import pytest
import httpx
from fastapi.testclient import TestClient

from deploy_ops.cli import dispatch, parser
from deploy_ops.console import create_app
from deploy_ops.config import fingerprint
from deploy_ops.delivery import backup
from deploy_ops.delivery.configuration import workspace_lock
from deploy_ops.delivery.engine import Engine
from deploy_ops.delivery.ledger import Ledger
from deploy_ops.delivery.retained import Retained
from deploy_ops.delivery.serving import Sessions
from deploy_ops.delivery.scaffold import create
from deploy_ops.errors import Refused
from deploy_ops.store import Store


# Complete explicit local approvals through the actual delivery owner
def deliver(engine):
    release = engine.create('development')
    for attempt in range(4):
        release = engine.advance(release['id'])
        if release['status'] != 'waiting':
            break
        for name, node in release['nodes'].items():
            if node['status'] == 'waiting':
                engine.approve(release['id'], name, node['request_digest'])
    assert release['status'] == 'succeeded'
    return release


# Prepare a real local immutable release and its retained static bytes
@pytest.fixture
def retained(tmp_path):
    engine = Engine(create(tmp_path / 'workspace'))
    release = deliver(engine)
    return engine, release


# Capture DB and owned files together; neither capture nor a moved restore rewrites historical identities
def test_coherent_capture_and_relocated_restore(retained, tmp_path):
    engine, release = retained
    audit = engine.ledger.store.audit()
    bundle = tmp_path / 'backup'
    captured = backup.capture(engine.path, engine.state, bundle)
    manifest = captured['manifest']
    assert captured['verified'] and manifest['capture'] == 'complete'
    assert engine.ledger.store.audit() == audit
    assert not (bundle / 'state/state.db-wal').exists()
    assert Ledger.open_existing(bundle / 'state').store.audit() == audit
    with pytest.raises(Refused, match='inspection-only'):
        Store.open_existing(bundle / 'state/state.db', writable=True)
    with pytest.raises(Refused, match='inspection-only'):
        Engine(engine.path, bundle / 'state')
    restored = backup.restore(bundle, tmp_path / 'restored')
    assert not restored['original_location'] and not restored['execution_admitted'] and not restored['paths_rewritten']
    state = Path(restored['state'])
    assert Ledger.open_existing(state).get(release['id']) == release
    assert Ledger.open_existing(state).store.audit() == audit
    assert Path(restored['project']).read_bytes() == engine.path.read_bytes()
    with pytest.raises(Refused, match='inspection-only'):
        Engine(Path(restored['project']))
    with pytest.raises(Refused, match='inspection-only'):
        Store(str(state / 'state.db'))
    with pytest.raises(Refused, match='inspection-only'):
        Store.open_existing(state / 'state.db', writable=True)
    alias = tmp_path / 'state-alias.db'; alias.symlink_to(state / 'state.db')
    with pytest.raises(Refused, match='inspection-only'):
        Store(str(alias))


# Runtime credential homes are excluded even when an interrupted old version left them behind
def test_credential_homes_are_not_backup_payload(retained, tmp_path):
    engine, release = retained
    home = engine.state / 'releases' / release['id'] / 'web' / 'home'
    home.mkdir(exist_ok=True)
    (home / 'token.txt').write_text('PRIVATE-CREDENTIAL-SENTINEL')
    bundle = tmp_path / 'backup'
    result = backup.capture(engine.path, engine.state, bundle)
    assert any(name.endswith('/home') for name in result['manifest']['omitted'])
    assert not any('PRIVATE-CREDENTIAL-SENTINEL' in path.read_text(errors='replace') for path in bundle.rglob('*') if path.is_file())


# Imported manifests cannot redirect writes, claim a new format or silently lose exact bytes
@pytest.mark.parametrize('change', ['path', 'version', 'payload'])
def test_changed_or_unsafe_backup_never_creates_restore_target(retained, tmp_path, change):
    engine, release = retained
    bundle = tmp_path / 'backup'
    backup.capture(engine.path, engine.state, bundle)
    path = bundle / 'manifest.json'
    document = json.loads(path.read_text())
    if change == 'payload':
        (bundle / 'workspace.toml').write_text('changed')
    else:
        if change == 'path':
            document['files'][0]['path'] = '../outside'
        else:
            document['version'] = 2
        document['sha256'] = fingerprint({key: value for key, value in document.items() if key != 'sha256'})
        path.write_text(json.dumps(document))
    destination = tmp_path / 'restore'
    with pytest.raises(Refused):
        backup.restore(bundle, destination)
    assert not destination.exists()


# Existing owner directories and the live workspace are never overwrite targets
def test_backup_and_restore_preserve_current_owner_work(retained, tmp_path):
    engine, release = retained
    with pytest.raises(Refused):
        backup.capture(engine.path, engine.state, engine.path.parent / 'backup')
    bundle = tmp_path / 'backup'
    backup.capture(engine.path, engine.state, bundle)
    with pytest.raises(Refused):
        backup.capture(engine.path, engine.state, bundle)
    owner = tmp_path / 'owner'; owner.mkdir(); (owner / 'work').write_text('owned')
    with pytest.raises(Refused):
        backup.restore(bundle, owner)
    assert (owner / 'work').read_text() == 'owned'


# Original-location restoration is an explicit owned-fixture operation, not relocated path rewriting
def test_original_location_preserves_absolute_paths(retained, tmp_path):
    engine, release = retained
    bundle = tmp_path / 'backup'
    backup.capture(engine.path, engine.state, bundle)
    project = engine.path
    assert project.parent == tmp_path / 'workspace'
    shutil.rmtree(project.parent)
    restored = backup.restore(bundle, project.parent)
    assert restored['original_location'] and not restored['execution_admitted']
    assert Ledger.open_existing(engine.state).get(release['id']) == release
    with pytest.raises(Refused, match='inspection-only'):
        with engine.ledger.store.connect():
            pass


# Original-path static readback reuses the serving owner and never initializes grants, schema or an Engine
def test_restored_original_static_http_readback_and_relocated_refusal(retained, tmp_path, monkeypatch):
    engine, release = retained
    site = Path(release['nodes']['web']['outputs']['path'])
    expected = (site / 'index.html').read_bytes()
    audit = engine.ledger.store.audit()
    bundle = tmp_path / 'backup'
    backup.capture(engine.path, engine.state, bundle)
    relocated = backup.restore(bundle, tmp_path / 'relocated')
    sessions = Sessions()
    try:
        with pytest.raises(Refused):
            sessions.start(Retained(Path(relocated['state'])), release['id'], 'web')
        assert sessions.list() == []
    finally:
        sessions.close()
    shutil.rmtree(engine.path.parent)
    backup.restore(bundle, engine.path.parent)
    def forbidden(*args, **kwargs):
        raise AssertionError('Retained static readback initialized a writer')
    monkeypatch.setattr(Engine, '__init__', forbidden)
    monkeypatch.setattr(Store, '__init__', forbidden)
    view = Retained(engine.state)
    sessions = Sessions()
    try:
        session = sessions.start(view, release['id'], 'web')
        response = httpx.get(session['url'], trust_env=False)
        assert response.status_code == 200 and response.content == expected
        assert view.ledger.store.audit() == audit
        assert not hasattr(view, 'advance') and not hasattr(view, 'gate')
    finally:
        sessions.close()
    assert sessions.list() == []


# CLI and authenticated API inspect the same exact extent and reject stale restore intent before writing
def test_cli_api_parity_and_digest_guard(retained, tmp_path):
    engine, release = retained
    client = TestClient(create_app(engine, 't' * 40), base_url='http://127.0.0.1:8765')
    assert client.get('/api/backups/options').status_code == 401
    client.headers['Authorization'] = 'Bearer ' + 't' * 40
    options = client.get('/api/backups/options').json()
    assert options['restore_mode'] == 'inspection-only' and not options['execution_admitted']
    assert client.post('/api/backups/capture', json={'backup_directory': 'relative'}).status_code == 422
    bundle = tmp_path / 'backup'
    response = client.post('/api/backups/capture', json={'backup_directory': str(bundle)})
    assert response.status_code == 200, response.text
    captured = response.json()
    assert dispatch(parser().parse_args(['backup', 'inspect', str(bundle)])) == captured
    assert client.post('/api/backups/inspect', json={'backup_directory': str(bundle)}).json() == captured
    destination = tmp_path / 'new-workspace'
    body = {'backup_directory': str(bundle), 'workspace_directory': str(destination), 'digest': '0' * 64}
    assert client.post('/api/backups/restore', json=body).status_code == 409
    assert not destination.exists()
    body['digest'] = captured['manifest']['sha256']
    result = client.post('/api/backups/restore', json=body)
    assert result.status_code == 200 and not result.json()['execution_admitted']
    inspected = client.post('/api/backups/inspect-restored', json={'workspace_directory': str(destination)})
    assert inspected.status_code == 200 and inspected.json() == result.json()
    assert dispatch(parser().parse_args(['backup', 'inspect-restored', str(destination)])) == result.json()
    history = dispatch(parser().parse_args(['--state', result.json()['state'], 'status', release['id']]))
    assert history == release


# Lost dependent bytes preserve narrower history while native serving continues to refuse them
def test_partial_history_preserves_missing_and_changed_prepared_evidence(retained, tmp_path):
    engine, release = retained
    prepared = engine.state / 'releases' / release['id'] / 'web' / 'prepared.json'
    prepared.unlink()
    bundle = tmp_path / 'backup'
    result = backup.capture(engine.path, engine.state, bundle)
    assert result['manifest']['capture'] == 'partial'
    assert any(name.endswith('/web/prepared.json') for name in result['manifest']['missing'])
    shutil.rmtree(engine.path.parent)
    restored = backup.restore(bundle, engine.path.parent)
    assert Ledger.open_existing(Path(restored['state'])).get(release['id']) == release
    sessions = Sessions()
    try:
        with pytest.raises(Refused):
            sessions.start(Retained(Path(restored['state'])), release['id'], 'web')
        assert sessions.list() == []
    finally:
        sessions.close()


# A committed WAL row belongs in the native snapshot even when it is not checkpointed into the main file
def test_capture_includes_live_wal_commits(retained, tmp_path):
    engine, release = retained
    with engine.ledger.store.connect() as held:
        held.execute('BEGIN')
        held.execute('SELECT count(*) FROM audit').fetchone()
        with engine.ledger.store.transaction() as connection:
            engine.ledger.store.event(connection, 123, 'fixture', 'backup.wal-proof', release['id'], {})
        wal = engine.state / 'state.db-wal'
        assert wal.is_file() and wal.stat().st_size > 0
        audit = engine.ledger.store.audit()
        result = backup.capture(engine.path, engine.state, tmp_path / 'backup')
        assert Ledger.open_existing(Path(result['bundle']) / 'state').store.audit() == audit


# Unknown retained locks block capture before any destination is created; nothing is force-cleared
def test_capture_refuses_retained_operation_lock(retained, tmp_path):
    engine, release = retained
    scope = 'release:' + release['id']
    engine.ledger.lock(scope, release['id'])
    try:
        with pytest.raises(Refused, match='operation lock'):
            backup.capture(engine.path, engine.state, tmp_path / 'backup')
        assert not (tmp_path / 'backup').exists()
        with engine.ledger.store.connect() as connection:
            assert connection.execute('SELECT release_id FROM release_locks WHERE scope=?', (scope,)).fetchone()[0] == release['id']
    finally:
        engine.ledger.unlock(scope, release['id'])


# Missing current configuration remains explicitly missing; restoration never fabricates a source file
def test_missing_configuration_keeps_narrower_history(retained, tmp_path):
    engine, release = retained
    engine.path.unlink()
    bundle = tmp_path / 'backup'
    captured = backup.capture(engine.path, engine.state, bundle)
    assert captured['manifest']['capture'] == 'partial'
    assert 'workspace.toml' in captured['manifest']['missing']
    result = backup.restore(bundle, tmp_path / 'restored', tmp_path / 'separate-state')
    assert not Path(result['project']).exists()
    assert Ledger.open_existing(Path(result['state'])).get(release['id']) == release
    with pytest.raises(Refused, match='inspection-only'):
        Engine(Path(result['project']))  # The workspace marker also protects an omitted custom --state


# An interrupted new capture has no completion manifest and cannot damage an earlier completed backup
def test_capture_failure_preserves_previous_backup(retained, tmp_path, monkeypatch):
    engine, release = retained
    completed = tmp_path / 'completed'
    backup.capture(engine.path, engine.state, completed)
    original_manifest = (completed / 'manifest.json').read_bytes()
    write = backup.write_file
    def interrupted(path, content, executable=False):
        if path.name == 'workspace.toml':
            raise OSError('synthetic interrupted private copy')
        return write(path, content, executable)
    monkeypatch.setattr(backup, 'write_file', interrupted)
    incomplete = tmp_path / 'incomplete'
    with pytest.raises(OSError):
        backup.capture(engine.path, engine.state, incomplete)
    assert incomplete.exists() and not (incomplete / 'manifest.json').exists()
    with pytest.raises(Refused):
        backup.inspect(incomplete)
    assert (completed / 'manifest.json').read_bytes() == original_manifest
    assert backup.inspect(completed)['verified']


# Exercise actual browser handlers with a DOM fixture; keep browser and assistive-technology claims separate
def test_backup_dom_contract():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is required for the dependency-free backup DOM contract')
    root = Path(__file__).resolve().parents[1]
    subprocess.run([node, str(root / 'tests/backup_ui.cjs')], cwd=root, check=True, capture_output=True, text=True, timeout=20)


# Restored receipts remain useful without their source bundle; a later changed file cannot pass verification
def test_restored_extent_verifies_without_original_bundle(retained, tmp_path):
    engine, release = retained
    bundle = tmp_path / 'backup'
    backup.capture(engine.path, engine.state, bundle)
    restored = tmp_path / 'restored'
    result = backup.restore(bundle, restored)
    shutil.rmtree(bundle)
    assert backup.inspect_restored(restored) == result
    source = Path(result['state']) / 'releases' / release['id'] / 'web' / 'source' / 'index.html'
    source.write_bytes(b'changed after restore')
    with pytest.raises(Refused, match='hash or size'):
        backup.inspect_restored(restored)
    assert Ledger.open_existing(Path(result['state'])).get(release['id']) == release


# Configured custom state cannot be omitted to bypass the restored workspace's write boundary
def test_restored_workspace_blocks_alternate_new_state(retained, tmp_path):
    engine, release = retained
    bundle = tmp_path / 'backup'; backup.capture(engine.path, engine.state, bundle)
    result = backup.restore(bundle, tmp_path / 'restored', tmp_path / 'custom-state')
    alternative = Path(result['project']).parent / 'new-state'
    with pytest.raises(Refused, match='inspection-only'):
        Engine(Path(result['project']), alternative)
    assert not alternative.exists()
    with pytest.raises(Refused, match='inspection-only'):
        with workspace_lock(Path(result['state'])):
            pass
    assert not (Path(result['state']) / 'workspace.lock').exists()


# A valid legacy component name cannot turn its credential cache into an aggregated source repository
def test_shared_repository_namespace_never_captures_runtime_home(tmp_path):
    path = create(tmp_path / 'workspace')
    path.write_text(path.read_text().replace('components.web', 'components.repositories'))
    engine = Engine(path)
    release = deliver(engine)
    folder = engine.state / 'releases' / release['id'] / 'repositories'
    home = folder / 'home'; home.mkdir()
    (home / 'token').write_text('PRIVATE-LEGACY-HOME-SENTINEL')
    result = backup.capture(engine.path, engine.state, tmp_path / 'backup')
    assert result['manifest']['capture'] == 'partial'
    assert any(name.endswith('/repositories') for name in result['manifest']['unavailable'])
    assert any(name.endswith('/repositories/home') for name in result['manifest']['omitted'])
    assert not any('/repositories/home/' in item['path'] for item in result['manifest']['files'])
