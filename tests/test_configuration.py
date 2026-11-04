# tests/test_configuration.py - Preserve authored drafts and reject conflicting configuration saves.


import json
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from deploy_ops.cli import dispatch, parser
from deploy_ops.console import create_app
from deploy_ops.delivery.configuration import add, inspect, patch, source_hash
from deploy_ops.delivery.engine import Engine
from deploy_ops.delivery.scaffold import create
from deploy_ops.errors import Refused


# Keep configuration edits inside a fresh disposable workspace
@pytest.fixture
def workspace(tmp_path):
    return create(tmp_path / 'workspace')


# Field edits preserve authored content and leave unchanged defaults absent
def test_form_patch_preserves_comments_advanced_settings_and_default_omissions(workspace):
    source = '# Keep this header\n' + workspace.read_text()
    result = patch(source, 'components', 'web', {'timeout_seconds': 900})
    assert result['toml'].startswith('# Keep this header\n')
    before = inspect(source)['configuration']['components']['web']
    after = result['configuration']['components']['web']
    assert before | {'timeout_seconds': 900} == after
    for line in source.splitlines():
        assert line in result['toml']
    unchanged = patch(source, 'components', 'web', {key: before[key] for key in inspect(source)['forms']['components']})
    assert unchanged['toml'] == source
    assert unchanged['diff'] == ''


# Reject malformed edits before modifying the saved workspace
@pytest.mark.parametrize('section,name,values', [
    ('components', 'web', {'needs': ['web']}),
    ('components', 'foundation', {'needs': ['web']}),
    ('components', 'web', {'timeout_seconds': True}),
    ('components', 'web', {'timeout_seconds': 1}),
    ('components', 'web', {'adapter': 'unknown'}),
    ('components', 'web', {'repository': 'missing'}),
    ('components', 'web', {'options': {'role_arn': 'writer'}}),
    ('environments', 'development', {'production': 'false'}),
    ('environments', 'development', {'provider': 'unknown'}),
    ('environments', 'development', {'promote_from': ['missing']}),
    ('repositories', 'application', {'revision': '--upload-pack=evil'}),
    ('components', 'missing', {'path': '.'}),
    ('targets', 'web', {'policy': {}}),
])
def test_invalid_guided_changes_are_refused(workspace, section, name, values):
    source = workspace.read_text()
    with pytest.raises(Refused):
        patch(source, section, name, values)
    assert workspace.read_text() == source


# CLI and console share one draft transformation without implicit saves
def test_cli_and_api_produce_same_draft_without_saving(workspace):
    source = workspace.read_text()
    values = {'timeout_seconds': 900}
    cli = dispatch(parser().parse_args(['--project', str(workspace), 'config', 'patch', '--section', 'components',
                                      '--name', 'web', '--values', json.dumps(values)]))
    engine = Engine(workspace)
    client = TestClient(create_app(engine, 't' * 40), base_url='http://127.0.0.1:8765')
    body = {'configuration': source, 'section': 'components', 'name': 'web', 'values': values}
    assert client.post('/api/config/patch', json=body).status_code == 401
    response = client.post('/api/config/patch', json=body, headers={'Authorization': 'Bearer ' + 't' * 40})
    assert response.status_code == 200
    assert response.json() == cli
    assert workspace.read_text() == source


# A stale browser cannot replace a newer authored configuration
def test_stale_tab_and_external_editor_cannot_overwrite_configuration(workspace):
    engine = Engine(workspace)
    client = TestClient(create_app(engine, 't' * 40), base_url='http://127.0.0.1:8765')
    headers = {'Authorization': 'Bearer ' + 't' * 40}
    original = workspace.read_text()
    changed = patch(original, 'components', 'web', {'timeout_seconds': 900})['toml']
    body = {'configuration': changed, 'source_hash': source_hash(original)}
    assert client.post('/api/config/save', json=body, headers=headers).status_code == 200
    backup = engine.state / 'previous-workspace.toml'
    assert backup.read_text() == original
    assert client.post('/api/config/save', json=body, headers=headers).status_code == 409
    assert workspace.read_text() == changed
    assert backup.read_text() == original
    external = changed.replace('timeout_seconds = 900', 'timeout_seconds = 901')
    workspace.write_text(external)
    current = client.get('/api/workspace', headers=headers).json()
    assert current['source_hash'] == source_hash(external)
    assert current['configuration']['components']['web']['timeout_seconds'] == 901
    assert client.post('/api/releases', json={'environment': 'development'}, headers=headers).status_code == 409
    assert client.post('/api/config/save', json={'configuration': changed, 'source_hash': source_hash(changed)}, headers=headers).status_code == 409
    assert workspace.read_text() == external


