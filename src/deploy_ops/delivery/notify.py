# src/deploy_ops/delivery/notify.py - Deliver durable events without giving repositories notification credentials.

import hashlib
import hmac
import json
import os
from urllib.parse import urlsplit

import httpx

from ..errors import Refused
from .ledger import Ledger


def flush(ledger: Ledger, url: str, kind: str = "json", client=None) -> dict:
    """Use at-least-once delivery. Receivers should deduplicate the stable event ID."""
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise Refused("Notification endpoints must be HTTPS without embedded credentials", 422)
    if kind not in ("json", "slack", "ntfy"):
        raise Refused("Choose json, slack, or ntfy notification format", 422)
    sent, failed = 0, 0
    owned = client is None
    client = client or httpx.Client(timeout=10, follow_redirects=False)
    try:
        with ledger.store.connect() as connection:
            rows = connection.execute("SELECT * FROM notifications WHERE sent=0 AND attempts<10 ORDER BY id LIMIT 50").fetchall()
        for row in rows:
            record = json.loads(row["payload"])
            event_id = hashlib.sha256((record["release_id"] + ":" + str(row["id"])).encode()).hexdigest()
            record["event_id"] = event_id
            text = f"DeployOps: {record['event']} for release {record['release_id']}"
            payload = {"text": text} if kind == "slack" else record
            body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
            headers = {"Content-Type": "application/json", "Idempotency-Key": event_id}
            secret = os.environ.get("DEPLOY_OPS_NOTIFY_SECRET", "")
            if kind == "ntfy":
                body = text.encode()
                headers["Content-Type"] = "text/plain"
                headers["Title"] = "DeployOps"
                headers["Priority"] = "high" if record["event"] in ("release.failed", "release.uncertain") else "default"
            if secret:
                headers["X-DeployOps-Signature"] = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
            token = os.environ.get("DEPLOY_OPS_NOTIFY_TOKEN")
            if token:
                headers["Authorization"] = "Bearer " + token
            try:
                response = client.post(url, content=body, headers=headers)
                ok = 200 <= response.status_code < 300
            except httpx.HTTPError:
                ok = False
            with ledger.store.transaction() as connection:
                connection.execute("UPDATE notifications SET sent=?,attempts=attempts+1 WHERE id=?", (int(ok), row["id"]))
            sent += int(ok)
            failed += int(not ok)
    finally:
        if owned:
            client.close()
    return {"sent": sent, "failed": failed, "delivery": "at-least-once", "attempt_limit": 10}
