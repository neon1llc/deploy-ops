# tests/test_waiting.py - Keep approval observations distinct from durable decision-needed transitions.


import json

import pytest

from deploy_ops import cli
from deploy_ops.delivery import paging
from deploy_ops.delivery.engine import Engine
from deploy_ops.delivery.scaffold import create


# Use the real source capture, controller and outboxes without starting any transport
@pytest.fixture
def engine(tmp_path):
    owner = Engine(create(tmp_path / 'workspace'))
    paging.save_config(owner.ledger, {'destinations': {'notice': {
        'provider': 'json', 'endpoint_env': 'DEPLOY_OPS_PAGING_NOTICE'}},
        'default_destinations': ['notice']}, 0)
    paging.activate(owner.ledger, 1)
    return owner


# Inspect original event identities and both durable notification owners
def waiting(owner, identifier):
    events = [event for event in owner.ledger.events(identifier) if event['name'] == 'release.waiting']
    with owner.ledger.store.connect() as connection:
        legacy = [row[0] for row in connection.execute('SELECT id,payload FROM notifications')
                  if json.loads(row[1])['event'] == 'release.waiting']
        named = [row[0] for row in connection.execute('SELECT id,payload FROM notification_obligations')
                 if json.loads(row[1])['event'] == 'release.waiting']
        assert connection.execute('SELECT count(*) FROM notification_attempts').fetchone()[0] == 0
        assert connection.execute('SELECT count(*) FROM notification_bindings').fetchone()[0] == 0
    return events, legacy, named


# Restart and unchanged pending observations retain one event, while the next component gets its own
def test_waiting_request_observation_is_not_a_new_transition(engine):
    release = engine.advance(engine.create('development')['id'])
    identifier = release['id']
    original = waiting(engine, identifier)
    assert [len(items) for items in original] == [1, 1, 1]
    owner = Engine(engine.path)
    for _ in range(3):
        assert owner.advance(identifier)['status'] == 'waiting'
    assert waiting(owner, identifier) == original
    foundation = release['nodes']['foundation']
    owner.approve(identifier, 'foundation', foundation['request_digest'])
    following = owner.advance(identifier)
    assert following['nodes']['foundation']['status'] == 'succeeded'
    assert following['nodes']['web']['status'] == 'waiting'
    assert [len(items) for items in waiting(owner, identifier)] == [2, 2, 2]
    assert owner.advance(identifier)['status'] == 'waiting'
    events, legacy, named = waiting(owner, identifier)
    assert [len(items) for items in [events, legacy, named]] == [2, 2, 2]
    assert events[0]['detail']['request_id'] == foundation['request_id']
    assert events[1]['detail']['request_id'] == following['nodes']['web']['request_id']
    assert events[0]['detail']['request_digest'] == foundation['request_digest']
    owner.approve(identifier, 'web', following['nodes']['web']['request_digest'])
    assert owner.advance(identifier)['status'] == 'succeeded'


