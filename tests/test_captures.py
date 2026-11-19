# tests/test_captures.py - Distinguish partial private source capture from registered deployment state.


import threading

import pytest
from fastapi.testclient import TestClient

from deploy_ops import cli
from deploy_ops.client import private_json
from deploy_ops.console import create_app
from deploy_ops.delivery import captures
from deploy_ops.delivery.engine import Engine
from deploy_ops.delivery.scaffold import create
from deploy_ops.errors import Refused


# Use real owned sources while leaving all provider and build paths unstarted
@pytest.fixture
def engine(tmp_path):
    return Engine(create(tmp_path / 'workspace'))


# A later source refusal records what was copied without creating a fake release or touching unknown folders
def test_partial_capture_is_readable_but_not_registered(engine):
    unknown = engine.state / 'releases' / ('a' * 32)
    unknown.mkdir(parents=True)
    untouched = unknown / 'keep.txt'
    untouched.write_text('existing unrecorded folder')
    before = untouched.stat()
    source = engine.path.parent / 'application'
    (source / 'unsupported-link').symlink_to(source / 'index.html')
    with pytest.raises(Refused, match='Source capture') as error:
        engine.create('development')
    history = captures.list_records(engine.state)
    assert history['total'] == 1 and not engine.ledger.list()
    result = history['records'][0]
    assert result['id'] in str(error.value) and result['registration']['state'] == 'not-registered'
    record = result['record']
    assert record['phase'] == 'refused' and record['stopped_during'] == 'capturing-repository'
    assert record['repository'] == 'application' and record['copied_components'] == ['foundation']
    assert set(record['captured_repositories']) == {'infrastructure'}
    with engine.ledger.store.connect() as connection:
        assert connection.execute('SELECT COUNT(*) FROM requests').fetchone()[0] == 0
    assert untouched.read_text() == 'existing unrecorded folder'
    assert (untouched.stat().st_mode, untouched.stat().st_mtime_ns) == (before.st_mode, before.st_mtime_ns)
    assert not (unknown / captures.FILENAME).exists()


# Successful registration joins the same identity and bindings; the marker does not manufacture a deployment
def test_successful_capture_links_existing_release(engine):
    release = engine.create('development')
    result = captures.inspect(engine.state, release['id'])
    assert result['registration'] == {'state': 'registered', 'release_id': release['id'], 'release_status': 'queued'}
    assert result['record']['phase'] == 'registration-requested'
    assert result['record']['source_digest'] == release['source_digest']
    assert result['record']['copied_components'] == release['order']
    assert len(engine.ledger.list()) == captures.list_records(engine.state)['total'] == 1
    assert result['record']['repositories']['application']['allow_uncommitted'] is True


# A real capture barrier is visible while its creator remains active, without liveness or resume claims
def test_running_capture_phase_and_later_registration(engine, monkeypatch):
    import deploy_ops.delivery.engine as module
    native = module.snapshot
    reached, proceed = threading.Event(), threading.Event()
    outcome = []
    before = engine.ledger.store.verify()

    # Hold the second repository after its phase record and first successful component copy
    def capture(repository, base, destination):
        if repository.path == 'application':
            reached.set()
            assert proceed.wait(5)
        return native(repository, base, destination)

    # Preserve exceptions for the joining test instead of silently losing a worker failure
    def create_release():
        try:
            outcome.append(engine.create('development'))
        except BaseException as error:
            outcome.append(error)

    monkeypatch.setattr(module, 'snapshot', capture)
    worker = threading.Thread(target=create_release)
    worker.start()
    try:
        assert reached.wait(5)
        result = captures.list_records(engine.state)['records'][0]
        assert result['record']['phase'] == 'capturing-repository'
        assert result['record']['repository'] == 'application'
        assert result['registration']['state'] == 'not-registered'
        assert 'not liveness proof' in result['scope']
        assert engine.ledger.store.verify() == before
    finally:
        proceed.set()
        worker.join(5)
    assert not worker.is_alive() and len(outcome) == 1 and isinstance(outcome[0], dict)
    assert captures.inspect(engine.state, result['id'])['registration']['state'] == 'registered'


# Cheap missing-source validation does not allocate an incomplete capture root
def test_missing_source_refuses_before_capture(engine):
    (engine.path.parent / 'application').rename(engine.path.parent / 'retained-application')
    with pytest.raises(Refused, match='no source capture started'):
        engine.create('development')
    assert captures.list_records(engine.state)['total'] == 0 and not engine.ledger.list()
    assert not (engine.state / 'releases').exists()


