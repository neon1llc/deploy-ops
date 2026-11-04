# tests/test_paging.py - Exercise independent destination lifecycles and uncertain delivery recovery.


import concurrent.futures
import json
import threading

import pytest

from deploy_ops.delivery.engine import Engine
from deploy_ops.delivery.incidents import open_incident, transition
from deploy_ops.delivery import paging
from deploy_ops.delivery.paging_providers import send
from deploy_ops.delivery.scaffold import create
from deploy_ops.errors import Refused


# Activate two native instances without contacting either external provider
@pytest.fixture
def system(tmp_path, monkeypatch):
    engine = Engine(create(tmp_path / 'workspace'))
    monkeypatch.setenv('DEPLOY_OPS_PAGING_PD', 'p' * 32)
    monkeypatch.setenv('DEPLOY_OPS_PAGING_GO', 'go-test-token')
    settings = {'destinations': {
        'primary': {'provider': 'pagerduty', 'key_env': 'DEPLOY_OPS_PAGING_PD'},
        'self-hosted': {'provider': 'goalert', 'key_env': 'DEPLOY_OPS_PAGING_GO',
                        'endpoint': 'https://alerts.example/api/v2/generic/incoming'}},
        'default_destinations': ['primary', 'self-hosted']}
    paging.save_config(engine.ledger, settings, 0)
    paging.activate(engine.ledger, 1)
    release = engine.create('development')
    release['status'] = 'uncertain'
    engine.ledger.save(release, 'release.uncertain')
    return engine, release


# Emit a real ledger incident through the existing lifecycle owner
def opened(system):
    engine, release = system
    return open_incident(engine.ledger, release['id'], 'operations', 'Inspect the failed rollout')


# Model native acceptance without claiming a responder or remote incident state
def accepted(settings, payload, action, dedup, identity):
    return {'state': 'accepted', 'reason': 'Fixture acceptance', 'remote_id': '42'}


# Draft activation, preview and per-destination states survive reopening
def test_independent_destinations_and_local_lifecycle(system):
    engine, release = system
    assert paging.status(engine.ledger)['deliveries'] == []
    incident = opened(system)
    transition(engine.ledger, incident['id'], 'acknowledge', incident['version'], 'Investigating locally')
    assert len(paging.status(engine.ledger)['deliveries']) == 2
    assert paging.drain(engine.ledger, sender=accepted)['accepted'] == 2
    restarted = Engine(engine.path)
    bindings = paging.status(restarted.ledger)['bindings']
    assert {binding['accepted_action'] for binding in bindings} == {'trigger'}
    assert {binding['remote_state'] for binding in bindings} == {'unverified'}
    assert restarted.ledger.get(release['id'])['status'] == 'uncertain'
    goalert = next(binding for binding in bindings if binding['provider'] == 'goalert')
    with pytest.raises(Refused, match='does not support'):
        paging.action(restarted.ledger, goalert['id'], 'acknowledge', goalert['version'])
    pager = next(binding for binding in bindings if binding['provider'] == 'pagerduty')
    paging.action(restarted.ledger, pager['id'], 'acknowledge', pager['version'])
    assert paging.drain(restarted.ledger, sender=accepted)['accepted'] == 1


# A local resolution suppresses only unsent native triggers and retains original bindings
def test_resolved_before_first_send_and_originating_configuration(system):
    engine, _ = system
    incident = opened(system)
    transition(engine.ledger, incident['id'], 'resolve', incident['version'], 'Fixed before dispatch')
    assert paging.drain(engine.ledger, sender=accepted)['attempted'] == 0
    assert paging.status(engine.ledger)['counts']['superseded'] == 2
    paging.save_config(engine.ledger, {}, 1)
    paging.activate(engine.ledger, 2)
    binding = paging.status(engine.ledger)['bindings'][0]
    paging.action(engine.ledger, binding['id'], 'resolve', binding['version'])
    assert paging.drain(engine.ledger, sender=accepted)['accepted'] == 1


