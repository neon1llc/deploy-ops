# src/deploy_ops/client.py - Keep bearer credentials out of files, logs, and redirects.

import hashlib
import json
import os
import tempfile
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx

from .errors import Refused


def private_json(path: Path, value: object) -> None:
    """Replace a local record atomically with owner-only permissions."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=".deploy_ops-")
    try:
        with os.fdopen(descriptor, "w") as handle:
            json.dump(value, handle, indent=2, allow_nan=False)
            handle.write("\n")
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def github_token(audience: str) -> str:
    """Ask the current Actions job for a fresh token with a specific audience."""
    endpoint = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL", "")
    credential = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "")
    parsed = urlparse(endpoint)
    if (not credential or parsed.scheme != "https" or not parsed.hostname
            or not parsed.hostname.endswith(".actions.githubusercontent.com")):
        raise Refused("Run this command in GitHub Actions with id-token: write", 401)
    query = [(key, value) for key, value in parse_qsl(parsed.query) if key != "audience"]
    endpoint = urlunparse(parsed._replace(query=urlencode([*query, ("audience", audience)])))
    try:
        with httpx.Client(timeout=10, follow_redirects=False) as client:
            response = client.get(endpoint, headers={"Authorization": "Bearer " + credential})
            response.raise_for_status()
            value = response.json()["value"]
            if not isinstance(value, str) or len(value) > 20000:
                raise ValueError("Unexpected token response")
            return value
    except (httpx.HTTPError, ValueError, KeyError) as error:
        raise Refused("Could not obtain a GitHub workflow credential", 503) from error


class Client:
    """Allow plain HTTP only for a local loopback controller."""

    def __init__(self, url: str | None = None, audience: str | None = None):
        self.url = (url or os.environ.get("DEPLOY_OPS_URL", "http://127.0.0.1:8787")).rstrip("/")
        self.audience = audience or os.environ.get("DEPLOY_OPS_AUDIENCE", "deploy-ops")
        parsed = urlparse(self.url)
        if (parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.scheme not in ("https", "http")
                or (parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"))):
            raise Refused("Use an HTTPS controller URL or a local loopback address", 422)

    def call(self, method: str, path: str, body: dict | None = None,
             operator: bool = False, execution_token: str | None = None):
        credential = execution_token
        if credential is None:
            credential = os.environ.get("DEPLOY_OPS_ADMIN_TOKEN", "") if operator else github_token(self.audience)
        if not credential:
            raise Refused("DEPLOY_OPS_ADMIN_TOKEN is required for operator commands", 401)
        try:
            with httpx.Client(timeout=10, follow_redirects=False) as client:
                response = client.request(method, self.url + path, json=body,
                                          headers={"Authorization": "Bearer " + credential})
                if not 200 <= response.status_code < 300:
                    try:
                        detail = response.json().get("detail", "Controller refused the operation")
                    except ValueError:
                        detail = "Controller returned an unexpected response"
                    raise Refused(str(detail), response.status_code)
                return response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise Refused("Controller unavailable. No new execution is authorized", 503) from error
