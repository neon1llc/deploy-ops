# src/deploy_ops/store.py - Commit decisions and audit links in the same SQLite transaction.


import hashlib
import json
import os
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .config import canonical, fingerprint
from .errors import Refused

# Keep request state and its audit chain in the same database
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

# Restored copies retain historical authority rows without admitting new writes
RESTORE_MARKER = '.deploy-ops-restore.json'


# Keep imported state inspection-only across delivery and standalone controller entry points
def require_writable(path: Path):
    parent = path.resolve().parent
    for directory in (parent, *parent.parents):
        marker = directory / RESTORE_MARKER
        if marker.exists() or marker.is_symlink():
            raise Refused('Restored state is inspection-only. Revalidate original ownership and current authority before execution; no replay or adoption is supplied', 403)


# Persist authority decisions and tamper-evident audit links together
class Store:
    """Serialize authority changes so concurrent claims cannot overspend a delegation."""


    # Reject unknown schema versions before opening writable state
    def __init__(self, path: str):
        require_writable(Path(path))
        self.read_only = False
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connect() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise Refused("Unsupported database version. Restore with the matching release", 503)
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(SCHEMA)
        os.chmod(self.path, 0o600)


    # Inspect only supported existing private state without bootstrap, migration or repair
    @classmethod
    def open_existing(cls, path: Path, *, writable: bool = False):
        path = path.resolve()
        if writable:
            require_writable(path)
        if not path.is_file():
            raise Refused("Retained state not found or uninitialized at the selected location", 404)
        metadata = path.stat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
            raise Refused("Retained state must be an owner-only regular file", 403)
        # A relocated WAL without shared memory needs a coherent capture, never an immutable shortcut
        wal = path.with_name(path.name + '-wal')
        if wal.exists() and wal.stat().st_size and not path.with_name(path.name + '-shm').is_file():
            raise Refused("Retained WAL inspection needs its existing shared-memory file or a coherent backup", 503)
        store = cls.__new__(cls)
        store.path, store.read_only = path, True
        store.existing = True
        try:
            with store.connect() as connection:
                if connection.execute("PRAGMA user_version").fetchone()[0] != 1:
                    raise Refused("Unsupported database version. Restore with the matching release", 503)
                if connection.execute("PRAGMA quick_check(1)").fetchone()[0] != 'ok':
                    raise Refused("Retained database integrity is unavailable; preserve it for inspection", 503)
                required = {'requests': {'id', 'digest', 'target', 'status', 'data'},
                            'audit': {'sequence', 'payload', 'previous_hash', 'hash'}}
                for table, fields in required.items():
                    if not fields.issubset({row['name'] for row in connection.execute(f'PRAGMA table_info({table})')}):
                        raise Refused("Retained state has an unsupported schema", 503)
        except sqlite3.Error:
            raise Refused("Retained state cannot be read consistently; no repair was attempted", 503) from None
        store.read_only = not writable
        return store


    # Close each connection even if the caller fails
    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        if not self.read_only:
            require_writable(self.path)
        if self.read_only:
            connection = sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True, timeout=5, isolation_level=None)
            connection.execute("PRAGMA query_only=ON")
        elif getattr(self, 'existing', False):
            connection = sqlite3.connect(self.path.as_uri() + '?mode=rw', uri=True, timeout=5, isolation_level=None)
        else:
            connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()


    # Acquire the write lock before reading state that will be changed
    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        if self.read_only:
            raise Refused("Retained inspection cannot mutate state", 403)
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise


    # Read one request within the caller's active transaction
    @staticmethod
    def request(connection, request_id: str) -> dict:
        row = connection.execute("SELECT data FROM requests WHERE id = ?", (request_id,)).fetchone()
        if row is None:
            raise Refused("Request not found", 404)
        return json.loads(row["data"])


    # Keep queryable status and serialized evidence in the same row
    @staticmethod
    def save_request(connection, request: dict) -> None:
        connection.execute(
            "INSERT INTO requests VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET status=excluded.status, data=excluded.data",
            (request["id"], request["digest"], request["manifest"]["target"], request["status"], canonical(request)),
        )


    # Require a known grant before evaluating its remaining allowance
    @staticmethod
    def delegation(connection, delegation_id: str) -> dict:
        row = connection.execute("SELECT data FROM delegations WHERE id = ?", (delegation_id,)).fetchone()
        if row is None:
            raise Refused("Delegation not found", 404)
        return json.loads(row["data"])


    # Persist grant counters without changing its identifier
    @staticmethod
    def save_delegation(connection, delegation: dict) -> None:
        connection.execute(
            "INSERT INTO delegations VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
            (delegation["id"], canonical(delegation)),
        )


    # Treat the stored switch as the controller-wide stop condition
    @staticmethod
    def paused(connection) -> bool:
        row = connection.execute("SELECT value FROM settings WHERE key='paused'").fetchone()
        return row is not None and row[0] == "true"


    # Link each new event to the previously committed audit hash
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


    # Return events in chain order for export and inspection
    def audit(self) -> list[dict]:
        with self.connect() as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM audit ORDER BY sequence")]


    # Detect broken links or altered event content in the retained chain
    def verify(self) -> dict:
        return self.verify_rows(self.audit())


    # Bind a checkpoint to the exact exported extent, including a valid historical prefix
    @staticmethod
    def verify_rows(rows: list[dict]) -> dict:
        previous = "0" * 64
        for sequence, row in enumerate(rows, start=1):
            if not isinstance(row, dict) or not isinstance(row.get("payload"), str):
                raise Refused("Audit chain does not match its stored events", 409)
            digest = hashlib.sha256((previous + "\n" + row["payload"]).encode()).hexdigest()
            if row.get("sequence") != sequence or row.get("previous_hash") != previous or row.get("hash") != digest:
                raise Refused("Audit chain does not match its stored events", 409)
            previous = digest
        # This head needs an independent archive to detect deletion or a fully rewritten chain
        return {"events": len(rows), "head": previous, "format": fingerprint({"version": 1})}