# Lost responses stay unknown, never auto-fail over or silently replay triggers
def test_unknown_and_explicit_retry(system):
    engine, _ = system
    opened(system)

    # Distinguish a provider timeout from a rejection before acceptance
    def uncertain(settings, payload, action, dedup, identity):
        return {'state': 'unknown', 'reason': 'Lost response'}

    assert paging.drain(engine.ledger, sender=uncertain)['unknown'] == 2
    assert paging.drain(engine.ledger, sender=accepted)['attempted'] == 0
    binding = paging.status(engine.ledger)['bindings'][0]
    with pytest.raises(Refused, match='duplicate'):
        paging.action(engine.ledger, binding['id'], 'retry', binding['version'])
    paging.action(engine.ledger, binding['id'], 'retry', binding['version'], True)
    assert paging.drain(engine.ledger, sender=accepted)['accepted'] == 1


# Concurrent drainers cannot send the same obligation twice
def test_concurrent_drains_and_expired_claim(system, monkeypatch):
    engine, _ = system
    opened(system)
    barrier = threading.Barrier(2)
    seen = []

    # Hold independent sends together to expose duplicate claims
    def blocked(settings, payload, action, dedup, identity):
        seen.append(dedup)
        barrier.wait(timeout=5)
        return accepted(settings, payload, action, dedup, identity)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: paging.drain(engine.ledger, limit=1, sender=blocked), range(2)))
    assert sum(result['accepted'] for result in results) == 2 and len(set(seen)) == 2
    paging.test_destination(engine.ledger, 'primary', 1)
    claim = paging._claim(engine.ledger)
    with engine.ledger.store.transaction() as connection:
        connection.execute('UPDATE notification_obligations SET claimed_at=1 WHERE id=?', (claim['id'],))
    assert paging.drain(engine.ledger, sender=accepted)['attempted'] == 0
    assert paging.status(engine.ledger)['counts']['unknown'] == 1


# Throttling schedules durable backoff while malformed native acceptance stays unknown
@pytest.mark.parametrize('provider,code,body,state', [
    ('pagerduty', 202, {'status': 'success', 'dedup_key': 'fixed'}, 'accepted'),
    ('pagerduty', 202, {'status': 'success', 'dedup_key': 'wrong'}, 'unknown'),
    ('pagerduty', 429, {}, 'failed'), ('pagerduty', 400, {}, 'failed'),
    ('pagerduty', 500, {}, 'unknown'), ('goalert', 200, {'AlertID': 42, 'IsNew': False}, 'accepted'),
    ('goalert', 204, {}, 'accepted'), ('goalert', 0, {}, 'unknown'),
])
def test_native_protocol(system, provider, code, body, state):
    engine, _ = system
    settings = paging.config(engine.ledger)['active']['destinations']['primary' if provider == 'pagerduty' else 'self-hosted']
    observed = []

    # Inspect exact transport contracts without resolving or contacting vendor hosts
    def transport(url, data, headers, networks):
        observed.append((url, json.loads(data), headers))
        return code, {'Retry-After': '120'}, json.dumps(body).encode()

    result = send(settings, {'event': 'incident.opened', 'release_id': 'test', 'severity': 'error'}, 'trigger', 'fixed', transport=transport)
    assert result['state'] == state
    assert result['retry_after'] == 120
    url, payload, headers = observed[0]
    if provider == 'pagerduty':
        assert payload['event_action'] == 'trigger' and payload['dedup_key'] == 'fixed'
    else:
        assert payload['dedup'] == 'fixed' and headers['Authorization'] == 'Bearer go-test-token'
        assert '?' not in url and 'token' not in payload
    assert 'go-test-token' not in json.dumps(result)


