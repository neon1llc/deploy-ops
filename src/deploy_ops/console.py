# src/deploy_ops/console.py - Serve an authenticated loopback-only operator interface.

import hmac
import os
import secrets
import threading
import tomllib
import time
from importlib.resources import files
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .errors import Refused


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")
    environment: str = "development"
    component: str | None = None
    only: bool = False
    image: str | None = None
    digest: str = ""
    hours: int = Field(default=1, ge=1, le=12)
    uses: int = Field(default=1, ge=1, le=100)
    origin: str | None = None
    rollback: bool = False
    note: str = Field(default="", max_length=500)
    configuration: str = Field(default="", max_length=30000)


def create_app(engine, token: str, port: int = 8765) -> FastAPI:
    if len(token) < 32:
        raise ValueError("Console tokens need at least 32 characters")
    app = FastAPI(title="DeployOps local console", docs_url=None, redoc_url=None, openapi_url=None)
    assets = Path(str(files("deploy_ops").joinpath("ui")))
    origins = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    workers = set()
    mutex = threading.Lock()

    @app.middleware("http")
    async def boundary(request, call_next):
        if request.headers.get("host") not in hosts:
            return JSONResponse({"detail": "Use the loopback console address"}, status_code=403)
        origin = request.headers.get("origin")
        if origin and origin not in origins:
            return JSONResponse({"detail": "Cross-origin calls are not allowed"}, status_code=403)
        try:
            if int(request.headers.get("content-length", "0")) > 32768:
                return JSONResponse({"detail": "Request is too large"}, status_code=413)
        except ValueError:
            return JSONResponse({"detail": "Invalid request length"}, status_code=400)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    def operator(request: Request):
        expected = "Bearer " + token
        if not hmac.compare_digest(request.headers.get("authorization", ""), expected):
            raise HTTPException(status_code=401, detail="Unlock the console with its operator token")

    @app.exception_handler(Refused)
    async def refused(request, error):
        return JSONResponse({"detail": str(error)}, status_code=error.status)

    def start(release_id):
        with mutex:
            if release_id in workers:
                raise Refused("This release already has an active local worker")
            workers.add(release_id)
        def work():
            try:
                engine.advance(release_id)
            except Exception as error:
                detail = str(error) if isinstance(error, Refused) else type(error).__name__
                engine.ledger.event(release_id, "worker.refused", {"reason": detail})
            finally:
                with mutex:
                    workers.discard(release_id)
        threading.Thread(target=work, daemon=True, name="deploy-ops-" + release_id[:8]).start()

    @app.get("/")
    def index():
        return FileResponse(assets / "index.html")

    @app.get("/assets/{name}")
    def asset(name: str):
        if name not in ("app.js", "style.css"):
            raise HTTPException(404)
        return FileResponse(assets / name)

    @app.get("/api/workspace", dependencies=[Depends(operator)])
    def workspace():
        return {"configuration": engine.spec.model_dump(), "digest": engine.spec.digest,
                "toml": engine.path.read_text(),
                "mode": "remote-controller" if engine.gate.__class__.__name__ == "RemoteGate" else "local-operator"}

    @app.post("/api/config/{action}", dependencies=[Depends(operator)])
    def configure(action: str, data: Input):
        nonlocal engine
        from .delivery.spec import Workspace
        from .delivery.engine import Engine
        from pydantic import ValidationError
        try:
            proposed = Workspace.model_validate(tomllib.loads(data.configuration))
        except (ValueError, ValidationError) as error:
            raise Refused(str(error), 422) from error
        if action == "validate":
            return {"digest": proposed.digest, "order": proposed.order(), "status": "valid"}
        if action != "save" or engine.gate.__class__.__name__ != "LocalGate":
            raise Refused("Only local operators may save a local workspace here", 403)
        with mutex:
            active = any(item["status"] not in ("succeeded", "failed", "cancelled") for item in engine.ledger.list())
            if workers or active:
                raise Refused("Finish, cancel, or reconcile outstanding releases before changing configuration")
            backup = engine.state / "previous-workspace.toml"
            backup.write_text(engine.path.read_text())
            backup.chmod(0o600)
            pending = engine.path.with_suffix(".pending")
            if pending.exists():
                raise Refused("A pending configuration file already exists. Inspect it first")
            descriptor = os.open(pending, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "w") as stream:
                stream.write(data.configuration)
            os.replace(pending, engine.path)
            with engine.ledger.store.transaction() as connection:
                engine.ledger.store.event(connection, int(time.time()), "local-operator", "workspace.updated", proposed.name,
                                          {"old_digest": engine.spec.digest, "new_digest": proposed.digest})
            engine = Engine(engine.path, engine.state)
        return {"digest": engine.spec.digest, "status": "saved", "reload": True}

    @app.get("/api/releases", dependencies=[Depends(operator)])
    def releases():
        return engine.ledger.list()

    @app.get("/api/releases/{release_id}", dependencies=[Depends(operator)])
    def detail(release_id: str):
        return {"release": engine.ledger.get(release_id), "events": engine.ledger.events(release_id)}

    @app.post("/api/plan", dependencies=[Depends(operator)])
    def plan(data: Input):
        return engine.describe(data.environment, data.component, not data.only)

    @app.post("/api/releases", dependencies=[Depends(operator)])
    def run(data: Input):
        release = engine.create(data.environment, data.component, not data.only,
                                promote=data.origin, rollback=data.rollback, image=data.image)
        start(release["id"])
        return release

    @app.post("/api/releases/{release_id}/{action}", dependencies=[Depends(operator)])
    def action(release_id: str, action: str, data: Input):
        if action == "approve":
            return engine.approve(release_id, data.component, data.digest)
        if action == "resume":
            start(release_id)
            return {"started": release_id}
        if action == "cancel":
            return engine.cancel(release_id)
        if action == "recover":
            with mutex:
                if release_id in workers:
                    raise Refused("Stop the active worker before reconciling")
            return engine.recover(release_id, data.note)
        raise Refused("Unknown release action", 404)

    @app.post("/api/delegations", dependencies=[Depends(operator)])
    def delegate(data: Input):
        if engine.gate.__class__.__name__ != "LocalGate":
            raise Refused("Manage remote delegation through the separate operator controller", 403)
        return engine.gate.delegate(data.environment, data.hours, data.uses)

    return app


def serve(engine, port: int, token_file: Path):
    """Keep credentials out of browser storage, query strings, and terminal output."""
    import uvicorn
    if not 1024 <= port <= 65535:
        raise Refused("Choose a local port from 1024 to 65535", 422)
    if not token_file.exists():
        token_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(token_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write(secrets.token_urlsafe(32) + "\n")
    if os.name == "posix" and token_file.stat().st_mode & 0o077:
        raise Refused("Restrict the console token file to mode 0600")
    token = token_file.read_text().strip()
    print(f"DeployOps: http://127.0.0.1:{port} | operator token: {token_file.resolve()}")
    uvicorn.run(create_app(engine, token, port), host="127.0.0.1", port=port, access_log=False)
