# src/deploy_ops/delivery/ledger.py - Persist release progress alongside the authority audit trail.


from __future__ import annotations

import hashlib
import json
import os
import time
import socket
import sqlite3
import uuid
from pathlib import Path

from ..config import canonical
from ..errors import Refused
from ..store import Store


# Store delivery progress beside the shared authority ledger
class Ledger:

    # Retained history reading does not initialize delivery tables or current authority
    @classmethod
    def open_existing(cls, state: Path):
        ledger = cls.__new__(cls)
        ledger._held = {}
        ledger.store = Store.open_existing(state / 'state.db')
        with ledger.store.connect() as connection:
            for table, fields in {'releases': {'id', 'environment', 'status', 'data'},
                                  'release_events': {'sequence', 'release_id', 'time', 'name', 'detail'}}.items():
                if not fields.issubset({row['name'] for row in connection.execute(f'PRAGMA table_info({table})')}):
                    raise Refused("Retained delivery state is uninitialized or uses an unsupported schema", 503)
        return ledger


    # Create additive delivery tables without replacing controller state
    def __init__(self, store: Store):
        self.store = store
        self._held = {}
        with store.connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS releases (
                    id TEXT PRIMARY KEY, environment TEXT NOT NULL, status TEXT NOT NULL, data TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS release_environment ON releases(environment);
                CREATE TABLE IF NOT EXISTS release_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    release_id TEXT NOT NULL, time INTEGER NOT NULL, name TEXT NOT NULL, detail TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS release_locks (
                    scope TEXT PRIMARY KEY, release_id TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS release_lock_owners (
                    scope TEXT PRIMARY KEY, owner_pid INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS incidents (
                    id TEXT PRIMARY KEY, release_id TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL, data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, payload TEXT NOT NULL,
                    sent INTEGER NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0
                );
            """)
        from .paging import initialize
        initialize(self.store)


    # Persist release state and its event atomically
    def save(self, release: dict, event: str | None = None, detail: dict | None = None) -> dict:
        with self.store.transaction() as connection:
            row = connection.execute('SELECT data FROM releases WHERE id=?', (release['id'],)).fetchone()
            current = json.loads(row[0]) if row else None
            if current and event == 'release.cancel_requested':
                if current['status'] in {'succeeded', 'failed', 'uncertain', 'cancelled'}:
                    raise Refused('This release finished before cancellation could be recorded; inspect its outcome')
                requested = release['status']
                release.update(current)
                release['status'] = requested
            elif current and current['status'] in {'cancel_requested', 'cancelled'}:
                # A stale component copy may update its facts, never erase an accepted stop
                if event == 'release.succeeded':
                    release['status'], event = 'cancelled', 'release.cancelled'
                elif release['status'] not in {'failed', 'uncertain', 'cancelled'}:
                    release['status'] = current['status']
            connection.execute(
                "INSERT INTO releases VALUES (?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
                "status=excluded.status, data=excluded.data",
                (release["id"], release["environment"], release["status"], canonical(release)),
            )
            if event:
                self._event(connection, release["id"], event, detail or {})
        return release


    # Queue transport work only after recording the durable event
    def _event(self, connection, release_id: str, name: str, detail: dict):
        now = int(time.time())
        cursor = connection.execute("INSERT INTO release_events(release_id,time,name,detail) VALUES (?,?,?,?)",
                           (release_id, now, name, canonical(detail)))
        self.store.event(connection, now, "delivery", name, release_id, detail)
        # Destination obligations commit atomically with their originating event
        from .paging import enqueue
        enqueue(connection, release_id, name, detail, cursor.lastrowid)
        if name in {"release.succeeded", "release.failed", "release.waiting", "release.uncertain"}:
            connection.execute("INSERT INTO notifications(payload) VALUES (?)", (
                canonical({"release_id": release_id, "event": name, "time": now}),
            ))


    # Give standalone delivery events their own transaction
    def event(self, release_id: str, name: str, detail: dict):
        with self.store.transaction() as connection:
            self._event(connection, release_id, name, detail)


    # Require an existing release before returning its state
    def get(self, release_id: str) -> dict:
        with self.store.connect() as connection:
            row = connection.execute("SELECT data FROM releases WHERE id=?", (release_id,)).fetchone()
        if row is None:
            raise Refused("Release not found", 404)
        return json.loads(row[0])


    # Bound ordinary history reads without changing retained evidence
    def list(self) -> list[dict]:
        with self.store.connect() as connection:
            return [json.loads(row[0]) for row in connection.execute(
                "SELECT data FROM releases ORDER BY rowid DESC LIMIT 200")]


    # Return a release's events in their committed order
    def events(self, release_id: str) -> list[dict]:
        with self.store.connect() as connection:
            return [{**dict(row), "detail": json.loads(row["detail"])} for row in connection.execute(
                "SELECT * FROM release_events WHERE release_id=? ORDER BY sequence", (release_id,))]


    # Bind local liveness evidence to this host and canonical store, without exporting raw host identifiers
    def _worker_identity(self):
        host = hashlib.sha256((socket.gethostname() + ':' + str(uuid.getnode())).encode()).hexdigest()
        return {"protocol_version": 1, "host_id": host, "state_path": str(self.store.path.resolve())}


    # Record the admitted local owner in the existing event journal under the same transaction as its lock
    def _worker_event(self, connection, release_id, name, owner_id=None):
        detail = {**self._worker_identity(), "pid": os.getpid(), "owner_id": owner_id or uuid.uuid4().hex}
        self._event(connection, release_id, name, detail)
        return detail


    # Bind a real local controller claim to this exact admitted operation, never to a readback lock
    def worker_binding(self, scope, release_id):
        owned = self._held.get(scope)
        if not owned or owned[0] != release_id or not owned[1]:
            raise Refused("A local claim requires its admitted worker ownership")
        return {**owned[2], 'scope': scope, 'kind': owned[1]}


    # Derive recovery capability without changing a lock, record or controller decision
    def worker_status(self, scope: str, release_id: str, kind: str = "worker"):
        try:
            with self.store.connect() as connection:
                return self._worker_status(connection, scope, release_id, kind)
        except (sqlite3.Error, Refused, ValueError, TypeError, AttributeError):
            return {'state': 'unknown', 'reason': 'Worker ownership evidence is unavailable; retain state and inspect through the original owner.'}


    # Share one liveness interpretation with transactional recovery admission
    def _worker_status(self, connection, scope, release_id, kind):
        row = connection.execute("SELECT l.release_id,o.owner_pid FROM release_locks l "
            "LEFT JOIN release_lock_owners o ON o.scope=l.scope WHERE l.scope=?", (scope,)).fetchone()
        names = tuple('release.' + part + '.' + phase for part in (kind, kind + '.recovery') for phase in ('started', 'stopped'))
        latest = connection.execute("SELECT sequence,name,detail FROM release_events WHERE release_id=? AND name IN (?,?,?,?) "
            "ORDER BY sequence DESC LIMIT 1", (release_id, *names)).fetchone()
        unknown = {"state": "unknown", "reason": "Local worker ownership is unverified. Inspect through the original operation owner; no lock was cleared."}
        if not latest:
            return unknown
        try:
            evidence = json.loads(latest['detail'])
            if not isinstance(evidence, dict):
                return unknown
        except (ValueError, TypeError):
            return unknown
        if any(evidence.get(key) != value for key, value in self._worker_identity().items()):
            return {**unknown, "reason": "Worker evidence belongs to another host, state location or protocol. Use the original operation owner."}
        release_row = connection.execute('SELECT data FROM releases WHERE id=?', (release_id,)).fetchone()
        if not release_row or not evidence.get('owner_id'):
            return unknown
        release = json.loads(release_row[0])
        nodes = ([release.get('rollout', {}), release.get('rollout', {}).get('retirement', {})] if kind == 'rollout'
                 else ([release.get('cleanup', {})] if kind == 'cleanup' else release['nodes'].values()))
        for node in nodes:
            if not node.get('request_id'):
                continue
            request = self.store.request(connection, node['request_id'])
            execution = request.get('execution')
            if not execution or (node.get('status') not in {'running', 'post-check', 'uncertain'} and request['status'] not in {'running', 'uncertain'}):
                continue
            binding = execution.get('local_worker') or {}
            expected = self._worker_identity() | {'scope': scope, 'kind': kind, 'owner_id': evidence['owner_id']}
            if any(binding.get(key) != value for key, value in expected.items()):
                return {**unknown, 'reason': 'Controller execution has no matching local worker admission. Inspect through the original execution owner.'}
        if not row:
            if not latest['name'].endswith('.stopped'):
                return unknown
            started = connection.execute("SELECT sequence FROM release_events WHERE release_id=? AND sequence<? "
                "AND name IN (?,?) ORDER BY sequence DESC LIMIT 1",
                (release_id, latest['sequence'], 'release.' + kind + '.started', 'release.' + kind + '.recovery.started')).fetchone()
            if not started:
                return unknown
            commands = self._commands_status(connection, release_id, started['sequence'])
            return commands or {"state": "stopped", "owner_id": evidence['owner_id'], "reason": "The tracked local supervisor released its lock and command groups are stopped. Target inspection is still required."}
        pid = row['owner_pid']
        if row['release_id'] != release_id or not isinstance(pid, int) or pid < 2 or evidence.get('pid') != pid or not latest['name'].endswith('.started'):
            return unknown
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            commands = self._commands_status(connection, release_id, latest['sequence'])
            return commands or {"state": "dead", "owner_id": evidence['owner_id'], "reason": "The recorded local worker and any tracked command groups no longer run. Target inspection is still required."}
        except OSError:
            return {**unknown, "reason": "Worker liveness cannot be verified; recovery is blocked."}
        return {"state": "live", "reason": "The worker process still exists; stop it before reconciliation."}


    # A dead supervisor cannot stand in for a command group that may still run
    def _commands_status(self, connection, release_id, started_sequence):
        rows = connection.execute("SELECT name,detail FROM release_events WHERE release_id=? AND sequence>? "
            "AND name IN ('command.started','command.launched','command.failed','command.stopped') ORDER BY sequence",
            (release_id, started_sequence))
        active = {}
        unknown = {"state": "unknown", "reason": "Interrupted command ownership is unverified. Inspect the original command before recovery."}
        for row in rows:
            try:
                detail = json.loads(row['detail'])
                identifier = detail.get('command_id')
                if not isinstance(identifier, str) or not identifier:
                    return unknown
                if row['name'] == 'command.started':
                    active[identifier] = None
                elif row['name'] == 'command.launched':
                    active[identifier] = detail.get('process_group')
                elif row['name'] == 'command.stopped' or detail.get('outcome') == 'not-started':
                    active.pop(identifier, None)
            except (ValueError, TypeError, AttributeError):
                return unknown
        for group in active.values():
            if os.name != 'posix' or not isinstance(group, int) or group < 2:
                return unknown
            try:
                os.killpg(group, 0)
            except ProcessLookupError:
                continue
            except OSError:
                return unknown
            return {"state": "command_live", "reason": "The local supervisor exited but a tracked command group still exists. Inspect and stop the original command before recovery."}
        return None


    # Allow one writer to own a target scope
    def lock(self, scope: str, release_id: str, kind: str | None = None):
        detail = None
        with self.store.transaction() as connection:
            try:
                connection.execute("INSERT INTO release_locks(scope,release_id) VALUES (?,?)", (scope, release_id))
                connection.execute("INSERT INTO release_lock_owners VALUES (?,?) ON CONFLICT(scope) DO UPDATE SET owner_pid=excluded.owner_pid", (scope, os.getpid()))
                if kind and scope == "release:" + release_id:
                    detail = self._worker_event(connection, release_id, "release." + kind + ".started")
            except sqlite3.IntegrityError as error:
                raise Refused("Another release owns this target. Reconcile interrupted work before retrying") from error
        self._held[scope] = (release_id, kind, detail)


    # Mark an effect-capable operation only after its semantic admission succeeds
    def start_worker(self, scope, release_id, kind):
        if self._held.get(scope) != (release_id, None, None):
            raise Refused("Worker admission requires this operation's held local lock")
        with self.store.transaction() as connection:
            row = connection.execute("SELECT l.release_id,o.owner_pid FROM release_locks l "
                "LEFT JOIN release_lock_owners o ON o.scope=l.scope WHERE l.scope=?", (scope,)).fetchone()
            if not row or row['release_id'] != release_id or row['owner_pid'] != os.getpid():
                raise Refused("Worker lock ownership changed before admission")
            detail = self._worker_event(connection, release_id, 'release.' + kind + '.started')
        self._held[scope] = (release_id, kind, detail)


    # Require observed worker death before replacing ownership
    def recovery_lock(self, scope: str, release_id: str, kind: str = "worker"):
        """Replace only an observed dead worker's lock, atomically with acquiring recovery ownership."""
        detail = None
        with self.store.transaction() as connection:
            if scope == "release:" + release_id:
                observed = self._worker_status(connection, scope, release_id, kind)
                if observed['state'] not in {'dead', 'stopped'}:
                    raise Refused(observed['reason'])
            row = connection.execute("SELECT l.release_id,o.owner_pid FROM release_locks l "
                                     "LEFT JOIN release_lock_owners o ON o.scope=l.scope WHERE l.scope=?", (scope,)).fetchone()
            if row:
                pid = row["owner_pid"]
                if row["release_id"] != release_id or not isinstance(pid, int) or pid < 2:
                    raise Refused("Lock ownership is unknown; inspect the legacy worker before recovery")
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    pass
                except OSError as error:
                    raise Refused("Worker liveness cannot be verified; recovery is blocked") from error
                else:
                    raise Refused("The worker process still exists; stop it before reconciliation")
                connection.execute("DELETE FROM release_locks WHERE scope=?", (scope,))
            connection.execute("INSERT INTO release_locks(scope,release_id) VALUES (?,?)", (scope, release_id))
            connection.execute("INSERT INTO release_lock_owners VALUES (?,?) ON CONFLICT(scope) DO UPDATE SET owner_pid=excluded.owner_pid", (scope, os.getpid()))
            if scope == "release:" + release_id:
                detail = self._worker_event(connection, release_id, "release." + kind + ".recovery.started", observed['owner_id'])
        self._held[scope] = (release_id, kind + '.recovery' if scope == 'release:' + release_id else None, detail)


    # Remove only this process's ownership and retain a stop fact in the same transaction
    def unlock(self, scope: str, release_id: str):
        owned = self._held.get(scope)
        if not owned or owned[0] != release_id:
            return
        with self.store.transaction() as connection:
            row = connection.execute("SELECT l.release_id,o.owner_pid FROM release_locks l "
                "LEFT JOIN release_lock_owners o ON o.scope=l.scope WHERE l.scope=?", (scope,)).fetchone()
            if not row or row['release_id'] != release_id or row['owner_pid'] != os.getpid():
                return
            if owned[1] and scope == "release:" + release_id:
                self._worker_event(connection, release_id, 'release.' + owned[1] + '.stopped', owned[2]['owner_id'])
            connection.execute("DELETE FROM release_locks WHERE scope=? AND release_id=?", (scope, release_id))
            connection.execute("DELETE FROM release_lock_owners WHERE scope=?", (scope,))
        self._held.pop(scope, None)


    # Reuse successful outputs only under the same workspace contract
    def latest_outputs(self, environment: str, component: str, config_digest: str) -> dict | None:
        # Stream only this environment's retained evidence, independently of display pagination
        with self.store.connect() as connection:
            rows = connection.execute("SELECT data FROM releases WHERE environment=? ORDER BY rowid DESC", (environment,))
            for row in rows:
                release = json.loads(row[0])
                if release["config_digest"] != config_digest:
                    continue
                node = release["nodes"].get(component, {})
                if node.get("status") == "succeeded":
                    # Any later claim may have changed the producer even if it ultimately failed
                    request_id = node.get('request_id')
                    request = self.store.request(connection, request_id) if request_id else None
                    if not request or request['status'] != 'succeeded':
                        return None
                    latest = connection.execute(
                        "SELECT r.id FROM requests r JOIN audit a ON json_extract(a.payload, '$.object_id')=r.id "
                        "WHERE r.target=? AND json_extract(a.payload, '$.event')='execution.claimed' "
                        "ORDER BY a.sequence DESC LIMIT 1", (request['manifest']['target'],)
                    ).fetchone()
                    if not latest or latest['id'] != request_id:
                        continue
                    return {"outputs": node["outputs"], "receipt": release["id"], "source": node["source"]}
        return None
