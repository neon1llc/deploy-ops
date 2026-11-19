# src/deploy_ops/delivery/ledger.py - Persist release progress alongside the authority audit trail.


from __future__ import annotations

import json
import os
import time
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


    # Allow one writer to own a target scope
    def lock(self, scope: str, release_id: str):
        import sqlite3
        with self.store.transaction() as connection:
            try:
                connection.execute("INSERT INTO release_locks(scope,release_id) VALUES (?,?)", (scope, release_id))
                connection.execute("INSERT INTO release_lock_owners VALUES (?,?) ON CONFLICT(scope) DO UPDATE SET owner_pid=excluded.owner_pid", (scope, os.getpid()))
            except sqlite3.IntegrityError as error:
                raise Refused("Another release owns this target. Reconcile interrupted work before retrying") from error


    # Require observed worker death before replacing ownership
    def recovery_lock(self, scope: str, release_id: str):
        """Replace only an observed dead worker's lock, atomically with acquiring recovery ownership."""
        with self.store.transaction() as connection:
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


    # Remove ownership only for the release that acquired it
    def unlock(self, scope: str, release_id: str):
        with self.store.transaction() as connection:
            removed = connection.execute("DELETE FROM release_locks WHERE scope=? AND release_id=?", (scope, release_id))
            if removed.rowcount:
                connection.execute("DELETE FROM release_lock_owners WHERE scope=?", (scope,))


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