# Concurrent release creation blocks a conflicting configuration save
def test_release_creation_and_save_share_a_boundary(workspace, monkeypatch):
    engine = Engine(workspace)
    client = TestClient(create_app(engine, 't' * 40), base_url='http://127.0.0.1:8765')
    headers = {'Authorization': 'Bearer ' + 't' * 40}
    source = workspace.read_text()
    entered, proceed = threading.Event(), threading.Event()
    original_create = engine.create


    # Hold release creation open to exercise the concurrent save boundary
    def create_release(*args, **kwargs):
        entered.set()
        assert proceed.wait(5)
        return original_create(*args, **kwargs)
    monkeypatch.setattr(engine, 'create', create_release)
    monkeypatch.setattr(engine, 'advance', lambda _: None)
    with ThreadPoolExecutor(max_workers=2) as pool:
        release = pool.submit(client.post, '/api/releases', json={'environment': 'development'}, headers=headers)
        assert entered.wait(5)
        save = pool.submit(client.post, '/api/config/save', json={'configuration': source, 'source_hash': source_hash(source)}, headers=headers)
        proceed.set()
        assert release.result().status_code == 200
        assert save.result().status_code == 409
    assert workspace.read_text() == source


# A remote-worker console cannot rewrite controller authority
def test_remote_console_draft_cannot_save_authority(workspace):
    engine = Engine(workspace, controller='https://controller.example')
    client = TestClient(create_app(engine, 't' * 40), base_url='http://127.0.0.1:8765')
    headers = {'Authorization': 'Bearer ' + 't' * 40}
    source = workspace.read_text()
    body = {'configuration': source, 'source_hash': source_hash(source)}
    assert client.post('/api/config/inspect', json=body, headers=headers).status_code == 200
    assert client.post('/api/config/save', json=body, headers=headers).status_code == 403
    assert workspace.read_text() == source


# Effective provider fields write only the selected environment override
def test_native_options_share_schema_and_preserve_inheritance(tmp_path):
    source = '''# Keep the operator's annotation
name = "native"
[repositories.infrastructure]
path = "infrastructure"
[environments.staging]
provider = "aws"
[environments.production]
provider = "aws"
[components.foundation]
repository = "infrastructure"
kind = "infrastructure"
adapter = "cloudformation"
[components.foundation.options]
account_id = "123456789012"
region = "us-east-1"
stack_name = "example"
template = "main.json"
'''
    before = inspect(source)
    selected = before["option_forms"]["foundation/staging"]
    assert selected["values"]["region"] == "us-east-1"
    result = patch(source, "options", "foundation/staging", {"region": "us-west-2", "parameters": {"Name": "sample"}})
    assert result["toml"].startswith("# Keep the operator's annotation")
    assert result["configuration"]["components"]["foundation"]["options"]["region"] == "us-east-1"
    assert result["option_forms"]["foundation/staging"]["values"]["region"] == "us-west-2"
    assert result["option_forms"]["foundation/production"]["values"]["region"] == "us-east-1"
    path = tmp_path / "deploy-ops.toml"
    path.write_text(source)
    cli = dispatch(parser().parse_args(["--project", str(path), "config", "patch", "--section", "options",
                                      "--name", "foundation/staging", "--values", '{"region":"us-west-2"}']))
    assert cli["option_forms"]["foundation/staging"]["values"]["region"] == "us-west-2"
    with pytest.raises(Refused):
        patch(source, "options", "foundation/staging", {"credentials_file": "/untrusted/key"})
    with pytest.raises(Refused):
        patch(source, "options", "foundation/staging", {"capabilities": ["CAPABILITY_AUTO_EXPAND"]})


