# src/deploy_ops/delivery/paging.py - Route durable destination work through the existing incident ledger.


import hashlib
import json
import random
import threading
import time
import tomllib
import uuid
from datetime import datetime

from pydantic import ValidationError

from ..config import canonical
from ..errors import Refused
from .paging_config import CAPABILITIES, PLANNED, Destination, Paging, Route
from .paging_providers import credential, read_remote, send


# Add paging records beside existing incidents without rewriting legacy notification history
def initialize(store):
    with store.connect() as connection:
        connection.executescript('''
            CREATE TABLE IF NOT EXISTS notification_routes (
                event_id TEXT PRIMARY KEY, revision INTEGER NOT NULL, context TEXT NOT NULL, decision TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS notification_bindings (
                id TEXT PRIMARY KEY, incident_id TEXT NOT NULL, destination TEXT NOT NULL,
                data TEXT NOT NULL, UNIQUE(incident_id,destination)
            );
            CREATE TABLE IF NOT EXISTS notification_obligations (
                id TEXT PRIMARY KEY, event_id TEXT NOT NULL, destination TEXT NOT NULL,
                binding_id TEXT NOT NULL, action TEXT NOT NULL, state TEXT NOT NULL,
                payload TEXT NOT NULL, settings TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                due INTEGER NOT NULL, claim TEXT NOT NULL DEFAULT '', claimed_at INTEGER NOT NULL DEFAULT 0,
                result TEXT NOT NULL DEFAULT '{}', UNIQUE(event_id,destination)
            );
            CREATE TABLE IF NOT EXISTS notification_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT, obligation_id TEXT NOT NULL,
                claim TEXT NOT NULL, started_at INTEGER NOT NULL, completed_at INTEGER,
                result TEXT NOT NULL DEFAULT '{}'
            );
        ''')


# Return the active revision without reading credential values
def _configuration(connection):
    row = connection.execute("SELECT value FROM settings WHERE key='paging'").fetchone()
    return json.loads(row[0]) if row else {'revision': 0, 'active_revision': 0,
                                         'draft': Paging().model_dump(), 'active': Paging().model_dump()}


# Expose shared forms and capabilities with the authored draft
def config(ledger):
    with ledger.store.connect() as connection:
        result = _configuration(connection)
    return {**result, 'forms': {'destination': Destination.model_json_schema(), 'route': Route.model_json_schema()},
            'capabilities': CAPABILITIES, 'planned': list(PLANNED)}


# Parse TOML or structured settings through the same secret-free model
def validate(configuration):
    try:
        if isinstance(configuration, str):
            configuration = tomllib.loads(configuration)
        return Paging.model_validate(configuration).model_dump()
    except (ValueError, TypeError, ValidationError):
        raise Refused('Invalid paging settings; check provider fields, references, routes and timezone', 422) from None


