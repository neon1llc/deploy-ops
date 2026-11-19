# tests/test_runner_inputs.py - Refuse unreviewed automatic root inputs without reading or deleting them.


import os
import subprocess
from pathlib import Path

import pytest

from deploy_ops.errors import Refused
from deploy_ops import runner


# Keep real Git provenance isolated from host configuration and credential helpers
@pytest.fixture
def checkout(tmp_path, monkeypatch):
    for key in list(os.environ):
        if key.startswith('GIT_'):
            monkeypatch.delenv(key)
    monkeypatch.setenv('GIT_CONFIG_NOSYSTEM', '1')
    monkeypatch.setenv('GIT_CONFIG_GLOBAL', os.devnull)
    root = tmp_path / 'repository'
    root.mkdir()
    (root / 'main.tf').write_text('output "reviewed" { value = "reviewed" }\n')
    (root / '.gitignore').write_text('*.auto.tfvars*\noverride.tf\n*.tofu*\nterraform.tfvars*\n')
    subprocess.run(['git', 'init', '--initial-branch=main'], cwd=root, check=True, capture_output=True)
    subprocess.run(['git', 'add', '.'], cwd=root, check=True, capture_output=True)
    subprocess.run(['git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                    '-c', 'core.hooksPath=/dev/null', 'commit', '-m', 'Reviewed fixture'],
                   cwd=root, check=True, capture_output=True)
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
    return root, commit


# Git ignores do not exempt automatically loaded native configuration or variable values
@pytest.mark.parametrize('engine,name', [
    ('terraform', 'override.tf'), ('terraform', 'untracked.tf.json'),
    ('terraform', 'private.auto.tfvars'), ('terraform', 'private.auto.tfvars.json'),
    ('terraform', 'terraform.tfvars'), ('terraform', 'terraform.tfvars.json'),
    ('opentofu', 'override.tofu'), ('opentofu', 'override.tofu.json'),
    ('terraform', '.terraform.lock.hcl'),
])
def test_automatic_unreviewed_input_refuses_without_exposing_or_changing_it(checkout, engine, name):
    root, commit = checkout
    extra = root / name
    extra.write_text('synthetic-private-value')
    before = extra.stat()
    with pytest.raises(Refused, match='not all in the approved commit') as error:
        runner.workdir(root, '.', commit, engine)
    assert name not in str(error.value) and 'synthetic-private-value' not in str(error.value)
    assert extra.read_text() == 'synthetic-private-value' and extra.stat().st_mtime_ns == before.st_mtime_ns


# Harmless output and explicit nonautomatic variable files do not become a blanket dirty-tree ban
@pytest.mark.parametrize('engine', ['terraform', 'opentofu'])
def test_unrelated_untracked_files_and_tool_cache_remain_usable(checkout, engine):
    root, commit = checkout
    (root / 'notes.txt').write_text('operator work')
    (root / 'manual.tfvars').write_text('not automatically loaded')
    (root / '.terraform/modules/downloaded').mkdir(parents=True)
    (root / '.terraform/modules/downloaded/main.tf').write_text('downloaded native module')
    assert runner.workdir(root, '.', commit, engine) == root
    assert (root / 'notes.txt').read_text() == 'operator work'


# A committed root symlink does not attest the bytes of its external target
def test_tracked_native_input_symlink_refuses(checkout):
    root, commit = checkout
    external = root.parent / 'external'
    external.write_text('synthetic-private-value')
    (root / 'linked.tf').symlink_to(external)
    subprocess.run(['git', 'add', 'linked.tf'], cwd=root, check=True, capture_output=True)
    subprocess.run(['git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                    '-c', 'core.hooksPath=/dev/null', 'commit', '-m', 'Recorded symlink fixture'],
                   cwd=root, check=True, capture_output=True)
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
    with pytest.raises(Refused, match='ordinary reviewed files'):
        runner.workdir(root, '.', commit, 'terraform')
    assert external.read_text() == 'synthetic-private-value'


# Refusal happens before native tools, output creation or credential acquisition
def test_planning_refuses_before_native_or_credentials(checkout, monkeypatch):
    from deploy_ops.models import Caller
    root, commit = checkout
    (root / 'override.tf').write_text('synthetic-private-value')

    # Return only the planning context; every later operation is forbidden for this case
    class Client:
        def call(self, method, path):
            assert method == 'GET' and path == '/v1/targets/example/plan-context'
            return {'engines': ['terraform'], 'workdir': '.', 'commit': commit}

    def forbidden(*args, **kwargs):
        pytest.fail('Refused directory reached a native tool or credential boundary')

    monkeypatch.setattr(runner, 'executable', forbidden)
    monkeypatch.setattr(runner, 'environment', forbidden)
    output = root.parent / 'output'
    with pytest.raises(Refused, match='not all in the approved commit'):
        runner.plan(Client(), 'example', 'terraform', root, output, Caller())
    assert not output.exists()