# The CLI validates and compares a draft without creating operator state
@pytest.mark.parametrize('action', ['validate', 'diff'])
def test_cli_draft_review_matches_console_and_has_no_state_side_effect(workspace, tmp_path, action):
    source = workspace.read_text()
    draft = tmp_path / 'draft.toml'
    draft.write_text(patch(source, 'components', 'web', {'timeout_seconds': 900})['toml'])
    arguments = ['--project', str(workspace), 'config', action, '--file', str(draft)]
    result = dispatch(parser().parse_args(arguments))
    assert not (workspace.parent / '.deploy-ops').exists()
    assert result['saved_source_hash'] == source_hash(source)
    assert result['source_hash'] == source_hash(draft.read_text())
    assert '+timeout_seconds = 900' in result['diff']
    client = TestClient(create_app(Engine(workspace), 't' * 40), base_url='http://127.0.0.1:8765')
    response = client.post('/api/config/' + action, json={'configuration': draft.read_text()},
                           headers={'Authorization': 'Bearer ' + 't' * 40})
    assert response.json() == result
    assert workspace.read_text() == source


# An explicit CLI save records the exact source, recovery copy and audit event
def test_cli_save_requires_source_identity_and_rejects_a_stale_retry(workspace, tmp_path):
    source = workspace.read_text()
    draft = tmp_path / 'draft.toml'
    draft.write_text(patch(source, 'components', 'web', {'timeout_seconds': 900})['toml'])
    arguments = ['--project', str(workspace), 'config', 'save', '--file', str(draft)]
    with pytest.raises(Refused, match='source-hash'):
        dispatch(parser().parse_args(arguments))
    assert not (workspace.parent / '.deploy-ops').exists()
    arguments += ['--source-hash', source_hash(source)]
    result = dispatch(parser().parse_args(arguments))
    assert result['status'] == 'saved'
    assert result['source_hash'] == source_hash(draft.read_text())
    assert workspace.read_text() == draft.read_text()
    state = workspace.parent / '.deploy-ops'
    backup = state / 'previous-workspace.toml'
    assert backup.read_text() == source
    assert backup.stat().st_mode & 0o777 == 0o600
    engine = Engine(workspace)
    events = [json.loads(row['payload']) for row in engine.ledger.store.audit()]
    event = next(row for row in events if row['event'] == 'workspace.updated')
    assert event['detail'] == {'old_digest': inspect(source)['digest'], 'new_digest': result['digest']}
    with pytest.raises(Refused, match='changed since'):
        dispatch(parser().parse_args(arguments))
    assert backup.read_text() == source


# Missing or invalid input cannot silently validate or overwrite the saved file
@pytest.mark.parametrize('action', ['validate', 'diff', 'save'])
def test_cli_review_requires_an_explicit_draft(workspace, action):
    with pytest.raises(Refused, match='--file'):
        dispatch(parser().parse_args(['--project', str(workspace), 'config', action]))


# A pending recovery artifact remains intact when a new save is refused
def test_pending_file_is_preserved_without_overwriting_backup(workspace):
    from deploy_ops.delivery.configuration import save
    engine = Engine(workspace)
    source = workspace.read_text()
    pending = workspace.with_suffix('.pending')
    pending.write_text('interrupted draft')
    backup = engine.state / 'previous-workspace.toml'
    backup.write_text('earlier backup')
    with pytest.raises(Refused, match='pending configuration'):
        save(engine, source, source_hash(source))
    assert pending.read_text() == 'interrupted draft'
    assert backup.read_text() == 'earlier backup'
    assert workspace.read_text() == source


# Shared locking covers a second CLI process while source capture is still running
def test_cli_save_cannot_cross_release_preparation(workspace, tmp_path, monkeypatch):
    import deploy_ops.delivery.engine as implementation
    source = workspace.read_text()
    draft = tmp_path / 'draft.toml'
    draft.write_text(patch(source, 'components', 'web', {'timeout_seconds': 900})['toml'])
    engine = Engine(workspace)
    entered, proceed = threading.Event(), threading.Event()
    original = implementation.snapshot

    # Hold the real source capture before the release is recorded in the ledger
    def capture(*args):
        entered.set()
        assert proceed.wait(5)
        return original(*args)
    monkeypatch.setattr(implementation, 'snapshot', capture)
    arguments = ['--project', str(workspace), 'config', 'save', '--file', str(draft), '--source-hash', source_hash(source)]
    with ThreadPoolExecutor(max_workers=1) as pool:
        release = pool.submit(engine.create, 'development')
        assert entered.wait(5)
        try:
            response = subprocess.run([sys.executable, '-m', 'deploy_ops.cli', *arguments],
                                      capture_output=True, text=True, timeout=5)
            assert response.returncode == 2
            assert 'preparation is active' in response.stderr
        finally:
            proceed.set()
        assert release.result()['status'] == 'queued'
    with pytest.raises(Refused, match='outstanding releases'):
        dispatch(parser().parse_args(arguments))
    assert workspace.read_text() == source


