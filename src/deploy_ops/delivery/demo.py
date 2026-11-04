# src/deploy_ops/delivery/demo.py - Exercise real local deployment and HTTP verification without cloud access.


import functools
import json
import threading
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ..errors import Refused
from .engine import Engine
from .scaffold import create


# Keep the demonstration's result separate from HTTP access logs
class QuietHandler(SimpleHTTPRequestHandler):

    # Suppress the standard handler's diagnostic output
    def log_message(self, format, *args):
        pass


# Drive the explicitly requested demo through its normal approval gates
def finish(engine: Engine, release: dict) -> dict:
    """Approve only this explicit demonstration's current exact requests."""
    while release["status"] not in ("succeeded", "failed", "uncertain", "cancelled"):
        release = engine.advance(release["id"])
        if release["status"] == "waiting":
            for name, node in release["nodes"].items():
                if node["status"] == "waiting":
                    engine.approve(release["id"], name, node["request_digest"])
    if release["status"] != "succeeded":
        raise Refused("Local demonstration failed: " + release.get("reason", release["status"]))
    return release


# Exercise app-only reuse and promotion before checking the served page
def run(directory: Path) -> dict:
    project = create(directory)
    engine = Engine(project)
    first = finish(engine, engine.create("development"))
    app_only = finish(engine, engine.create("development", "web", False))
    finish(engine, engine.create("staging", "foundation"))
    promoted = finish(engine, engine.create("staging", "web", False, promote=app_only["id"]))
    # Serve the promoted artifact through a disposable loopback HTTP server
    site = Path(promoted["nodes"]["web"]["outputs"]["path"])
    handler = functools.partial(QuietHandler, directory=str(site))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/", timeout=5) as response:
            if response.status != 200 or b"Deployed with DeployOps" not in response.read():
                raise Refused("Deployed page failed its HTTP check")
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    return {"status": "succeeded", "directory": str(directory.resolve()),
            "first_release": first["id"], "application_only_release": app_only["id"],
            "promotion_release": promoted["id"], "http_check": "200 with expected page content",
            "artifact_preserved": app_only["nodes"]["web"]["outputs"]["digest"] == promoted["nodes"]["web"]["outputs"]["digest"]}