# Configuration loss and missing registration remain readable without creating a database or runtime
def test_capture_reader_without_current_configuration_or_registration(engine, monkeypatch):
    release = engine.create('development', 'foundation')
    engine.path.write_text('invalid = [ workspace')
    audit = engine.ledger.store.verify()
    monkeypatch.setattr('deploy_ops.delivery.engine.Engine', lambda *args, **kwargs: pytest.fail('Read-only capture inspection bootstrapped execution'))
    args = cli.parser().parse_args(['--project', str(engine.path), 'captures', 'show', release['id']])
    assert cli.dispatch(args)['registration']['state'] == 'registered'
    assert engine.ledger.store.verify() == audit
    database = engine.state / 'state.db'
    retained = engine.state / 'kept-state.db'
    database.rename(retained)
    try:
        observed = captures.inspect(engine.state, release['id'])
        assert observed['available'] and observed['registration']['state'] == 'unavailable'
        assert not database.exists()
    finally:
        retained.rename(database)


# A valid-looking folder or changed marker cannot adopt a different registered release
def test_mismatched_marker_and_existing_root_are_not_adopted(engine):
    release = engine.create('development', 'foundation')
    other = 'b' * 32
    folder = engine.state / 'releases' / other
    folder.mkdir()
    record = captures.inspect(engine.state, release['id'])['record']
    private_json(folder / captures.FILENAME, record)
    before = (folder / captures.FILENAME).read_bytes()
    with pytest.raises(Refused, match='unavailable or unsupported'):
        captures.inspect(engine.state, other)
    with pytest.raises(Refused, match='not adopted'):
        captures.begin(engine.state, other, engine.spec.name, 'development', engine.spec.digest,
                       ['foundation'], {'infrastructure': engine.spec.repositories['infrastructure']})
    assert (folder / captures.FILENAME).read_bytes() == before
    assert len(engine.ledger.list()) == 1


# Authenticated API and the shared reader expose the same evidence without registering or executing work
def test_capture_api_authentication_and_paging(engine):
    release = engine.create('development', 'foundation')
    token = 'x' * 43
    before = engine.ledger.store.verify()
    with TestClient(create_app(engine, token), base_url='http://127.0.0.1:8765') as client:
        assert client.get('/api/captures').status_code == 401
        client.headers['Authorization'] = 'Bearer ' + token
        result = client.get('/api/captures?limit=1').json()
        assert result['total'] == 1 and result['records'][0]['id'] == release['id']
        assert client.get('/api/captures/' + release['id']).json()['record'] == result['records'][0]['record']
        assert client.get('/api/captures?limit=101').status_code == 422
    assert engine.ledger.store.verify() == before


# Offset pages disclose their moving observation extent without losing a record in a stable history
def test_capture_history_pages_use_the_shared_bounds(engine):
    identifiers = {engine.create('development', 'foundation')['id'] for _ in range(2)}
    first = captures.list_records(engine.state, 0, 1)
    second = captures.list_records(engine.state, 1, 1)
    assert first['total'] == second['total'] == 2
    assert first['has_more'] and not second['has_more']
    assert {first['records'][0]['id'], second['records'][0]['id']} == identifiers


# Private restore preserves the readable marker while explicitly omitting unregistered partial source trees
def test_partial_capture_marker_backup_and_read_only_restore(engine):
    from pathlib import Path
    from deploy_ops.delivery import backup
    source = engine.path.parent / 'application'
    (source / 'unsupported-link').symlink_to(source / 'index.html')
    with pytest.raises(Refused):
        engine.create('development')
    observed = captures.list_records(engine.state)['records'][0]
    parent = engine.path.parent.parent
    bundle = parent / 'private-backup'
    result = backup.capture(engine.path, engine.state, bundle)
    marker = 'state/releases/' + observed['id'] + '/' + captures.FILENAME
    assert marker in {item['path'] for item in result['manifest']['files']}
    assert 'state/releases/' + observed['id'] + '/repositories' in result['manifest']['omitted']
    restored = backup.restore(bundle, parent / 'restored-workspace', parent / 'restored-state')
    state = Path(restored['state'])
    assert captures.inspect(state, observed['id'])['record'] == observed['record']
    assert captures.inspect(state, observed['id'])['registration']['state'] == 'not-registered'
    assert not (state / 'releases' / observed['id'] / 'repositories').exists()
    token = 'x' * 43
    with TestClient(create_app(engine, token), base_url='http://127.0.0.1:8765') as client:
        client.headers['Authorization'] = 'Bearer ' + token
        response = client.post('/api/captures/' + observed['id'] + '/inspect', json={'state_directory': str(state)})
        assert response.status_code == 200 and response.json()['record'] == observed['record']
        assert response.json()['state_directory'] == str(state)
        engine.path.write_text('invalid = [ workspace')
        assert client.post('/api/captures/' + observed['id'] + '/inspect', json={'state_directory': str(state)}).status_code == 200
        missing = parent / 'absent-state-do-not-create'
        assert client.post('/api/captures/' + observed['id'] + '/inspect', json={'state_directory': str(missing)}).status_code == 422
        assert not missing.exists()
        assert client.post('/api/captures/' + observed['id'] + '/inspect', json={'state_directory': 'relative-state'}).status_code == 422
    with pytest.raises(Refused, match='inspection-only'):
        Engine(Path(restored['project']), state)