# An engine loaded before a successful settings save cannot capture the old contract
def test_release_creation_rechecks_saved_configuration(workspace):
    from deploy_ops.delivery.configuration import save
    engine = Engine(workspace)
    source = workspace.read_text()
    revised = patch(source, 'components', 'web', {'timeout_seconds': 900})['toml']
    save(engine, revised, source_hash(source))
    with pytest.raises(Refused, match='workspace changed'):
        engine.create('development')
    assert engine.ledger.list() == []


# An old outstanding release still blocks saving after newer history exceeds the display limit
def test_save_checks_outstanding_work_beyond_recent_history(workspace):
    from deploy_ops.delivery.configuration import save
    engine = Engine(workspace)
    source = workspace.read_text()
    with engine.ledger.store.transaction() as connection:
        for number in range(202):
            status = 'waiting' if number == 0 else 'succeeded'
            release = {'id': str(number), 'environment': 'development', 'status': status}
            connection.execute('INSERT INTO releases VALUES (?, ?, ?, ?)',
                               (str(number), 'development', status, json.dumps(release)))
    assert all(item['status'] == 'succeeded' for item in engine.ledger.list())
    with pytest.raises(Refused, match='outstanding releases'):
        save(engine, source, source_hash(source))
    assert workspace.read_text() == source


# Controller selection never permits a CLI draft to rewrite worker authority
def test_remote_cli_cannot_save_local_workspace(workspace, tmp_path):
    source = workspace.read_text()
    draft = tmp_path / 'draft.toml'
    draft.write_text(source)
    arguments = ['--project', str(workspace), '--controller', 'https://controller.example',
                 'config', 'save', '--file', str(draft), '--source-hash', source_hash(source)]
    with pytest.raises(Refused, match='Only local operators'):
        dispatch(parser().parse_args(arguments))
    assert workspace.read_text() == source


# Every interface adds the same authored entry without saving or creating release work
@pytest.mark.parametrize('section,name,values', [
    ('repositories', 'new-app', {'path': 'new-app'}),
    ('environments', 'local-test', {}),
    ('components', 'new-foundation', {'repository': 'infrastructure', 'kind': 'infrastructure',
        'adapter': 'directory', 'options': {'name': 'separate-site'}}),
    ('components', 'new-site', {'repository': 'application', 'kind': 'application', 'adapter': 'static',
        'needs': ['foundation'], 'build': {'checks': [['python3', 'check.py']], 'command': ['python3', 'build.py']},
        'options': {'directory': 'dist', 'destination': '${outputs.foundation.path}'},
        'targets': {'staging': {'directory': 'other-dist'}}}),
])
def test_add_shared_draft_semantics(workspace, tmp_path, section, name, values):
    source = '# Preserve this authored header\n' + workspace.read_text()
    workspace.write_text(source)
    draft = tmp_path / 'draft.toml'
    arguments = ['--project', str(workspace), 'config', 'add', '--section', section, '--name', name,
                 '--values', json.dumps(values), '--draft-out', str(draft)]
    cli = dispatch(parser().parse_args(arguments))
    assert not (workspace.parent / '.deploy-ops').exists()
    client = TestClient(create_app(Engine(workspace), 't' * 40), base_url='http://127.0.0.1:8765')
    body = {'configuration': source, 'section': section, 'name': name, 'values': values}
    assert client.post('/api/config/add', json=body).status_code == 401
    api = client.post('/api/config/add', json=body, headers={'Authorization': 'Bearer ' + 't' * 40})
    assert api.status_code == 200
    assert api.json() == cli
    assert cli['source_hash'] != cli['saved_source_hash'] == source_hash(source)
    assert draft.read_text() == cli['toml']
    assert cli['toml'].startswith('# Preserve this authored header\n')
    for line in source.splitlines():
        assert line in cli['toml']
    original = inspect(source)['configuration']
    revised = cli['configuration']
    for group in ('repositories', 'environments', 'components'):
        assert all(revised[group][key] == value for key, value in original[group].items())
    assert workspace.read_text() == source
    assert Engine(workspace).ledger.list() == []
    assert 'allow_uncommitted = false' not in cli['toml']
    assert 'production = false' not in cli['toml']