# Save a draft without activating routes or sending a synthetic page
def save_config(ledger, configuration, revision):
    draft = validate(configuration)
    with ledger.store.transaction() as connection:
        current = _configuration(connection)
        if current['revision'] != revision:
            raise Refused('Paging settings changed; reload before saving')
        current.update(revision=revision + 1, draft=draft)
        connection.execute("INSERT INTO settings VALUES('paging',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                           (canonical(current),))
    return config(ledger)


# Activate only the reviewed draft while retaining all previous destination bindings
def activate(ledger, revision):
    with ledger.store.transaction() as connection:
        current = _configuration(connection)
        if current['revision'] != revision:
            raise Refused('Paging draft changed; review it before activation')
        current.update(active_revision=revision, active=current['draft'])
        connection.execute("INSERT INTO settings VALUES('paging',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                           (canonical(current),))
        ledger.store.event(connection, int(time.time()), 'paging', 'configuration.activated', str(revision), {})
    return config(ledger)


# Explain deterministic first-match routes without contacting a destination
def _route(settings, context, now):
    selected = settings['default_destinations']
    reason, fallback, quiet = 'Default route', '', False
    for route in settings['routes']:
        if all(not route[field] or context.get(key, '') in route[field] for field, key in (
                ('projects', 'project'), ('environments', 'environment'), ('components', 'component'),
                ('events', 'event'), ('severities', 'severity'))):
            selected, fallback = route['destinations'], route['fallback']
            reason = 'Route ' + route['name']
            quiet = bool(route['quiet_until'] and datetime.fromisoformat(
                route['quiet_until'].replace('Z', '+00:00')).timestamp() > now)
            break
    rows = []
    native_event = context.get('event') == 'incident.opened'
    for name, destination in settings['destinations'].items():
        matched = name in selected
        why = reason if matched else 'Not selected by the effective route'
        if matched and not destination['enabled']:
            matched, why = False, 'Destination disabled'
        elif matched and quiet:
            matched, why = False, 'Quiet period is active'
        elif matched and not CAPABILITIES[destination['provider']]['notifications'] and not native_event:
            matched, why = False, 'Native pages start only from an opened incident'
        rows.append({'name': name, 'selected': matched, 'reason': why})
    return {'selected': [row['name'] for row in rows if row['selected']], 'destinations': rows, 'fallback': fallback}


# Preview either the active revision or an unsaved validated draft
def preview(ledger, context, configuration=None):
    with ledger.store.connect() as connection:
        current = _configuration(connection)
    settings = validate(configuration) if configuration is not None else current['active']
    return {**_route(settings, context, time.time()), 'revision': current['active_revision']}


# Read one binding inside the transaction owning its next transition
def _binding(connection, binding_id):
    row = connection.execute('SELECT data FROM notification_bindings WHERE id=?', (binding_id,)).fetchone()
    if row is None:
        raise Refused('Paging binding not found', 404)
    return json.loads(row[0])


# Persist binding state separately from transport attempts and local incident decisions
def _save_binding(connection, binding):
    connection.execute('UPDATE notification_bindings SET data=? WHERE id=?', (canonical(binding), binding['id']))


# Queue one operation per event and destination with a frozen credential reference
def _obligation(connection, event_id, name, settings, payload, action, binding_id='', fallback=''):
    identifier = hashlib.sha256((event_id + ':' + name).encode()).hexdigest()
    record = {**payload, 'event_id': event_id, 'fallback': fallback}
    connection.execute('INSERT OR IGNORE INTO notification_obligations '
                       '(id,event_id,destination,binding_id,action,state,payload,settings,due) VALUES(?,?,?,?,?,?,?,?,?)',
                       (identifier, event_id, name, binding_id, action, 'queued', canonical(record),
                        canonical(settings), int(time.time())))
    return identifier


# Freeze native alert identity before its first network attempt
def _open_binding(connection, incident_id, name, settings, payload, revision):
    row = connection.execute('SELECT data FROM notification_bindings WHERE incident_id=? AND destination=?',
                             (incident_id, name)).fetchone()
    if row:
        return json.loads(row[0])
    identifier = uuid.uuid4().hex
    binding = {'id': identifier, 'incident_id': incident_id, 'destination': name, 'provider': settings['provider'],
               'version': 1, 'revision': revision, 'dedup': 'deploy-ops-' + identifier, 'settings': settings,
               'payload': payload, 'identity': '', 'remote_id': '', 'state': 'queued', 'desired_action': 'trigger',
               'accepted_action': '', 'remote_state': 'unverified', 'locally_resolved': False}
    connection.execute('INSERT INTO notification_bindings VALUES(?,?,?,?)',
                       (identifier, incident_id, name, canonical(binding)))
    return binding


# Route new durable events in the same transaction as their incident or release
def enqueue(connection, release_id, event, detail, sequence):
    if event not in {'release.succeeded', 'release.failed', 'release.uncertain', 'release.waiting',
                     'incident.opened', 'incident.acknowledged', 'incident.resolved', 'incident.escalated'}:
        return
    current = _configuration(connection)
    row = connection.execute('SELECT data FROM releases WHERE id=?', (release_id,)).fetchone()
    release = json.loads(row[0]) if row else {}
    component = detail.get('component', '')
    if not component:
        failed = [name for name, node in release.get('nodes', {}).items() if node['status'] in ('failed', 'uncertain')]
        component = failed[0] if len(failed) == 1 else ''
    payload = {'release_id': release_id, 'event': event, 'project': release.get('project', ''),
               'environment': release.get('environment', ''), 'component': component,
               'severity': 'critical' if release.get('status') == 'uncertain' else 'error',
               'actor': detail.get('actor', 'local-operator'), 'time': int(time.time())}
    if event in ('release.succeeded', 'release.waiting'):
        payload['severity'] = 'info'
    incident_id = detail.get('incident_id', '')
    if incident_id:
        incident_row = connection.execute('SELECT data FROM incidents WHERE id=?', (incident_id,)).fetchone()
        if incident_row:
            incident = json.loads(incident_row[0])
            payload.update({key: value for key, value in incident.items() if key != 'id'})
        payload.update(incident_id=incident_id, version=detail['version'])

    # Local resolution cancels unsent native triggers without claiming remote closure
    if event == 'incident.resolved':
        bindings = connection.execute('SELECT data FROM notification_bindings WHERE incident_id=?', (incident_id,))
        for row in bindings.fetchall():
            binding = json.loads(row[0])
            binding.update(locally_resolved=True, version=binding['version'] + 1)
            _save_binding(connection, binding)
            connection.execute("UPDATE notification_obligations SET state='superseded' "
                               "WHERE binding_id=? AND action='trigger' AND state='queued'", (binding['id'],))
    route = _route(current['active'], payload, time.time())
    event_id = 'event-' + str(sequence)
    connection.execute('INSERT INTO notification_routes VALUES(?,?,?,?)',
                       (event_id, current['active_revision'], canonical(payload), canonical(route)))
    for name in route['selected']:
        settings = current['active']['destinations'][name]
        native = not CAPABILITIES[settings['provider']]['notifications']
        binding = _open_binding(connection, incident_id, name, settings, payload, current['active_revision']) if native else None
        _obligation(connection, event_id, name, settings, payload, 'trigger' if native else 'notify',
                    binding['id'] if binding else '',
                    {'name': route['fallback'], 'settings': current['active']['destinations'][route['fallback']],
                     'revision': current['active_revision']} if route['fallback'] else '')


# Request one destination action without acknowledging the local incident or a deployment
def action(ledger, binding_id, action, version, allow_duplicate=False):
    if action not in ('acknowledge', 'resolve', 'retry'):
        raise Refused('Choose acknowledge, resolve or retry', 422)
    with ledger.store.transaction() as connection:
        binding = _binding(connection, binding_id)
        if binding['version'] != version:
            raise Refused('Paging binding changed; review the latest result')
        if action != 'retry' and not CAPABILITIES[binding['provider']][action]:
            raise Refused('This provider does not support acknowledgement through its selected API', 422)
        if binding.get('remote_state') == 'resolved':
            raise Refused('The provider was observed resolved; read status instead of sending another transition', 422)
        if binding['provider'] == 'better-stack' and action != 'retry' and not binding['remote_id']:
            raise Refused('Better Stack needs the saved remote incident ID before acknowledgement or resolution', 422)
        if action == 'retry':
            if not allow_duplicate:
                raise Refused('Retry may create a duplicate page; explicitly allow duplicate risk', 422)
            row = connection.execute("SELECT * FROM notification_obligations WHERE binding_id=? "
                                     "ORDER BY rowid DESC LIMIT 1", (binding_id,)).fetchone()
            if (row is None or row['state'] not in ('failed', 'unknown')
                    or row['action'] != binding['desired_action']
                    or (row['action'] == 'trigger' and (binding['locally_resolved'] or
                        (binding['provider'] == 'better-stack' and binding['remote_id'])))
                    or (row['action'] == 'acknowledge' and binding.get('remote_state') == 'acknowledged')):
                raise Refused('No retryable current operation; resolved incidents cannot replay triggers', 422)
            if row['attempts'] >= binding['settings']['attempts']:
                raise Refused('Attempt limit exhausted; inspect the destination before a new incident', 422)
            connection.execute("UPDATE notification_obligations SET state='queued',due=? WHERE id=?",
                               (int(time.time()), row['id']))
        else:
            if binding['desired_action'] == 'resolve':
                raise Refused('Resolution already requested; inspect or explicitly retry its delivery', 422)
            # Resolution never leaves an older unsent trigger available for a later drain
            if action == 'resolve':
                connection.execute("UPDATE notification_obligations SET state='superseded' "
                                   "WHERE binding_id=? AND state='queued' AND action IN ('trigger','acknowledge')", (binding_id,))
            _obligation(connection, 'binding-' + binding_id + '-' + str(version + 1), binding['destination'],
                        binding['settings'], binding['payload'], action, binding_id)
            binding['desired_action'] = action
        binding.update(version=version + 1, state='queued')
        _save_binding(connection, binding)
        ledger.store.event(connection, int(time.time()), 'paging', 'binding.' + action, binding_id,
                           {'actor': 'local-operator', 'duplicate_risk_accepted': allow_duplicate})
    return _public(binding)


# Record explicit native readback without rewriting local incidents or uncertain send history
def readback(ledger, binding_id, version, reader=None):
    with ledger.store.connect() as connection:
        binding = _binding(connection, binding_id)
        in_flight = connection.execute("SELECT 1 FROM notification_obligations WHERE binding_id=? AND state='in-flight'",
                                       (binding_id,)).fetchone()
    if binding['version'] != version or in_flight:
        raise Refused('Binding changed or a send is in flight; refresh before readback')
    if not CAPABILITIES[binding['provider']]['readback']:
        raise Refused('The selected provider API does not support remote readback', 422)
    result = (reader or read_remote)(binding['settings'], binding['remote_id'], binding['identity'])
    with ledger.store.transaction() as connection:
        current = _binding(connection, binding_id)
        in_flight = connection.execute("SELECT 1 FROM notification_obligations WHERE binding_id=? AND state='in-flight'",
                                       (binding_id,)).fetchone()
        if current['version'] != version or in_flight:
            raise Refused('Binding changed during readback; discard this stale observation and refresh')
        current['last_readback'] = {**result, 'checked_at': int(time.time())}
        if result['state'] == 'observed':
            current.update({key: result[key] for key in ('remote_state', 'observed_at', 'observation_source')})
            if result['remote_state'] == 'resolved':
                connection.execute("UPDATE notification_obligations SET state='superseded' WHERE binding_id=? "
                                   "AND state='queued'", (binding_id,))
                if current['state'] == 'queued':
                    current['state'] = 'observed'
        current['version'] += 1
        _save_binding(connection, current)
        ledger.store.event(connection, int(time.time()), 'paging', 'binding.readback', binding_id,
                           {'actor': 'local-operator', 'result': result})
    return {'binding': _public(current), 'readback': result}


# Queue a clearly synthetic incident only after an explicit operator send action
def test_destination(ledger, name, revision):
    with ledger.store.transaction() as connection:
        current = _configuration(connection)
        if current['active_revision'] != revision or name not in current['active']['destinations']:
            raise Refused('Select a destination from the current active revision', 422)
        settings = current['active']['destinations'][name]
        if not settings['enabled']:
            raise Refused('Enable and activate this destination before sending a test', 422)
        identifier = 'test-' + uuid.uuid4().hex
        payload = {'release_id': identifier, 'incident_id': identifier, 'event': 'incident.opened',
                   'project': 'DeployOps TEST', 'environment': 'test', 'component': '', 'severity': 'info',
                   'actor': 'local-operator', 'synthetic': True, 'time': int(time.time())}
        native = not CAPABILITIES[settings['provider']]['notifications']
        binding = _open_binding(connection, identifier, name, settings, payload, revision) if native else None
        delivery = _obligation(connection, identifier, name, settings, payload, 'trigger' if native else 'notify',
                               binding['id'] if binding else '')
        ledger.store.event(connection, int(time.time()), 'paging', 'test.queued', identifier, {'destination': name})
    return {'delivery_id': delivery, 'binding': _public(binding) if binding else None, 'state': 'queued'}


# Hide credential hashes and internal snapshots from operator responses
def _public(binding):
    return {key: value for key, value in binding.items() if key not in ('identity', 'settings', 'payload')}


# Return transport evidence without exposing request URLs, credentials or raw responses
def status(ledger):
    with ledger.store.connect() as connection:
        bindings = [_public(json.loads(row[0])) for row in connection.execute('SELECT data FROM notification_bindings ORDER BY rowid DESC')]
        deliveries = [dict(row) for row in connection.execute(
            'SELECT id,destination,binding_id,action,state,attempts,due,result,settings FROM notification_obligations ORDER BY rowid DESC LIMIT 200')]
        counts = {row['state']: row['count'] for row in connection.execute(
            'SELECT state,COUNT(*) AS count FROM notification_obligations GROUP BY state')}
        routes = [{**dict(row), 'context': json.loads(row['context']), 'decision': json.loads(row['decision'])}
                  for row in connection.execute('SELECT * FROM notification_routes ORDER BY rowid DESC LIMIT 40')]
        row = connection.execute("SELECT value FROM settings WHERE key='paging-worker'").fetchone()
    for delivery in deliveries:
        delivery['attempt_limit'] = json.loads(delivery.pop('settings'))['attempts']
        delivery['exhausted'] = delivery['state'] == 'failed' and delivery['attempts'] >= delivery['attempt_limit']
        delivery['overdue'] = delivery['state'] == 'queued' and delivery['due'] < time.time() - 30
        delivery['result'] = {key: value for key, value in json.loads(delivery['result']).items() if key != 'identity'}
    worker = json.loads(row[0]) if row else {'last_seen': None}
    worker['healthy'] = worker.get('running', False) and worker['last_seen'] is not None and time.time() - worker['last_seen'] < 60
    return {'bindings': bindings, 'deliveries': deliveries, 'counts': counts, 'worker': worker, 'routes': routes}


# Reserve an exclusive attempt and treat abandoned sends as unknown
def _claim(ledger):
    now = int(time.time())
    with ledger.store.transaction() as connection:
        expired = connection.execute("SELECT * FROM notification_obligations WHERE state='in-flight' AND claimed_at<?",
                                     (now - 120,)).fetchall()
        for row in expired:
            outcome = canonical({'state': 'unknown', 'reason': 'Worker stopped before recording the remote outcome'})
            connection.execute("UPDATE notification_obligations SET state='unknown',result=? WHERE id=?", (outcome, row['id']))
            connection.execute('UPDATE notification_attempts SET completed_at=?,result=? WHERE claim=?', (now, outcome, row['claim']))
            if row['binding_id']:
                binding = _binding(connection, row['binding_id'])
                binding.update(state='unknown', version=binding['version'] + 1)
                _save_binding(connection, binding)
        row = connection.execute("SELECT a.* FROM notification_obligations a WHERE a.state='queued' AND a.due<=? "
                                 "AND NOT EXISTS (SELECT 1 FROM notification_obligations b WHERE a.binding_id!='' "
                                 "AND b.binding_id=a.binding_id AND (b.state='in-flight' OR (b.state='queued' AND b.rowid<a.rowid))) "
                                 'ORDER BY a.rowid LIMIT 1', (now,)).fetchone()
        if row is None:
            return None
        claim = uuid.uuid4().hex
        connection.execute("UPDATE notification_obligations SET state='in-flight',attempts=attempts+1,claim=?,claimed_at=? WHERE id=?",
                           (claim, now, row['id']))
        connection.execute('INSERT INTO notification_attempts(obligation_id,claim,started_at) VALUES(?,?,?)', (row['id'], claim, now))
        return {**dict(row), 'claim': claim, 'attempts': row['attempts'] + 1}


# Commit only the owning attempt's sanitized outcome and bounded retry schedule
def _finish(ledger, row, result):
    now = int(time.time())
    settings = json.loads(row['settings'])
    with ledger.store.transaction() as connection:
        current = connection.execute('SELECT state,claim FROM notification_obligations WHERE id=?', (row['id'],)).fetchone()
        if current['state'] != 'in-flight' or current['claim'] != row['claim']:
            return
        state = result['state']
        due = now
        binding = _binding(connection, row['binding_id']) if row['binding_id'] else None
        obsolete = binding and row['action'] == 'trigger' and (binding['locally_resolved'] or binding['desired_action'] == 'resolve')
        if state == 'failed' and result.get('retryable') and row['attempts'] < settings['attempts'] and not obsolete:
            state = 'queued'
            due += max(result.get('retry_after', 0), min(3600, settings['retry_seconds'] * 2 ** (row['attempts'] - 1)))
            due += random.randint(0, min(30, settings['retry_seconds']))
        connection.execute('UPDATE notification_obligations SET state=?,due=?,result=? WHERE id=?',
                           (state, due, canonical(result), row['id']))
        connection.execute('UPDATE notification_attempts SET completed_at=?,result=? WHERE claim=?', (now, canonical(result), row['claim']))
        if binding:
            binding.update(state=state, version=binding['version'] + 1)
            if state == 'accepted':
                binding['accepted_action'] = row['action']
                binding['remote_id'] = result.get('remote_id', binding['remote_id'])
                for key in ('remote_state', 'observed_at', 'observation_source'):
                    if key in result:
                        binding[key] = result[key]
            _save_binding(connection, binding)

        # Fallback is explicit and follows only a definite final rejection
        payload = json.loads(row['payload'])
        fallback = payload.get('fallback')
        if state == 'failed' and fallback and not obsolete and row['action'] in ('trigger', 'notify'):
            target, target_settings = fallback['name'], fallback['settings']
            native = not CAPABILITIES[target_settings['provider']]['notifications']
            if not target_settings['enabled'] or (native and payload['event'] != 'incident.opened'):
                return
            next_binding = _open_binding(connection, payload['incident_id'], target, target_settings, payload, fallback['revision']) if native else None
            _obligation(connection, row['event_id'], target, target_settings, payload, 'trigger' if native else 'notify',
                        next_binding['id'] if next_binding else '')


# Drain bounded queued work without turning notification failure into deployment failure
def drain(ledger, limit=50, sender=None, heartbeat=None):
    if type(limit) is not int or not 1 <= limit <= 200:
        raise Refused('Drain limit must be between 1 and 200', 422)
    counts = {'attempted': 0, 'accepted': 0, 'failed': 0, 'unknown': 0}
    for _ in range(limit):
        if heartbeat:
            heartbeat()
        row = _claim(ledger)
        if row is None:
            break
        settings, payload = json.loads(row['settings']), json.loads(row['payload'])
        identity, dedup, remote_id = '', row['id'], ''
        try:
            if row['binding_id']:
                # Pin the originating integration hash before the first possible external send
                observed = hashlib.sha256(credential(settings['key_env']).encode()).hexdigest()
                with ledger.store.transaction() as connection:
                    binding = _binding(connection, row['binding_id'])
                    if not binding['identity']:
                        binding['identity'] = observed
                        _save_binding(connection, binding)
                    identity, dedup, remote_id = binding['identity'], binding['dedup'], binding['remote_id']
            public_payload = {key: value for key, value in payload.items() if key != 'fallback'}
            if row['binding_id']:
                public_payload['remote_id'] = remote_id
            result = (sender or send)(settings, public_payload, row['action'], dedup, identity)
        except Refused:
            result = {'state': 'failed', 'reason': 'Check destination settings and required credential references', 'retryable': False}
        except Exception:
            result = {'state': 'unknown', 'reason': 'Transport stopped without a recorded outcome', 'retryable': False}
        _finish(ledger, row, result)
        counts['attempted'] += 1
        counts[result['state']] += 1
    return counts


# Run due timers and delivery independently of the browser with visible worker health
def run_worker(ledger, stop=None, interval=5):
    if not 1 <= interval <= 10:
        raise Refused('Worker interval must be between 1 and 10 seconds', 422)
    from .incidents import tick
    stop = stop or threading.Event()
    worker_id = uuid.uuid4().hex


    # Refresh health between bounded requests without exposing process credentials
    def heartbeat():
        with ledger.store.transaction() as connection:
            connection.execute("INSERT INTO settings VALUES('paging-worker',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                               (canonical({'last_seen': int(time.time()), 'running': True, 'id': worker_id}),))

    try:
        while not stop.is_set():
            heartbeat()
            tick(ledger)
            drain(ledger, heartbeat=heartbeat)
            stop.wait(interval)
    finally:
        with ledger.store.transaction() as connection:
            row = connection.execute("SELECT value FROM settings WHERE key='paging-worker'").fetchone()
            if row and json.loads(row[0]).get('id') == worker_id:
                connection.execute("UPDATE settings SET value=? WHERE key='paging-worker'",
                                   (canonical({'last_seen': int(time.time()), 'running': False, 'id': worker_id}),))