# A bounded waiter rechecks live authority each visit without treating each visit as a transition
def test_bounded_cli_wait_preserves_pending_identity(engine, monkeypatch):
    release = engine.create('development', 'foundation')
    now = [0]
    sleeps = []
    checks = []
    original_status = engine.gate.status

    # Observe every real controller read rather than returning a cached approval
    def status(identifier):
        checks.append(identifier)
        return original_status(identifier)

    # Advance only the waiter's monotonic clock; controller wall-clock authority stays real
    def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds

    monkeypatch.setattr(engine.gate, 'status', status)
    monkeypatch.setattr(cli.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(cli.time, 'sleep', sleep)
    observed = cli.advance(engine, release['id'], 12)
    assert observed['status'] == 'waiting'
    assert sleeps == [5, 5, 2] and len(checks) == 4 and len(set(checks)) == 1
    assert [len(items) for items in waiting(engine, release['id'])] == [1, 1, 1]



# Decisions arriving during a wait still use current authority and preserve bounded cancellation
@pytest.mark.parametrize('decision,expected', [('approve', 'succeeded'), ('reject', 'failed'), ('cancel', 'cancelled'), ('expire', 'failed')])
def test_wait_rechecks_decisions_between_visits(engine, monkeypatch, decision, expected):
    release = engine.advance(engine.create('development', 'foundation')['id'])
    node = release['nodes']['foundation']
    now = [0]

    # Deliver one real operator/controller transition between actual pending observations
    def sleep(seconds):
        now[0] += seconds
        if decision == 'cancel':
            engine.cancel(release['id'])
        elif decision == 'expire':
            engine.gate.controller.clock = lambda: 4_000_000_000
        else:
            engine.gate.approve(node['request_id'], node['request_digest'], decision)

    monkeypatch.setattr(cli.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(cli.time, 'sleep', sleep)
    observed = cli.advance(engine, release['id'], 12)
    assert observed['status'] == expected
    assert now[0] == 5
    assert len(waiting(engine, release['id'])[0]) == 1
    if decision != 'approve':
        assert not list(engine.deployments.rglob('resource.json'))


# A lost return after the atomic waiting/event/outbox commit does not create another semantic event
def test_saved_waiting_transition_survives_lost_return(engine, monkeypatch):
    release = engine.create('development', 'foundation')
    save = engine.ledger.save

    # Interrupt only after the real transaction returned, preserving its durable row and queues
    def lost_return(data, event=None, detail=None):
        result = save(data, event, detail)
        if event == 'release.waiting':
            raise KeyboardInterrupt('Synthetic lost caller return after commit')
        return result

    monkeypatch.setattr(engine.ledger, 'save', lost_return)
    with pytest.raises(KeyboardInterrupt):
        engine.advance(release['id'])
    original = waiting(engine, release['id'])
    assert [len(items) for items in original] == [1, 1, 1]
    restarted = Engine(engine.path)
    assert restarted.advance(release['id'])['status'] == 'waiting'
    assert waiting(restarted, release['id']) == original


# A worker acquiring ownership after another legacy migration must reload the committed identity
def test_markerless_waiting_identity_reloaded_under_worker_ownership(engine, monkeypatch):
    release = engine.advance(engine.create('development', 'foundation')['id'])
    identifier = release['id']
    release['nodes']['foundation'].pop('waiting_request')
    engine.ledger.save(release)
    a, b = Engine(engine.path), Engine(engine.path)
    acquire = a.ledger.lock
    middle = []

    # B completes a real serialized advance before A actually acquires the same native lock
    def before_lock(scope, owner, kind=None):
        b.advance(identifier)
        middle.append(waiting(engine, identifier))
        acquire(scope, owner, kind)

    monkeypatch.setattr(a.ledger, 'lock', before_lock)
    assert a.advance(identifier)['status'] == 'waiting'
    assert [len(items) for items in middle[0]] == [2, 2, 2]
    assert waiting(engine, identifier) == middle[0]


# A cancellation that settles before lock acquisition cannot be overwritten by an older waiting copy
def test_cancelled_release_revalidated_under_worker_ownership(engine, monkeypatch):
    from deploy_ops.errors import Refused
    release = engine.advance(engine.create('development', 'foundation')['id'])
    identifier = release['id']
    a, b = Engine(engine.path), Engine(engine.path)
    acquire = a.ledger.lock

    # Preserve normal lock and terminal guards rather than suppressing a contention refusal
    def before_lock(scope, owner, kind=None):
        b.cancel(identifier)
        acquire(scope, owner, kind)

    monkeypatch.setattr(a.ledger, 'lock', before_lock)
    with pytest.raises(Refused, match='Terminal or interrupted'):
        a.advance(identifier)
    assert engine.ledger.get(identifier)['status'] == 'cancelled'
    assert [len(items) for items in waiting(engine, identifier)] == [1, 1, 1]
    with engine.ledger.store.connect() as connection:
        assert connection.execute('SELECT count(*) FROM release_locks').fetchone()[0] == 0
