# tests/test_history.py - Browse retained rows without workspace initialization or execution authority.


import json
from pathlib import Path
import subprocess

from fastapi.testclient import TestClient
import pytest

from deploy_ops.cli import dispatch, parser
from deploy_ops.console import create_app
from deploy_ops.delivery.engine import Engine
from deploy_ops.delivery.ledger import Ledger
from deploy_ops.delivery.scaffold import create
from deploy_ops.errors import Refused


# Synthetic history is metadata coverage, never completed deployment evidence
@pytest.fixture
def archive(tmp_path):
    project = create(tmp_path / 'workspace')
    engine = Engine(project)
    with engine.ledger.store.transaction() as connection:
        for i in range(1, 206):
            component = 'old-canary' if i == 1 else 'web'
            value = {'id': f'{i:032x}', 'project': 'history-fixture', 'environment': 'retired' if i == 1 else 'development',
                'status': 'failed' if i == 1 else 'succeeded', 'nodes': {component: {'status': 'succeeded'}}, 'order': [component]}
            connection.execute('INSERT INTO releases(id,environment,status,data) VALUES(?,?,?,?)',
                (value['id'], value['environment'], value['status'], json.dumps(value)))
    return project, engine


# Find old historical names literally while preserving compatibility recent lists
def test_literal_filters_find_older_than_recent_extent(archive):
    project, engine = archive
    assert len(engine.ledger.list()) == 200
    result = engine.ledger.history(query='OLD-CANARY', environment='retired', component='old-canary', outcome='failed')
    assert result['total'] == 1 and result['releases'][0]['id'] == f'{1:032x}'
    assert not result['has_more'] and result['next_cursor'] is None
    for literal in ('%', '_', "' OR 1=1 --"):
        assert engine.ledger.history(query=literal)['total'] == 0


# Keyset traversal stays complete and duplicate-free when new rows arrive
def test_pages_pin_creation_extent_and_newest_refresh(archive):
    project, engine = archive
    first = engine.ledger.history()
    with engine.ledger.store.transaction() as connection:
        row = {'id': f'{206:032x}', 'project': 'new-entry', 'environment': 'development', 'status': 'waiting', 'nodes': {}}
        connection.execute('INSERT INTO releases(id,environment,status,data) VALUES(?,?,?,?)',
            (row['id'], row['environment'], row['status'], json.dumps(row)))
    seen, page = [], first
    while True:
        seen.extend(row['id'] for row in page['releases'])
        assert page['total'] == 205
        if not page['has_more']:
            break
        page = engine.ledger.history(cursor=page['next_cursor'])
    assert seen == [f'{i:032x}' for i in range(205, 0, -1)]
    assert engine.ledger.history()['releases'][0]['id'] == f'{206:032x}'
    assert engine.ledger.history(cursor=first['cursor'])['releases'] == first['releases']


# Cursors cannot silently change filters or cross selected stores
def test_cursor_is_bound_to_filters_and_selected_store(archive, tmp_path):
    project, engine = archive
    cursor = engine.ledger.history()['next_cursor']
    with pytest.raises(Refused, match='filters or retained store'):
        engine.ledger.history(cursor=cursor, query='web')
    other = Engine(create(tmp_path / 'other'))
    with pytest.raises(Refused, match='filters or retained store'):
        other.ledger.history(cursor=cursor)


# Invalid bounds/cursors refuse before they can become broad wildcard or authority queries
@pytest.mark.parametrize('values', [{'limit': 0}, {'limit': 101}, {'limit': True}, {'query': 'x' * 97},
    {'outcome': 'healthy'}, {'component': '../escape'}, {'cursor': 'invalid?'}, {'cursor': 'x' * 513}])
def test_history_refuses_invalid_inputs(archive, values):
    with pytest.raises(Refused):
        archive[1].ledger.history(**values)


# CLI/API share the query and retained reads do not load invalid source or construct an Engine
def test_source_free_cli_and_authenticated_api_parity(archive, monkeypatch):
    project, engine = archive
    arguments = ['--project', str(project), 'status', '--query', 'old-canary', '--outcome', 'failed', '--limit', '10']
    expected = dispatch(parser().parse_args(arguments))
    client = TestClient(create_app(engine, 't' * 40), base_url='http://127.0.0.1:8765')
    assert client.get('/history.js').status_code == 200
    assert client.get('/api/releases/history').status_code == 401
    response = client.get('/api/releases/history?query=old-canary&outcome=failed&limit=10',
        headers={'Authorization': 'Bearer ' + 't' * 40})
    assert response.status_code == 200 and response.json() == expected
    project.write_text('Invalid workspace source')

    def forbidden(*args, **kwargs):
        raise AssertionError('Engine construction during retained history inspection')

    monkeypatch.setattr(Engine, '__init__', forbidden)
    assert dispatch(parser().parse_args(arguments)) == expected
    assert project.read_text() == 'Invalid workspace source'
    with pytest.raises(Refused, match='one release ID or history filters'):
        dispatch(parser().parse_args(arguments + [f'{1:032x}']))


# Execute shipped page/filter ownership handlers with deterministic response races
def test_history_browser_response_ownership():
    import shutil
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is required for history handler checks')
    root = Path(__file__).resolve().parents[1]
    subprocess.run([node, str(root / 'tests/history_ui.cjs')], cwd=root, timeout=15, check=True)
