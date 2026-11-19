# tests/test_responder_handoff.py - Keep later evidence, local decisions and explicit responder handoff separate.


import copy
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from deploy_ops.console import create_app
from deploy_ops.delivery import paging
from deploy_ops.delivery.demo import finish
from deploy_ops.delivery.engine import Engine
from deploy_ops.delivery.incidents import handoff, handoff_preview, open_incident, transition
from deploy_ops.delivery.scaffold import create
from deploy_ops.errors import Refused


# A real successful local marker can become drifted later without changing delivery history
@pytest.fixture
def system(tmp_path,monkeypatch):
    engine=Engine(create(tmp_path/'workspace'))
    release=finish(engine,engine.create('development','foundation'))
    before=copy.deepcopy(engine.ledger.get(release['id']))
    clock=[1800000000]
    monkeypatch.setattr('deploy_ops.delivery.incidents.time.time',lambda:clock[0])
    monkeypatch.setenv('DEPLOY_OPS_PAGING_PD','p'*32)
    paging.save_config(engine.ledger,{'destinations':{'page':{'provider':'pagerduty','key_env':'DEPLOY_OPS_PAGING_PD'}},
                                    'default_destinations':['page']},0)
    paging.activate(engine.ledger,1)
    marker=engine.deployments/'development'/'hello'/'resource.json'
    marker.write_text('{"name":"changed","environment":"development"}')
    observation=engine.observe(release['id'])
    assert observation['status']=='drifted'
    return engine,release,observation,before,clock


# Reference the exact saved observation returned by the shared owner
def open_observed(system):
    engine,release,observation,_,_=system
    reference=observation['reference']
    return open_incident(engine.ledger,release['id'],'operations','Investigate later drift',
                         observation_sequence=reference['sequence'],observation_digest=reference['digest'])


# Opening the incident does not page; an exact reviewed handoff creates one queued destination obligation
def test_later_incident_then_explicit_handoff(system):
    engine,release,observation,before,_=system
    incident=open_observed(system)
    assert incident['origin']['digest']==observation['reference']['digest'] and not incident['native_handoff']
    assert paging.status(engine.ledger)['bindings']==[]
    assert open_observed(system)==incident
    preview=handoff_preview(engine.ledger,incident['id'])
    assert preview['available'] and preview['source']=='active'
    assert preview['context']['component']=='foundation'
    assert any(row['reason']=='Incident has not been handed off' for row in preview['prior_routes'][0]['decision']['destinations'])
    queued=handoff(engine.ledger,incident['id'],incident['version'],preview['route_digest'],'Send the reviewed responder route')
    assert queued['version']==2 and queued['native_handoff']
    assert len(paging.status(engine.ledger)['bindings'])==1
    attempts=[]
    def accepted(*args):
        attempts.append(args)
        return {'state':'accepted','reason':'Local fixture acceptance'}
    assert paging.drain(engine.ledger,sender=accepted)['accepted']==1 and len(attempts)==1
    assert not handoff_preview(engine.ledger,incident['id'])['available']
    assert engine.ledger.get(release['id'])==before
    assert attempts[0][1]['event']=='incident.handoff_requested' and attempts[0][1]['version']==2


# A quiet period never turns its old suppressed opening into a catch-up page
def test_quiet_expiry_requires_fresh_reviewed_handoff(system):
    engine,release,_,_,clock=system
    draft=paging.config(engine.ledger)['draft']
    draft['routes']=[{'name':'quiet','events':['incident.opened'],'destinations':['page'],
                      'quiet_until':datetime.fromtimestamp(clock[0]+60,timezone.utc).isoformat()}]
    paging.save_config(engine.ledger,draft,1);paging.activate(engine.ledger,2)
    # Use the existing failed-release path as well as the new later-observation path.
    release['status']='uncertain';engine.ledger.save(release)
    incident=open_incident(engine.ledger,release['id'],'operations','Inspect uncertain work')
    quiet=handoff_preview(engine.ledger,incident['id'])
    assert not quiet['available'] and quiet['decision']['quiet']
    clock[0]+=120
    current=handoff_preview(engine.ledger,incident['id'])
    assert current['available'] and not current['decision']['quiet']
    assert current['prior_routes'][0]['decision']['quiet']
    assert paging.drain(engine.ledger,sender=lambda *args:pytest.fail('No automatic catch-up'))['attempted']==0
    with pytest.raises(Refused,match='route changed'):
        handoff(engine.ledger,incident['id'],1,quiet['route_digest'],'Old quiet preview must not dispatch')
    assert paging.status(engine.ledger)['bindings']==[]
    handoff(engine.ledger,incident['id'],1,current['route_digest'],'Explicit handoff after quiet expiry')
    assert len(paging.status(engine.ledger)['bindings'])==1


