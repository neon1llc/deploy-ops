# src/deploy_ops/delivery/ledger.py - Persist release progress alongside the authority audit trail.

from __future__ import annotations

import json
import time
from pathlib import Path

from ..config import canonical
from ..errors import Refused
from ..store import Store


class Ledger:
    def __init__(self, store: Store):
        self.store = store
        with store.connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS releases (
                    id TEXT PRIMARY KEY, environment TEXT NOT NULL, status TEXT NOT NULL, data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS release_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    release_id TEXT NOT NULL, time INTEGER NOT NULL, name TEXT NOT NULL, detail TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS release_locks (
                    scope TEXT PRIMARY KEY, release_id TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, payload TEXT NOT NULL,
                    sent INTEGER NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0
                );
            """)

    def save(self, release: dict, event: str | None = None, detail: dict | None = None) -> dict:
        with self.store.transaction() as connection:
            connection.execute(
                "INSERT INTO releases VALUES (?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
                "status=excluded.status, data=excluded.data",
                (release["id"], release["environment"], release["status"], canonical(release)),
            )
            if event:
                self._event(connection, release["id"], event, detail or {})
        return release

    def _event(self, connection, release_id: str, name: str, detail: dict):
        now = int(time.time())
        connection.execute("INSERT INTO release_events(release_id,time,name,detail) VALUES (?,?,?,?)",
                           (release_id, now, name, canonical(detail)))
        self.store.event(connection, now, "delivery", name, release_id, detail)
        if name in {"release.succeeded", "release.failed", "release.waiting", "release.uncertain"}:
            connection.execute("INSERT INTO notifications(payload) VALUES (?)", (
                canonical({"release_id": release_id, "event": name, "time": now}),
            ))

    def event(self, release_id: str, name: str, detail: dict):
        with self.store.transaction() as connection:
            self._event(connection, release_id, name, detail)

    def get(self, release_id: str) -> dict:
        with self.store.connect() as connection:
            row = connection.execute("SELECT data FROM releases WHERE id=?", (release_id,)).fetchone()
        if row is None:
            raise Refused("Release not found", 404)
        return json.loads(row[0])

    def list(self) -> list[dict]:
        with self.store.connect() as connection:
            return [json.loads(row[0]) for row in connection.execute(
                "SELECT data FROM releases ORDER BY rowid DESC LIMIT 200")]

    def events(self, release_id: str) -> list[dict]:
        with self.store.connect() as connection:
            return [{**dict(row), "detail": json.loads(row["detail"])} for row in connection.execute(
                "SELECT * FROM release_events WHERE release_id=? ORDER BY sequence", (release_id,))]

    def lock(self, scope: str, release_id: str):
        import sqlite3
        with self.store.transaction() as connection:
            try:
                connection.execute("INSERT INTO release_locks VALUES (?,?)", (scope, release_id))
            except sqlite3.IntegrityError as error:
                raise Refused("Another release owns this target. Reconcile interrupted work before retrying") from error

    def unlock(self, scope: str, release_id: str):
        with self.store.transaction() as connection:
            connection.execute("DELETE FROM release_locks WHERE scope=? AND release_id=?", (scope, release_id))

    def latest_outputs(self, environment: str, component: str, config_digest: str) -> dict | None:
        for release in self.list():
            if release["environment"] != environment or release["config_digest"] != config_digest:
                continue
            node = release["nodes"].get(component, {})
            if node.get("status") == "succeeded":
                return {"outputs": node["outputs"], "receipt": release["id"], "source": node["source"]}
        return None
