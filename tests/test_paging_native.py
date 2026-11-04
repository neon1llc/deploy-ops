# tests/test_paging_native.py - Verify alert-only and remote-ID paging lifecycles without external sends.


import json

import pytest
from fastapi.testclient import TestClient

from deploy_ops.console import create_app
from deploy_ops.delivery import paging, paging_providers
from deploy_ops.delivery.engine import Engine
from deploy_ops.delivery.incidents import open_incident
from deploy_ops.delivery.paging_config import Destination
from deploy_ops.delivery.scaffold import create
from deploy_ops.errors import Refused


# Use synthetic references and existing provider routes without creating accounts or policies
INCIDENT = {'provider': 'incident.io', 'key_env': 'DEPLOY_OPS_PAGING_HTTP', 'alert_source_id': '01EXAMPLE'}
BETTER = {'provider': 'better-stack', 'key_env': 'DEPLOY_OPS_PAGING_BETTER',
          'requester_email': 'operator@example.com', 'policy_id': '123'}
PAYLOAD = {'event': 'incident.opened', 'release_id': 'fixture-release', 'severity': 'error'}


# Build the minimal supported JSON:API response while testing discard of unrelated private fields
def incident_response(identifier='42', state='Started'):
    return json.dumps({'data': {'id': identifier, 'type': 'incident', 'attributes': {'status': state}},
                       'included': [{'auth_password': 'MUST-NOT-RETAIN'}]}).encode()


# Activate a real incident route through the shared ledger and replace only its network boundary
@pytest.fixture
def native_system(tmp_path, monkeypatch):
    monkeypatch.setenv('DEPLOY_OPS_PAGING_HTTP', 'http-source-fixture')
    monkeypatch.setenv('DEPLOY_OPS_PAGING_BETTER', 'better-fixture')
    engine = Engine(create(tmp_path / 'workspace'))
    paging.save_config(engine.ledger, {'destinations': {'alerts': INCIDENT, 'responders': BETTER},
                                      'default_destinations': ['alerts', 'responders']}, 0)
    paging.activate(engine.ledger, 1)
    release = engine.create('development')
    release['status'] = 'uncertain'
    engine.ledger.save(release, 'release.uncertain')
    incident = open_incident(engine.ledger, release['id'], 'operations', 'Inspect fixture deployment')
    calls = []

    # Return exact vendor shapes and record HTTP methods without opening a socket
    def transport(url, body, headers, networks, method='POST'):
        data = json.loads(body) if body else None
        calls.append((url, data, method))
        assert networks == [] and headers['Authorization'].startswith('Bearer ')
        if 'api.incident.io/' in url:
            return 202, {}, json.dumps({'status': 'success', 'message': 'Accepted',
                                       'deduplication_key': data['deduplication_key']}).encode()
        state = 'Acknowledged' if url.endswith('/acknowledge') else 'Resolved' if url.endswith('/resolve') else 'Started'
        return (201 if url.endswith('/incidents') else 200), {}, incident_response(state=state)

    monkeypatch.setattr(paging_providers, 'request', transport)
    return engine, incident, calls


# Distinct provider actions retain originating identities after configuration changes and restart
def test_native_lifecycle_and_remote_id_survive_restart(native_system):
    engine, incident, calls = native_system
    assert paging.drain(engine.ledger)['accepted'] == 2
    bindings = paging.status(engine.ledger)['bindings']
    alerts = next(item for item in bindings if item['provider'] == 'incident.io')
    responders = next(item for item in bindings if item['provider'] == 'better-stack')
    assert alerts['remote_state'] == 'unverified' and responders['remote_id'] == '42'
    assert responders['remote_state'] == 'started'
    with pytest.raises(Refused, match='does not support'):
        paging.action(engine.ledger, alerts['id'], 'acknowledge', alerts['version'])
    paging.save_config(engine.ledger, {}, 1)
    paging.activate(engine.ledger, 2)
    engine = Engine(engine.path)
    paging.action(engine.ledger, responders['id'], 'acknowledge', responders['version'])
    assert paging.drain(engine.ledger)['accepted'] == 1
    responders = next(item for item in paging.status(engine.ledger)['bindings'] if item['id'] == responders['id'])
    assert responders['remote_state'] == 'acknowledged' and responders['accepted_action'] == 'acknowledge'
    assert calls[-1][0].endswith('/incidents/42/acknowledge')
    assert calls[-1][1]['acknowledged_by'] == 'DeployOps API (local operator request)'
    for binding in (alerts, responders):
        paging.action(engine.ledger, binding['id'], 'resolve', binding['version'])
    assert paging.drain(engine.ledger)['accepted'] == 2
    alert_events = [data for url, data, _ in calls if 'api.incident.io' in url]
    assert [item['status'] for item in alert_events] == ['firing', 'resolved']
    assert len({item['deduplication_key'] for item in alert_events}) == 1
    assert all(item['incident_id'] == incident['id'] for item in paging.status(engine.ledger)['bindings'])
    assert 'MUST-NOT-RETAIN' not in json.dumps(paging.status(engine.ledger))


