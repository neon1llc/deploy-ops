# tests/test_kubernetes_identity.py - Keep native patches on the reviewed connection and existing object through interruption.


import copy
import json
from types import SimpleNamespace

import pytest

from deploy_ops.delivery.adapters import cloud, kubernetes as kube
from deploy_ops.delivery.adapters.base import Context
from deploy_ops.delivery.engine import Engine
from deploy_ops.delivery.spec import Component
from deploy_ops.errors import Refused


IMAGE = 'registry.example/app@sha256:' + 'a' * 64


# Emulate independent file aliases and cluster objects, including native JSON Patch test semantics
class Native:
    def __init__(self):
        self.config = {'apiVersion':'v1','kind':'Config','current-context':'test',
                       'contexts':[{'name':'test','context':{'cluster':'cluster','user':'scoped'}}],
                       'clusters':[{'name':'cluster','cluster':{'server':'https://cluster.invalid'}}],
                       'users':[{'name':'scoped','user':{'token':'not-a-real-test-token'}}]}
        self.object = {'apiVersion':'apps/v1','kind':'Deployment',
                       'metadata':{'name':'app','namespace':'app','uid':'original-uid','resourceVersion':'123','generation':1},
                       'spec':{'replicas':1,'template':{'spec':{'containers':[{'name':'web','image':'old'}]}}},
                       'status':{'observedGeneration':1,'updatedReplicas':1,'availableReplicas':1}}
        self.calls = []; self.race = None; self.lose_ack = False; self.replace_after_patch = False
        self.heartbeat = lambda: None

    # Never let a workload command fall back to the mutable config file
    def run(self, argv, cwd, timeout, capture, input_text):
        self.calls.append((argv, input_text))
        if argv[1] == 'version': return json.dumps({'clientVersion':{'gitVersion':'v1.36.1'}})
        if 'config' in argv: return json.dumps(self.config)
        assert argv[1] == '--kubeconfig=/dev/stdin' and capture and input_text
        selected = json.loads(input_text)
        assert selected['clusters'][0]['cluster']['server'] == 'https://cluster.invalid'
        if 'get' in argv:
            result = copy.deepcopy(self.object)
            if self.race:
                mutation, self.race = self.race, None
                mutation()
            return json.dumps(result)
        if 'patch' in argv:
            patch = json.loads(argv[argv.index('--patch')+1])
            expected = {'/metadata/uid':self.object['metadata']['uid'],
                        '/metadata/resourceVersion':self.object['metadata']['resourceVersion'],
                        '/spec/template/spec/containers/0/name':self.object['spec']['template']['spec']['containers'][0]['name'],
                        '/spec/template/spec/containers/0/image':self.object['spec']['template']['spec']['containers'][0]['image']}
            assert len(patch)==5
            if any(item['value'] != expected[item['path']] for item in patch[:-1]):
                raise Refused('Native JSON Patch test failed')
            self.object['spec']['template']['spec']['containers'][0]['image'] = patch[-1]['value']
            self.object['metadata'].update(resourceVersion='124',generation=2)
            self.object['status']['observedGeneration']=2
            if self.replace_after_patch:self.object['metadata']['uid']='replacement-uid'
            if self.lose_ack:raise Refused('Acknowledgement lost after patch')
            return 'patched'
        assert 'rollout' in argv
        return 'successfully rolled out'


# Keep credentials in a native-shaped fixture rather than product state or host configuration
@pytest.fixture
def context(tmp_path):
    stage=tmp_path/'artifacts';stage.mkdir()
    return Context(Component(repository='app',kind='application',adapter='kubernetes'), 'app','development','local',
                   tmp_path,stage,tmp_path/'deployed',{'context':'test','namespace':'app','deployment':'app',
                   'container':'web','image':IMAGE},Native(),'release')


# The shared cloud entry points retain exact native identity and public-only recovery facts
def test_pinned_connection_uid_and_result(context):
    prepared=cloud.prepare(context)
    assert prepared['kubernetes_request']['baseline']['uid']=='original-uid'
    assert 'not-a-real-test-token' not in json.dumps(prepared)
    result=cloud.apply(context,prepared)
    assert result=={'deployment':'deployment/app','image':IMAGE,'uid':'original-uid','no_op':False}
    observed=cloud.inspect(context,prepared)
    assert observed['observation']['status']=='converged' and observed['replay_allowed'] is False
    journal=(context.stage.parent/'kubernetes-operation.json').read_text()
    assert 'not-a-real-test-token' not in journal
    assert not list(context.stage.glob('*kubeconfig*'))
    with pytest.raises(Refused,match='already attempted'):cloud.apply(context,prepared)


# Alias edits after the operation snapshot cannot redirect the native mutation or following readback
def test_alias_change_during_operation_stays_pinned(context):
    prepared=cloud.prepare(context)
    context.process.race=lambda: context.process.config['clusters'][0]['cluster'].update(server='https://other.invalid')
    assert cloud.apply(context,prepared)['uid']=='original-uid'
    before=len(context.process.calls)
    observed=cloud.inspect(context,prepared)
    assert observed['status']=='connection-changed' and not observed['cluster_read_sent']
    assert not any('get' in argv for argv,_ in context.process.calls[before:])


# Changed identity before submission refuses without a mutation or another-cluster read
@pytest.mark.parametrize('change',['connection','uid','version','container','tls'])
def test_changed_review_refuses_before_patch(context,change):
    prepared=cloud.prepare(context)
    if change=='connection':context.process.config['clusters'][0]['cluster']['server']='https://other.invalid'
    elif change=='uid':context.process.object['metadata']['uid']='replacement-uid'
    elif change=='version':context.process.object['metadata']['resourceVersion']='999'
    elif change=='container':context.process.object['spec']['template']['spec']['containers'][0]['name']='other'
    else:context.process.config['clusters'][0]['cluster']['insecure-skip-tls-verify']=True
    with pytest.raises(Refused):cloud.apply(context,prepared)
    assert not any('patch' in argv for argv,_ in context.process.calls)
    assert not (context.stage.parent/'kubernetes-operation.json').exists()


