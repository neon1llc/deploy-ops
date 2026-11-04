# tests/test_configuration.py - Preserve authored drafts and reject conflicting configuration saves.


import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from deploy_ops.cli import dispatch, parser
from deploy_ops.console import create_app
from deploy_ops.delivery.configuration import inspect, patch, source_hash
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