# Changed incident decisions or active routing cannot consume an old handoff review
@pytest.mark.parametrize('change',['incident','route'])
def test_changed_handoff_refuses(system,change):
    engine,_,_,_,_=system
    incident=open_observed(system);preview=handoff_preview(engine.ledger,incident['id'])
    if change=='incident':transition(engine.ledger,incident['id'],'resolve',1,'Resolved after reviewing the target')
    else:
        draft=paging.config(engine.ledger)['draft'];draft['destinations']['page']['enabled']=False
        paging.save_config(engine.ledger,draft,1);paging.activate(engine.ledger,2)
    with pytest.raises(Refused,match='changed'):
        handoff(engine.ledger,incident['id'],1,preview['route_digest'],'Stale review must not dispatch')
    assert paging.status(engine.ledger)['bindings']==[]


# Public source references reject missing, cross-release and changed observations without rewriting records
def test_observation_reference_and_single_incident_limit(system):
    engine,release,observation,_,_=system
    reference=observation['reference']
    for identifier,sequence,digest in [(release['id'],reference['sequence'],'f'*64),
        (engine.create('staging','foundation')['id'],reference['sequence'],reference['digest']),
        (release['id'],999999,reference['digest'])]:
        with pytest.raises(Refused):open_incident(engine.ledger,identifier,'operations','Check exact reference',
            observation_sequence=sequence,observation_digest=digest)
    incident=open_observed(system)
    transition(engine.ledger,incident['id'],'resolve',1,'Resolved original observation')
    newer=engine.observe(release['id'])
    with pytest.raises(Refused,match='another source'):
        open_incident(engine.ledger,release['id'],'operations','Do not overwrite historical episode',
                      observation_sequence=newer['reference']['sequence'],observation_digest=newer['reference']['digest'])


# Generic preview identifies the evaluated settings; evaluating an unsaved draft never activates it
def test_preview_source_and_digest(system):
    engine,_,_,_,_=system
    context={'event':'incident.opened'}
    active=paging.preview(engine.ledger,context)
    draft=paging.config(engine.ledger)['draft']
    saved=paging.preview(engine.ledger,context,draft)
    draft['destinations']['page']['enabled']=False
    unsaved=paging.preview(engine.ledger,context,draft)
    assert (active['source'],saved['source'],unsaved['source'])==('active','saved-draft','unsaved-draft')
    assert unsaved['revision'] is None and unsaved['configuration_digest']!=active['configuration_digest']
    assert paging.preview(engine.ledger,context)['configuration_digest']==active['configuration_digest']


# Authenticated API uses the exact same source reference, holds until explicit handoff and preserves success
def test_api_later_incident_reference_and_handoff(system):
    engine,release,observation,before,_=system
    headers={'Authorization':'Bearer '+'x'*43}
    with TestClient(create_app(engine,'x'*43),base_url='http://127.0.0.1:8765') as client:
        detail=client.get('/api/releases/'+release['id'],headers=headers).json()
        record=next(row for row in detail['events'] if row['name']=='release.observed')
        assert record['reference']==observation['reference']
        response=client.post('/api/incidents',headers=headers,json={'release_id':release['id'],'owner':'operations','note':'Later recorded drift',
            'observation_sequence':record['reference']['sequence'],'observation_digest':record['reference']['digest']})
        assert response.status_code==200,response.text
        incident=response.json();assert paging.status(engine.ledger)['bindings']==[]
        preview=client.get('/api/incidents/'+incident['id']+'/handoff',headers=headers).json()
        response=client.post('/api/incidents/'+incident['id']+'/handoff',headers=headers,json={
            'version':incident['version'],'route_digest':preview['route_digest'],'note':'Explicit reviewed handoff'})
        assert response.status_code==200,response.text
        assert len(paging.status(engine.ledger)['bindings'])==1
    assert engine.ledger.get(release['id'])==before
