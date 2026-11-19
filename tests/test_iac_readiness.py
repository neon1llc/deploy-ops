# tests/test_iac_readiness.py - Check known output failures and the actual reviewed backend before apply.


import json
from types import SimpleNamespace

import pytest

from deploy_ops.delivery.adapters import iac
from deploy_ops.delivery.engine import Engine
from deploy_ops.errors import Refused


# Known incompatible outputs should never become an executable saved plan review
@pytest.mark.parametrize('entry,change', [({'sensitive': True}, {}),
    ({'type': ['list', 'string'], 'value': ['private']}, {}),
    ({'type': 'number', 'value': 1.5}, {}), ({'value': None}, {}),
    ({'type': 'string', 'value': 'a' * 2001}, {}), ({}, {'after_sensitive': True})])
def test_known_unusable_output_refuses(entry, change):
    with pytest.raises(Refused, match='Selected infrastructure output'):
        iac.output_readiness({'planned_values': {'outputs': {'selected': entry}},
                              'output_changes': {'selected': change}}, ['selected'])


# Unknown values remain explicit and do not masquerade as a successful public output
@pytest.mark.parametrize('entry,change,expected', [({'type': 'string', 'value': 'ok'}, {}, 'known_supported_scalar'),
    ({'sensitive': False}, {'after_unknown': True}, 'unknown_until_apply'),
    ({}, {'after': None, 'after_unknown': True}, 'unknown_until_apply')])
def test_output_readiness_distinguishes_unknown(entry, change, expected):
    assert iac.output_readiness({'planned_values': {'outputs': {'result': entry}},
                                'output_changes': {'result': change}}, ['result']) == {'result': expected}
    with pytest.raises(Refused, match='not declared'):
        iac.output_readiness({'planned_values': {}}, ['missing'])


# Reuse native-shaped backend metadata while leaving real resources and settings untouched
def native_context(tmp_path, options, kind='local', config=None):
    source = tmp_path / 'source'; source.mkdir()
    data = source / '.terraform'; data.mkdir()
    context = SimpleNamespace(source=source, deployments=tmp_path/'deployed', environment='development',
                              name='foundation', options=options)
    destination = (context.deployments/context.environment/context.name/'terraform.tfstate').resolve()
    value = config or {'path': str(destination), 'workspace_dir': None}
    (data/'terraform.tfstate').write_text(json.dumps({'version':3,'backend':{'type':kind,'config':value}}))
    return context


# A declaration is insufficient when native initialization selected local or a different destination
@pytest.mark.parametrize('mutation', ['declared_remote', 'path', 'workspace', 'missing', 'unsupported'])
def test_effective_backend_refuses_mismatches(tmp_path, mutation):
    context = native_context(tmp_path, {'local_state': True})
    file = context.source/'.terraform/terraform.tfstate'
    if mutation == 'declared_remote': context.options = {'remote_state': True}
    elif mutation == 'path':
        data = json.loads(file.read_text()); data['backend']['config']['path'] = str(tmp_path/'elsewhere'); file.write_text(json.dumps(data))
    elif mutation == 'workspace': (context.source/'.terraform/environment').write_text('other')
    elif mutation == 'missing': file.unlink()
    else:
        data = json.loads(file.read_text()); data['backend']['type'] = 'http'; file.write_text(json.dumps(data))
    with pytest.raises(Refused, match='Effective backend'):
        iac.backend_binding(context)


# Full configuration binding notices a change without exposing raw backend credentials
@pytest.mark.parametrize('kind,config', [('s3', {'bucket':'bucket', 'key':'state', 'region':None}),
    ('gcs', {'bucket':'bucket', 'prefix':''}),
    ('azurerm', {'storage_account_name':'storage', 'container_name':'state', 'key':'state.tfstate'})])
def test_supported_remote_metadata_binding(tmp_path, kind, config):
    context = native_context(tmp_path, {'remote_state':True}, kind, config)
    first = iac.backend_binding(context)
    assert first['type'] == kind and first['workspace'] == 'default'
    data = json.loads((context.source/'.terraform/terraform.tfstate').read_text())
    data['backend']['config']['fixture_token'] = 'not-a-real-token'
    (context.source/'.terraform/terraform.tfstate').write_text(json.dumps(data))
    second = iac.backend_binding(context)
    assert first['config_digest'] != second['config_digest']
    assert 'not-a-real-token' not in str(second)


# Both entry points refuse old generic delivery evidence before claim or native tool use
@pytest.mark.parametrize('adapter', ['terraform', 'opentofu'])
def test_generic_legacy_iac_refuses_before_admission(tmp_path, adapter):
    release = {'id':'old', 'nodes':{'foundation':{}}}
    old = {'preview': {'changes':[{'action':'import'}]}}
    class NoNative:
        component = SimpleNamespace(adapter=adapter)
        def __getattr__(self, key): raise AssertionError('No tool or lease use permitted')
    with pytest.raises(Refused, match='Legacy IaC'):
        iac.apply(NoNative(), old)
    instance = SimpleNamespace(state=tmp_path, _reviewed_envelope=lambda *args:{'adapter':adapter,'prepared':old})
    with pytest.raises(Refused, match='Legacy IaC'):
        Engine._apply(instance, release, 'foundation', {})