# First-match routing, quiet periods and multiple same-provider destinations remain explicit
def test_route_drafts_activation_and_fanout(system):
    engine, _ = system
    draft = paging.config(engine.ledger)['draft']
    draft['destinations']['other'] = dict(draft['destinations']['primary'])
    draft['routes'] = [{'name': 'development', 'environments': ['development'], 'destinations': ['other', 'other'],
                        'quiet_until': '2099-01-01T00:00:00+00:00'}]
    paging.save_config(engine.ledger, draft, 1)
    context = {'event': 'incident.opened', 'environment': 'development'}
    assert paging.preview(engine.ledger, context)['selected'] == ['primary', 'self-hosted']
    assert paging.preview(engine.ledger, context, draft)['selected'] == []
    paging.activate(engine.ledger, 2)
    opened(system)
    assert paging.status(engine.ledger)['deliveries'] == []
    with pytest.raises(Refused, match='changed'):
        paging.save_config(engine.ledger, draft, 1)


# Invalid configuration never echoes credential-like input or grants broad network access
@pytest.mark.parametrize('settings', [
    {'destinations': {'x': {'provider': 'pagerduty', 'key_env': 'super-secret-value'}}},
    {'destinations': {'x': {'provider': 'goalert', 'key_env': 'DEPLOY_OPS_PAGING_GO',
                            'endpoint': 'https://alerts.example/api/v2/generic/incoming?token=super-secret-value'}}},
    {'destinations': {'x': {'provider': 'json', 'endpoint_env': 'DEPLOY_OPS_PAGING_URL', 'allowed_networks': ['0.0.0.0/0']}}},
    {'destinations': {'x': {'provider': 'incident.io'}}},
])
def test_invalid_configuration_redaction(settings):
    with pytest.raises(Refused) as error:
        paging.validate(settings)
    assert 'super-secret-value' not in str(error.value)


# A successful later resolution cannot make an old unknown trigger retryable again
def test_retry_cannot_reopen_after_remote_resolution(system):
    engine, _ = system
    opened(system)
    paging.drain(engine.ledger, sender=lambda *args: {'state': 'unknown', 'reason': 'Lost response'})
    binding = paging.status(engine.ledger)['bindings'][0]
    paging.action(engine.ledger, binding['id'], 'resolve', binding['version'])
    paging.drain(engine.ledger, sender=accepted)
    binding = next(item for item in paging.status(engine.ledger)['bindings'] if item['id'] == binding['id'])
    with pytest.raises(Refused, match='No retryable'):
        paging.action(engine.ledger, binding['id'], 'retry', binding['version'], True)


# Native TLS verifies the fixture certificate and preserves JSON bytes at a local receiver
def test_real_local_tls_transport(tmp_path, monkeypatch):
    import datetime
    import ipaddress
    import ssl
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    from deploy_ops.delivery import paging_providers

    # Generate trust material only for this disposable loopback receiver
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'localhost')])
    now = datetime.datetime.now(datetime.timezone.utc)
    certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                   .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(minutes=1))
                   .not_valid_after(now + datetime.timedelta(hours=1))
                   .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost'),
                       x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), critical=False).sign(key, hashes.SHA256()))
    cert_path, key_path = tmp_path / 'cert.pem', tmp_path / 'key.pem'
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                            serialization.NoEncryption()))
    received = []

    # Capture requests without forwarding to any native paging provider
    class Receiver(BaseHTTPRequestHandler):
        # Return an accepted generic notification response after reading the real body
        def do_POST(self):
            received.append(self.rfile.read(int(self.headers['Content-Length'])))
            self.send_response(204)
            self.end_headers()


        # Keep test output free of request paths or sensitive headers
        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Receiver)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    trust = ssl.create_default_context(cafile=str(cert_path))
    monkeypatch.setattr(paging_providers.ssl, 'create_default_context', lambda: trust)
    try:
        code, _, _ = paging_providers.request(f'https://127.0.0.1:{server.server_port}/', b'{"test":true}',
                                              {'Content-Type': 'application/json'}, ['127.0.0.1/32'])
        assert code == 204 and received == [b'{"test":true}']
        with pytest.raises(Refused, match='private network'):
            paging_providers.request(f'https://127.0.0.1:{server.server_port}/', b'', {}, [])
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


