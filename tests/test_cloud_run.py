# tests/test_cloud_run.py - Reject replay and false convergence across both native Cloud Run mutations.


import copy
import json
from types import SimpleNamespace

import httpx
import pytest

from cloud_run_fixture import CloudRunFixture
from deploy_ops.delivery.adapters import cloud, cloud_run
from deploy_ops.delivery.adapters.base import Context
from deploy_ops.delivery.spec import Component
from deploy_ops.errors import Refused

IMAGE = 'registry.example/app@sha256:' + 'a' * 64


# Use the real adapter/context with a native-state fixture, never real cloud credentials
@pytest.fixture
def scene(tmp_path, monkeypatch):
    stage = tmp_path / 'artifacts'; stage.mkdir()
    process = SimpleNamespace(heartbeat=lambda: None, env={}, secrets=[], deadline=None)
    context = Context(Component(repository='app', kind='application', adapter='cloud-run'), 'web', 'development',
        'gcp', tmp_path, stage, tmp_path / 'deployments',
        {'name': 'web', 'project_id': 'fixture-project', 'region': 'us-central1', 'image': IMAGE}, process, 'a' * 32)
    native = CloudRunFixture()
    monkeypatch.setattr(cloud_run, 'request', native.request)
    return context, native


# Observe actual revision image and native operation completion before the exact traffic switch
@pytest.mark.parametrize("parent", ["qualified", "short"])
@pytest.mark.parametrize("encoding", ["omitted-default", "explicit-default"])
def test_two_conditional_boundaries_and_actual_readback(scene, parent, encoding, monkeypatch):
    context, native = scene
    if encoding == 'explicit-default':
        native.service(context)['reconciling'] = False
        original_request = native.request
        def explicit(context, method, name, body=None, mask=None):
            value = original_request(context, method, name, body, mask)
            if method == 'GET' and '/revisions/' in name:value['reconciling'] = False
            return value
        native.request = explicit
        monkeypatch.setattr(cloud_run, "request", explicit)
    if parent == 'short':
        original = native.revision
        def short_revision(service, name, containers):
            value = original(service, name, containers); value['service'] = service.rsplit('/', 1)[1]; return value
        native.revision = short_revision
    prepared = cloud.prepare(context)
    assert 'PRIVATE-TEMPLATE-SENTINEL' not in json.dumps(prepared)
    review = cloud_run.review(context, prepared)
    assert review['candidate_revision'] == prepared['cloud_run_request']['candidate']
    assert review['baseline_revision'] == prepared['previous_revision'] and review['traffic_percent'] == 100
    assert review['native_request_digest'] == prepared['request_digest']
    assert 'PRIVATE' not in json.dumps(review)
    changed = copy.deepcopy(prepared)
    changed['cloud_run_request']['image'] = 'other@sha256:' + 'd' * 64
    with pytest.raises(Refused, match='supported reviewed'):cloud_run.review(context, changed)
    result = cloud.apply(context, prepared)
    assert result['image'] == IMAGE and result['revision'] == prepared['cloud_run_request']['candidate']
    writes = [call for call in native.calls if call[0] == 'PATCH']
    assert len(writes) == 2 and writes[0][2]['etag'] != writes[1][2]['etag']
    assert writes[0][2]['traffic'][0]['revision'] == 'web-old'
    assert writes[1][2]['traffic'][0]['revision'] == result['revision'].rsplit('/', 1)[1]
    record = json.loads((context.stage.parent / cloud_run.JOURNAL).read_text())
    assert record['status'] == 'converged' and record['candidate_operation'] != record['traffic_operation']
    assert 'PRIVATE-TEMPLATE-SENTINEL' not in json.dumps(record)
    assert cloud.inspect(context, prepared)['status'] == 'succeeded'
    with pytest.raises(Refused, match='already attempted'):
        cloud.apply(context, prepared)
    assert len([call for call in native.calls if call[0] == 'PATCH']) == 2


# A lost acknowledgement leaves durable intent and never resubmits either native write
@pytest.mark.parametrize('stage', ['candidate', 'traffic'])
def test_acceptance_without_acknowledgement_is_inspectable_without_replay(scene, stage):
    context, native = scene
    prepared = cloud.prepare(context)
    def cut(context, mask):
        if (mask == 'traffic') == (stage == 'traffic'):
            raise Refused('response lost', 503)
    native.after_write = cut
    with pytest.raises(Refused, match='response lost'):
        cloud.apply(context, prepared)
    native.after_write = None
    observed = cloud.inspect(context, prepared)
    assert observed['operation'] == ('traffic-submitted' if stage == 'traffic' else 'candidate-submitted')
    assert observed['status'] == ('succeeded' if stage == 'traffic' else 'baseline-serving')
    count = len([call for call in native.calls if call[0] == 'PATCH'])
    with pytest.raises(Refused, match='already attempted'):
        cloud.apply(context, prepared)
    assert len([call for call in native.calls if call[0] == 'PATCH']) == count


