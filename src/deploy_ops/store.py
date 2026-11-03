# src/deploy_ops/store.py - Commit decisions and audit links in the same SQLite transaction.

import hashlib
import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .config import canonical, fingerprint
from .errors import Refused

SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
    id TEXT PRIMARY KEY, digest TEXT NOT NULL UNIQUE, target TEXT NOT NULL,
    status TEXT NOT NULL, data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS request_state ON requests(target, status);
CREATE TABLE IF NOT EXISTS delegations (id TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT, payload TEXT NOT NULL,
    previous_hash TEXT NOT NULL, hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS deliveries (id TEXT PRIMARY KEY, expires_at INTEGER NOT NULL);
PRAGMA user_version = 1;
"""


class Store:
    """Serialize authority changes so concurrent claims cannot overspend a delegation."""

    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connect() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise Refused("Unsupported database version. Restore with the matching release", 503)
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(SCHEMA)
        os.chmod(self.path, 0o600)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    @staticmethod
    def request(connection, request_id: str) -> dict:
        row = connection.execute("SELECT data FROM requests WHERE id = ?", (request_id,)).fetchone()
        if row is None:
            raise Refused("Request not found", 404)
        return json.loads(row["data"])

    @staticmethod
    def save_request(connection, request: dict) -> None:
        connection.execute(
            "INSERT INTO requests VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET status=excluded.status, data=excluded.data",
            (request["id"], request["digest"], request["manifest"]["target"], request["status"], canonical(request)),
        )

    @staticmethod
    def delegation(connection, delegation_id: str) -> dict:
        row = connection.execute("SELECT data FROM delegations WHERE id = ?", (delegation_id,)).fetchone()
        if row is None:
            raise Refused("Delegation not found", 404)
        return json.loads(row["data"])

    @staticmethod
    def save_delegation(connection, delegation: dict) -> None:
        connection.execute(
            "INSERT INTO delegations VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
            (delegation["id"], canonical(delegation)),
        )

    @staticmethod
    def paused(connection) -> bool:
        row = connection.execute("SELECT value FROM settings WHERE key='paused'").fetchone()
        return row is not None and row[0] == "true"

    @staticmethod
    def event(connection, now: int, actor: str, name: str, object_id: str, detail: dict) -> None:
        previous = connection.execute("SELECT hash FROM audit ORDER BY sequence DESC LIMIT 1").fetchone()
        previous_hash = previous[0] if previous else "0" * 64
        payload = canonical({"time": now, "actor": actor, "event": name, "object_id": object_id, "detail": detail})
        digest = hashlib.sha256((previous_hash + "\n" + payload).encode()).hexdigest()
        connection.execute(
            "INSERT INTO audit(payload, previous_hash, hash) VALUES (?, ?, ?)",
            (payload, previous_hash, digest),
        )

    def audit(self) -> list[dict]:
        with self.connect() as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM audit ORDER BY sequence")]

    def verify(self) -> dict:
        previous = "0" * 64
        rows = self.audit()
        for sequence, row in enumerate(rows, start=1):
            digest = hashlib.sha256((previous + "\n" + row["payload"]).encode()).hexdigest()
            if row["sequence"] != sequence or row["previous_hash"] != previous or row["hash"] != digest:
                raise Refused("Audit chain does not match its stored events", 409)
            previous = digest
        # This head needs an independent archive to detect deletion or a fully rewritten chain
        return {"events": len(rows), "head": previous, "format": fingerprint({"version": 1})}