# DNS failure precedes sending and hard deadlines stop slow native parser reads
def test_dns_failure_and_total_deadline(monkeypatch):
    import socket
    from deploy_ops.delivery import paging_providers

    # Fail resolution before any socket or authenticated write exists
    def failed_dns(*args, **kwargs):
        raise socket.gaierror('Fixture DNS failure')

    monkeypatch.setattr(paging_providers.socket, 'getaddrinfo', failed_dns)
    assert paging_providers.request('https://unavailable.example/', b'', {}, [])[0] == -1
    first, second = socket.socketpair()
    reader = paging_providers.DeadlineReader(first, 0)
    try:
        with pytest.raises(TimeoutError, match='deadline'):
            reader.readinto(bytearray(5))
    finally:
        reader.close()
        first.close()
        second.close()


# Connection-close responses retain their native socket file until the body is consumed
def test_delayed_body_survives_connection_close():
    import http.client
    import socket
    import time
    from deploy_ops.delivery.paging_providers import DeadlineSocket

    first, second = socket.socketpair()
    connection = http.client.HTTPConnection('fixture.example')
    connection.sock = DeadlineSocket(first, time.monotonic() + 5)
    try:
        connection.request('POST', '/', body=b'{}')
        second.recv(4096)
        second.sendall(b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n')
        response = connection.getresponse()
        second.sendall(b'{}')
        assert response.read() == b'{}'
        response.close()
    finally:
        connection.close()
        second.close()


# CLI TOML drafts and authenticated console requests use the same revision and activation rules
def test_cli_console_shared_contract(system, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from deploy_ops.cli import dispatch, parser
    from deploy_ops.console import create_app

    engine, _ = system
    token = 'local-paging-api-fixture-' + 'x' * 40
    app = create_app(engine, token)
    client = TestClient(app, base_url='http://127.0.0.1:8765')
    headers = {'Authorization': 'Bearer ' + token}
    assert client.get('/api/paging/config').status_code == 401
    assert client.get('/assets/paging.js').status_code == 200
    response = client.post('/api/paging/save', headers=headers, json={'revision': 1, 'paging_configuration': {
        'destinations': {'local': {'provider': 'ntfy', 'endpoint_env': 'DEPLOY_OPS_PAGING_NTFY'}}, 'default_destinations': ['local']}})
    assert response.status_code == 200 and response.json()['active_revision'] == 1
    assert client.post('/api/paging/activate', headers=headers, json={'revision': 1}).status_code == 409
    assert client.post('/api/paging/activate', headers=headers, json={'revision': 2}).status_code == 200
    args = parser().parse_args(['--project', str(engine.path), 'paging', 'config'])
    assert dispatch(args)['active_revision'] == 2
    assert client.post('/api/paging/preview', headers=headers, json={'context': {'event': 'release.succeeded'}}).json()['selected'] == ['local']
    assert paging.status(engine.ledger)['deliveries'] == []
    fixture = tmp_path / 'paging.toml'
    fixture.write_text('schema_version = 1\ndefault_destinations = []\n')
    draft = dispatch(parser().parse_args(['--project', str(engine.path), 'paging', 'save', str(fixture), '--revision', '2']))
    assert draft['revision'] == 3 and draft['active_revision'] == 2


# Throttle recovery, partial fanout and fallback preserve independent destination outcomes
def test_throttle_and_definite_fallback(system):
    engine, _ = system
    draft = paging.config(engine.ledger)['draft']
    draft['routes'] = [{'name': 'incident', 'destinations': ['primary'], 'fallback': 'self-hosted'}]
    paging.save_config(engine.ledger, draft, 1)
    paging.activate(engine.ledger, 2)
    opened(system)
    paging.drain(engine.ledger, sender=lambda *args: {'state': 'failed', 'reason': 'Throttle', 'retryable': True, 'retry_after': 120})
    rows = paging.status(engine.ledger)['deliveries']
    assert len(rows) == 1 and rows[0]['state'] == 'queued'
    assert paging.drain(engine.ledger, sender=accepted)['attempted'] == 0
    with engine.ledger.store.transaction() as connection:
        connection.execute('UPDATE notification_obligations SET due=0')
    paging.drain(engine.ledger, limit=1, sender=lambda *args: {'state': 'failed', 'reason': 'Rejected', 'retryable': False})
    assert paging.drain(engine.ledger, sender=accepted)['accepted'] == 1
    assert paging.status(engine.ledger)['counts'] == {'accepted': 1, 'failed': 1}


# A real local check failure produces a routed incident without any external provider call
def test_failed_local_release_routes_to_two_same_provider_instances(system):
    import sys

    engine, _ = system
    settings = paging.config(engine.ledger)['draft']
    settings['destinations']['self-hosted'] = dict(settings['destinations']['primary'])
    paging.save_config(engine.ledger, settings, 1)
    paging.activate(engine.ledger, 2)
    import tomlkit
    authored = tomlkit.parse(engine.path.read_text())
    authored['components']['foundation']['build'] = {'checks': [[sys.executable, '-c', 'raise SystemExit(7)']]}
    engine.path.write_text(tomlkit.dumps(authored))
    engine = Engine(engine.path)
    release = engine.create('development')
    failed = engine.advance(release['id'])
    assert failed['status'] == 'uncertain'
    assert any(event['name'] == 'command.failed' and event['detail']['exit_code'] == 7
               for event in engine.ledger.events(release['id']))
    open_incident(engine.ledger, release['id'], 'operations', 'Investigate failed local check')
    assert paging.drain(engine.ledger, sender=accepted)['accepted'] == 2
    bindings = paging.status(engine.ledger)['bindings']
    assert {binding['provider'] for binding in bindings} == {'pagerduty'}
    assert len({binding['dedup'] for binding in bindings}) == 2


# Ordinary success uses notification transports and a foreground worker can stop cleanly
def test_success_notification_and_foreground_worker_health(system, monkeypatch):
    engine, release = system
    paging.save_config(engine.ledger, {'destinations': {'notices': {
        'provider': 'json', 'endpoint_env': 'DEPLOY_OPS_PAGING_NOTICE'}}, 'default_destinations': ['notices']}, 1)
    paging.activate(engine.ledger, 2)
    engine.ledger.event(release['id'], 'release.succeeded', {})
    stop = threading.Event()
    observed = []

    # Finish the worker after the bounded local send without installing a host service
    def sending(settings, payload, action, dedup, identity):
        observed.append((payload['event'], action))
        stop.set()
        return accepted(settings, payload, action, dedup, identity)

    monkeypatch.setattr(paging, 'send', sending)
    worker = threading.Thread(target=paging.run_worker, args=(engine.ledger, stop, 1))
    worker.start()
    worker.join(timeout=5)
    assert not worker.is_alive()
    state = paging.status(engine.ledger)
    assert observed == [('release.succeeded', 'notify')]
    assert state['bindings'] == [] and state['counts']['accepted'] == 1
    assert state['worker']['running'] is False and state['worker']['healthy'] is False


# Credential replacement cannot redirect lifecycle updates to another native integration
def test_originating_integration_is_pinned(system, monkeypatch):
    engine, _ = system
    opened(system)
    paging.drain(engine.ledger, sender=accepted)
    binding = next(item for item in paging.status(engine.ledger)['bindings'] if item['provider'] == 'pagerduty')
    queued = paging.action(engine.ledger, binding['id'], 'resolve', binding['version'])
    with pytest.raises(Refused, match='changed'):
        paging.action(engine.ledger, binding['id'], 'resolve', binding['version'])
    monkeypatch.setenv('DEPLOY_OPS_PAGING_PD', 'replacement-fixture-key')
    result = paging.drain(engine.ledger)
    assert result['failed'] == 1
    state = paging.status(engine.ledger)
    assert 'Originating integration credential changed' in json.dumps(state)
    assert 'replacement-fixture-key' not in json.dumps(state)