# A lost create response has no recoverable ID and cannot cause automatic retries or guessed lifecycle calls
def test_unknown_creation_and_explicit_duplicate_risk(native_system, monkeypatch):
    engine, _, calls = native_system
    monkeypatch.setattr(paging_providers, 'request', lambda *args, **kwargs: (0, {}, b''))
    assert paging.drain(engine.ledger)['unknown'] == 2
    assert paging.drain(engine.ledger)['attempted'] == 0
    binding = next(item for item in paging.status(engine.ledger)['bindings'] if item['provider'] == 'better-stack')
    assert not binding['remote_id']
    with pytest.raises(Refused, match='remote incident ID'):
        paging.action(engine.ledger, binding['id'], 'resolve', binding['version'])
    with pytest.raises(Refused, match='incident ID'):
        paging.readback(engine.ledger, binding['id'], binding['version'])
    with pytest.raises(Refused, match='duplicate'):
        paging.action(engine.ledger, binding['id'], 'retry', binding['version'])
    paging.action(engine.ledger, binding['id'], 'retry', binding['version'], True)
    assert paging.drain(engine.ledger)['unknown'] == 1
    assert calls == []


# Read-only observation resolves uncertainty about current state without rewriting original send evidence
def test_readback_after_unknown_ack_preserves_send_history(native_system, monkeypatch):
    engine, _, _ = native_system
    paging.drain(engine.ledger)
    binding = next(item for item in paging.status(engine.ledger)['bindings'] if item['provider'] == 'better-stack')
    paging.action(engine.ledger, binding['id'], 'acknowledge', binding['version'])
    monkeypatch.setattr(paging_providers, 'request', lambda *args, **kwargs: (0, {}, b''))
    assert paging.drain(engine.ledger)['unknown'] == 1
    binding = next(item for item in paging.status(engine.ledger)['bindings'] if item['id'] == binding['id'])

    # Status uses GET with no request body or new lifecycle command
    def read(url, body, headers, networks, method='POST'):
        assert method == 'GET' and body is None and url.endswith('/incidents/42')
        return 200, {}, incident_response(state='Acknowledged')

    monkeypatch.setattr(paging_providers, 'request', read)
    result = paging.readback(engine.ledger, binding['id'], binding['version'])
    assert result['binding']['remote_state'] == 'acknowledged'
    assert result['binding']['state'] == 'unknown' and result['binding']['accepted_action'] == 'trigger'
    assert any(item['action'] == 'acknowledge' and item['state'] == 'unknown' for item in paging.status(engine.ledger)['deliveries'])
    with pytest.raises(Refused, match='No retryable'):
        paging.action(engine.ledger, binding['id'], 'retry', result['binding']['version'], True)


# Stale or unavailable reads never overwrite a newer result or erase its last successful observation
def test_readback_conflicts_and_failure(native_system, monkeypatch):
    engine, _, _ = native_system
    paging.drain(engine.ledger)
    binding = next(item for item in paging.status(engine.ledger)['bindings'] if item['provider'] == 'better-stack')

    # Simulate a local operator transition racing a remote response
    def stale(settings, remote_id, identity):
        paging.action(engine.ledger, binding['id'], 'acknowledge', binding['version'])
        return {'state': 'observed', 'remote_state': 'resolved', 'observed_at': 123,
                'observation_source': 'better-stack-api'}

    with pytest.raises(Refused, match='during readback'):
        paging.readback(engine.ledger, binding['id'], binding['version'], reader=stale)
    paging.drain(engine.ledger)
    binding = next(item for item in paging.status(engine.ledger)['bindings'] if item['id'] == binding['id'])
    monkeypatch.setattr(paging_providers, 'request', lambda *args, **kwargs: (404, {}, b'private response'))
    result = paging.readback(engine.ledger, binding['id'], binding['version'])
    assert result['binding']['remote_state'] == 'acknowledged'
    assert result['readback']['state'] == 'unavailable' and 'private response' not in json.dumps(result)
    monkeypatch.setenv('DEPLOY_OPS_PAGING_BETTER', 'changed-fixture')
    with pytest.raises(Refused, match='credential changed'):
        paging.readback(engine.ledger, binding['id'], result['binding']['version'])


# Provider-confirmed resolution prevents queued transitions and future trigger replay
def test_observed_resolution_supersedes_queued_action(native_system, monkeypatch):
    engine, _, _ = native_system
    paging.drain(engine.ledger)
    binding = next(item for item in paging.status(engine.ledger)['bindings'] if item['provider'] == 'better-stack')
    binding = paging.action(engine.ledger, binding['id'], 'acknowledge', binding['version'])
    monkeypatch.setattr(paging_providers, 'request', lambda *args, **kwargs: (200, {}, incident_response(state='Resolved')))
    result = paging.readback(engine.ledger, binding['id'], binding['version'])
    assert result['binding']['remote_state'] == 'resolved'
    assert paging.drain(engine.ledger)['attempted'] == 0
    with pytest.raises(Refused, match='observed resolved'):
        paging.action(engine.ledger, binding['id'], 'retry', result['binding']['version'], True)