# A healthy predecessor after native rollback never proves delivery of the requested image
@pytest.mark.parametrize('change', ['image', 'route', 'uid', 'generation', 'template'])
def test_changed_candidate_or_target_blocks_traffic(scene, change):
    context, native = scene
    prepared = cloud.prepare(context)
    def change_after_acceptance(context, mask):
        if mask == 'traffic':return
        service = native.service(context)
        candidate = prepared['cloud_run_request']['candidate']
        if change == 'image':native.revisions[candidate]['containers'][0]['image'] = 'other@sha256:' + 'c' * 64
        if change == 'route':service['traffic'] = service['trafficStatuses'] = [{'type':cloud_run.REVISION,'revision':'foreign','percent':100}]
        if change == 'uid':service['uid'] = 'different-physical-service'
        if change == 'generation':service['observedGeneration'] = '0'
        if change == 'template':service['template']['containers'][0]['env'][0]['value'] = 'external-edit'
    native.after_write = change_after_acceptance
    with pytest.raises(Refused):cloud.apply(context, prepared)
    assert len([call for call in native.calls if call[0] == 'PATCH']) == 1
    assert cloud.inspect(context, prepared)['status'] != 'succeeded'


# Only a provider-observed same image permits a non-mutating no-op
def test_same_image_no_op_observes_revision_without_write(scene):
    context, native = scene
    service = native.service(context)
    native.revisions[service['latestReadyRevision']]['containers'][0]['image'] = IMAGE
    service['template']['containers'][0]['image'] = IMAGE
    prepared = cloud.prepare(context)
    assert prepared['preview']['changes'][0]['action'] == 'no-op'
    assert cloud.apply(context, prepared)['no_op']
    assert not any(call[0] == 'PATCH' for call in native.calls)


# Legacy, missing and truncated records cannot authorize a new phase or fabricate no submission
@pytest.mark.parametrize('record', [None, {}, {'status':'candidate-submitted'}])
def test_missing_or_incomplete_operation_is_unknown(scene, record):
    context, native = scene
    if record is not None:(context.stage.parent / cloud_run.JOURNAL).write_text(json.dumps(record))
    with pytest.raises(Refused, match='unknown'):cloud.inspect_pending(context)
    with pytest.raises(Refused, match='supported reviewed'):cloud.apply(context, {'image':IMAGE})
    assert not native.calls


# Etag conflict at the second dispatch retains intent without overwriting the external writer
def test_conditional_traffic_write_rejects_last_moment_external_writer(scene):
    context, native = scene
    prepared = cloud.prepare(context)
    def concurrent(context, method, name, body, mask):
        if method == 'PATCH' and mask == 'traffic':native.service(context)['etag'] = 'foreign-etag'
    native.before_call = concurrent
    with pytest.raises(Refused, match='etag conflict'):cloud.apply(context, prepared)
    assert cloud_run.load(context)['status'] == 'traffic-submitted'
    assert native.service(context)['traffic'][0]['revision'] == 'web-old'


# The fixed-origin transport captures authentication privately and never retries a failed write
def test_fixed_origin_transport_does_not_follow_or_repeat(monkeypatch, tmp_path):
    process = SimpleNamespace(env={'GOOGLE_OAUTH_ACCESS_TOKEN':'PRIVATE-TOKEN-SENTINEL'}, secrets=[], deadline=None, heartbeat=lambda:None)
    context = SimpleNamespace(options={'project_id':'fixture-project','region':'us-central1','name':'web'},process=process,
                              release_id='a'*32,name='web',environment='development')
    calls=[];real_client=httpx.Client
    def handler(request):
        calls.append(request)
        assert request.url.host == 'run.googleapis.com' and request.headers['authorization']=='Bearer PRIVATE-TOKEN-SENTINEL'
        return httpx.Response(409,json={'error':{'message':'PRIVATE-ERROR-SENTINEL'}})
    def client(**kwargs):
        assert kwargs['trust_env'] is False and kwargs['follow_redirects'] is False
        return real_client(transport=httpx.MockTransport(handler),**kwargs)
    monkeypatch.setattr(cloud_run.httpx,'Client',client)
    with pytest.raises(Refused) as error:cloud_run.request(context,'PATCH',cloud_run.scope(context)['service'],{},'traffic')
    assert error.value.status==409 and 'PRIVATE' not in str(error.value) and len(calls)==1
    assert process.secrets==['PRIVATE-TOKEN-SENTINEL']
    with pytest.raises(Refused):cloud_run.request(context,'GET','https://foreign.example/path')
    assert len(calls)==1