# An object recreated after read must fail the native UID test even if version/image collide
def test_uid_race_fails_atomic_test(context):
    prepared=cloud.prepare(context)
    context.process.race=lambda: context.process.object['metadata'].update(uid='replacement-uid')
    with pytest.raises(Refused,match='JSON Patch test'):cloud.apply(context,prepared)
    assert context.process.object['spec']['template']['spec']['containers'][0]['image']=='old'
    assert json.loads((context.stage.parent/'kubernetes-operation.json').read_text())['status']=='patch-submitted'


# A lost response preserves observation and refuses resend rather than fabricating controller success
def test_lost_ack_observation_without_replay(context):
    prepared=cloud.prepare(context);context.process.lose_ack=True
    with pytest.raises(Refused,match='Acknowledgement lost'):cloud.apply(context,prepared)
    observed=cloud.inspect(context,prepared)
    assert observed['operation']=='patch-submitted' and observed['observation']['status']=='converged'
    with pytest.raises(Refused,match='already attempted'):cloud.apply(context,prepared)
    assert sum('patch' in argv for argv,_ in context.process.calls)==1


# Final readback cannot claim success for a replacement object carrying the desired image
def test_replaced_object_not_success(context):
    prepared=cloud.prepare(context);context.process.replace_after_patch=True
    with pytest.raises(Refused,match='did not converge'):cloud.apply(context,prepared)
    observed=cloud.inspect(context,prepared)
    assert observed['observation']['status']=='object-replaced'


# Same-image readiness is observed without a patch and remains bound to the original object
def test_no_op_does_not_patch(context):
    context.process.object['spec']['template']['spec']['containers'][0]['image']=IMAGE
    prepared=cloud.prepare(context)
    assert prepared['preview']['changes'][0]['action']=='no-op'
    assert cloud.apply(context,prepared)['no_op']
    assert not any('patch' in argv for argv,_ in context.process.calls)


# Legacy admission stops before any native command or execution lease
def test_legacy_refusal_before_native_and_claim(tmp_path):
    class NoNative:
        component=SimpleNamespace(adapter='kubernetes')
        def __getattr__(self,key):raise AssertionError('No native/lease access permitted')
    with pytest.raises(Refused,match='Legacy Kubernetes'):cloud.apply(NoNative(),{'image':IMAGE})
    instance=SimpleNamespace(state=tmp_path,_reviewed_envelope=lambda *args:{'adapter':'kubernetes','prepared':{'image':IMAGE}})
    with pytest.raises(Refused,match='Legacy Kubernetes'):
        Engine._apply(instance,{'id':'old','nodes':{'app':{}}},'app',{})


# Existing deployment inspection cannot silently label the new native journal as not applicable
def test_inspection_declines_unrepresented_operation(tmp_path,monkeypatch):
    from deploy_ops.delivery import evidence
    release={'nodes':{'app':{'adapter':'kubernetes','kubernetes_binding':{'uid':'original-uid'}}}}
    monkeypatch.setattr(evidence.Ledger,'open_existing',lambda path:SimpleNamespace(get=lambda identifier:release))
    with pytest.raises(Refused,match='does not describe the Kubernetes'):
        evidence.capture(tmp_path,'a'*32)


# Coherent backup admits the public journal but excludes the native credential home
def test_backup_extent_keeps_public_journal_only():
    from deploy_ops.delivery.backup import allowed
    prefix='state/releases/'+'a'*32+'/app/'
    assert allowed(prefix+'kubernetes-operation.json')
    assert not allowed(prefix+'home/private-kubeconfig')


# Malformed native journal data must not start another command or follow an indirect credential path
def test_invalid_journal_refuses_without_native(context):
    prepared=cloud.prepare(context)
    (context.stage.parent/'kubernetes-operation.json').write_text('[]')
    calls=len(context.process.calls)
    with pytest.raises(Refused,match='malformed'):cloud.inspect(context,prepared)
    assert len(context.process.calls)==calls


# Current console readback explains legacy compatibility without consuming or rewriting an approval
@pytest.mark.parametrize('adapter,prepared,reason', [
    ('kubernetes', {'image':IMAGE}, 'Legacy Kubernetes'),
    ('terraform', {'preview':{'changes':[]}}, 'Legacy IaC'),
    ('cloudformation', {'operation':'destroy'}, 'Legacy stack deletion')])
def test_console_projects_shared_compatibility(tmp_path,monkeypatch,adapter,prepared,reason):
    from fastapi.testclient import TestClient
    from deploy_ops.console import create_app
    from deploy_ops.delivery.scaffold import create
    engine=Engine(create(tmp_path/'workspace'))
    release=engine.advance(engine.create('development','foundation')['id'])
    before=copy.deepcopy(engine.ledger.get(release['id']))
    audit=engine.ledger.store.audit()
    monkeypatch.setattr(engine,'_reviewed_envelope',lambda *args:{'adapter':adapter,'prepared':prepared})
    with TestClient(create_app(engine,'x'*43),base_url='http://127.0.0.1:8765') as client:
        response=client.get('/api/releases/'+release['id'],headers={'Authorization':'Bearer '+'x'*43})
        assert response.status_code==200
        status=response.json()['request_statuses']['foundation']
        assert status['status']=='pending' and reason in status['execution_blocker']
    assert engine.ledger.get(release['id'])==before and engine.ledger.store.audit()==audit