# A rejected lifecycle transition must never start an unrelated page at the fallback destination
def test_lifecycle_conflict_does_not_trigger_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv('DEPLOY_OPS_PAGING_BETTER', 'fixture')
    engine = Engine(create(tmp_path / 'workspace'))
    paging.save_config(engine.ledger, {'destinations': {'primary': BETTER, 'fallback': INCIDENT}, 'routes': [
        {'name': 'all', 'destinations': ['primary'], 'fallback': 'fallback'}]}, 0)
    paging.activate(engine.ledger, 1)
    release = engine.create('development')
    release['status'] = 'uncertain'
    engine.ledger.save(release, 'release.uncertain')
    open_incident(engine.ledger, release['id'], 'operations', 'Fixture')
    monkeypatch.setattr(paging_providers, 'request', lambda *args, **kwargs: (201, {}, incident_response()))
    assert paging.drain(engine.ledger)['accepted'] == 1
    binding = paging.status(engine.ledger)['bindings'][0]
    paging.action(engine.ledger, binding['id'], 'acknowledge', binding['version'])
    monkeypatch.setattr(paging_providers, 'request', lambda *args, **kwargs: (409, {}, b'{}'))
    assert paging.drain(engine.ledger)['failed'] == 1
    assert len(paging.status(engine.ledger)['bindings']) == 1
    assert paging.drain(engine.ledger)['attempted'] == 0


# Native response and endpoint failures never become guessed identities or false acceptance
@pytest.mark.parametrize('settings,code,raw', [
    (INCIDENT, 202, b'{"status":"success","deduplication_key":"other"}'),
    (INCIDENT, 200, b'{}'), (BETTER, 201, b'{"data":{"id":"../42","type":"incident"}}'),
    (BETTER, 201, b'not-json'), (BETTER, 200, incident_response()), (BETTER, 503, incident_response())])
def test_native_malformed_acceptance_stays_unknown(settings, code, raw, monkeypatch):
    monkeypatch.setenv(settings['key_env'], 'fixture')
    result = paging_providers.send(settings, PAYLOAD, 'trigger', 'dedup', transport=lambda *args: (code, {}, raw))
    assert result['state'] == 'unknown' and not result.get('remote_id')


# Documented create responses omit status while later GET states remain authoritative over timestamps
def test_timestamp_shaped_creation_and_validating_state(monkeypatch):
    monkeypatch.setenv(BETTER['key_env'], 'fixture')
    attributes = {'started_at': '2026-11-03T12:00:00Z', 'acknowledged_at': None, 'resolved_at': None}
    response = {'data': {'id': '42', 'type': 'incident', 'attributes': attributes}}
    result = paging_providers.send(BETTER, PAYLOAD, 'trigger', 'dedup',
                                   transport=lambda *args: (201, {}, json.dumps(response).encode()))
    assert result['state'] == 'accepted' and result['remote_id'] == '42' and result['remote_state'] == 'started'
    attributes.update(status='Validating', acknowledged_at='2026-11-03T12:01:00Z')
    assert paging_providers.better_stack_response(response)['remote_state'] == 'validating'
    attributes['status'] = 'FutureProviderState'
    assert 'remote_state' not in paging_providers.better_stack_response(response)


# Hosted native endpoints and required scoped routing settings are validated before network access
@pytest.mark.parametrize('settings', [
    {**INCIDENT, 'alert_source_id': '../other'}, {**INCIDENT, 'endpoint': 'https://attacker.example'},
    {**BETTER, 'policy_id': ''}, {**BETTER, 'requester_email': 'not-email'},
    {**BETTER, 'allowed_networks': ['127.0.0.1/32']}, {**INCIDENT, 'policy_id': '12'}])
def test_native_settings_reject_unsafe_or_inapplicable_options(settings):
    with pytest.raises(ValueError):
        Destination.model_validate(settings)


# CLI and authenticated console expose the same explicitly selected readback operation
def test_readback_interfaces(native_system, monkeypatch):
    from deploy_ops.cli import parser
    engine, _, _ = native_system
    paging.drain(engine.ledger)
    binding = next(item for item in paging.status(engine.ledger)['bindings'] if item['provider'] == 'better-stack')
    parsed = parser().parse_args(['paging', 'readback', binding['id'], '--version', str(binding['version'])])
    assert parsed.paging_action == 'readback'
    token = 'native-paging-fixture-' + 'x' * 40
    client = TestClient(create_app(engine, token), base_url='http://127.0.0.1:8765')
    body = {'binding': binding['id'], 'version': binding['version']}
    assert client.post('/api/paging/readback', json=body).status_code == 401
    response = client.post('/api/paging/readback', json=body, headers={'Authorization': 'Bearer ' + token})
    assert response.status_code == 200 and response.json()['readback']['remote_state'] == 'started'