# Invalid additions preserve both the saved source and an already usable output draft
@pytest.mark.parametrize('section,name,values', [
    ('repositories', 'application', {'path': 'elsewhere'}),
    ('repositories', 'BadName', {'path': 'app'}),
    ('repositories', 'new-app', {'path': 'app', 'allow_uncommitted': 'true'}),
    ('repositories', 'new-app', {'path': 'app', 'unknown': True}),
    ('environments', 'new-local', {'provider': 'local', 'production': 'false'}),
    ('components', 'new-site', {'repository': 'missing', 'kind': 'application', 'adapter': 'static'}),
    ('components', 'new-site', {'repository': 'application', 'kind': 'application', 'adapter': 'static', 'needs': ['new-site']}),
    ('components', 'new-site', {'repository': 'application', 'kind': 'application', 'adapter': 'static', 'path': '../escape'}),
    ('components', 'new-site', {'repository': 'application', 'kind': 'application', 'adapter': 'static', 'options': {'directory': '../escape'}}),
    ('components', 'new-site', {'repository': 'application', 'kind': 'application', 'adapter': 'static', 'options': {'unknown': 'value'}}),
    ('components', 'new-site', {'repository': 'application', 'kind': 'application', 'adapter': 'static', 'options': {'destination': '${outputs.foundation.path}'}}),
    ('components', 'new-site', {'repository': 'application', 'kind': 'application', 'adapter': 'static', 'options': {'destination': '${env.missing}'}}),
    ('components', 'new-site', {'repository': 'application', 'kind': 'application', 'adapter': 'static', 'options': {'destination': 'prefix/${outputs.foundation.path}'}}),
    ('components', 'new-site', {'repository': 'application', 'kind': 'application', 'adapter': 'static', 'build': {'command': 'python3 build.py'}}),
    ('components', 'new-site', {'repository': 'application', 'kind': 'application', 'adapter': 'static', 'build': {'command': [1]}}),
    ('components', 'new-site', {'repository': 'application', 'kind': 'application', 'adapter': 'static', 'build': {'unknown': True}}),
    ('components', 'new-site', {'repository': 'application', 'kind': 'application', 'adapter': 'static', 'targets': {'missing': {'directory': 'dist'}}}),
    ('components', 'new-site', {'repository': 'application', 'kind': 'application', 'adapter': 'static', 'targets': {'staging': {'directory': '/escape'}}}),
])
def test_add_refusals_preserve_drafts(workspace, tmp_path, section, name, values):
    source = workspace.read_text()
    draft = tmp_path / 'draft.toml'
    draft.write_text(source)
    arguments = ['--project', str(workspace), 'config', 'add', '--section', section, '--name', name,
                 '--values', json.dumps(values), '--draft-out', str(draft)]
    with pytest.raises(Refused):
        dispatch(parser().parse_args(arguments))
    client = TestClient(create_app(Engine(workspace), 't' * 40), base_url='http://127.0.0.1:8765')
    response = client.post('/api/config/add', json={'configuration': source, 'section': section, 'name': name, 'values': values},
                           headers={'Authorization': 'Bearer ' + 't' * 40})
    assert response.status_code == 422
    assert workspace.read_text() == draft.read_text() == source


# Human creation flags preserve typed arguments and cannot act as an implicit workspace save
def test_add_flags_and_output_boundary(workspace, tmp_path):
    source = workspace.read_text()
    arguments = ['--project', str(workspace), 'config', 'add', '--section', 'components', '--name', 'another-site',
        '--repository', 'application', '--kind', 'application', '--adapter', 'static', '--need', 'foundation',
        '--artifact-directory', 'dist', '--destination', '${outputs.foundation.path}',
        '--build-command', '["python3","build.py"]', '--check', '["python3","check.py"]']
    result = dispatch(parser().parse_args(arguments))
    component = result['configuration']['components']['another-site']
    assert component['build']['command'] == ['python3', 'build.py']
    assert component['build']['checks'] == [['python3', 'check.py']]
    assert component['options'] == {'directory': 'dist', 'destination': '${outputs.foundation.path}'}
    alias = tmp_path / 'saved-alias.toml'
    alias.hardlink_to(workspace)
    for path in (workspace, alias):
        with pytest.raises(Refused, match='different file'):
            dispatch(parser().parse_args(arguments + ['--draft-out', str(path)]))
    assert workspace.read_text() == source
