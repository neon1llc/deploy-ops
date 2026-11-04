# src/deploy_ops/console.py - Serve an authenticated loopback-only operator interface.


import hmac
from contextlib import asynccontextmanager
import os
import secrets
import threading
from importlib.resources import files
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .errors import Refused


# Bound console input without granting authority through unknown fields
class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Release selection and authority windows contain no provider credential values
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

    # Draft editing carries exact source identity for conflict detection
    configuration: str = Field(default="", max_length=30000)
    receipts: dict[str, str] = Field(default_factory=dict)
    section: str = ""
    name: str = ""
    values: dict = Field(default_factory=dict)
    source_hash: str = ""
    scope: str = ""
    edit_environment: str = ""
    field: str = ""

    # Incident decisions require their current version rather than a transport receipt
    release_id: str = ""
    owner: str = Field(default="", max_length=500)
    interval_seconds: int = Field(default=900, ge=60, le=86400)
    limit: int = Field(default=3, ge=1, le=5)
    version: int | None = Field(default=None, ge=1)

    # Paging settings contain references only and use independent revision checks
    paging_configuration: dict | None = None
    revision: int = Field(default=0, ge=0)
    context: dict[str, str] = Field(default_factory=dict)
    binding: str = ""
    allow_duplicate: bool = False

    # Preview ownership and cleanup retain their separate lifecycle boundaries
    preview_owner: str = Field(default="", max_length=200)
    preview_hours: int = Field(default=1, ge=1, le=72)
    retain_preview_data: bool = False
    confirm_removed: bool = False


# Share one engine behind authenticated loopback routes and worker coordination
def create_app(engine, token: str, port: int = 8765) -> FastAPI:
    if len(token) < 32:
        raise ValueError("Console tokens need at least 32 characters")
    from .delivery.serving import Sessions
    serving = Sessions(port)

    # End only this console process's owned content sessions
    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            serving.close()

    app = FastAPI(title="DeployOps local console", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    assets = Path(str(files("deploy_ops").joinpath("ui")))
    origins = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    workers = set()
    mutex = threading.Lock()


    # Reject foreign hosts and origins before adding private response headers
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


    # Compare the operator credential without persisting it in browser state
    def operator(request: Request):
        expected = "Bearer " + token
        if not hmac.compare_digest(request.headers.get("authorization", ""), expected):
            raise HTTPException(status_code=401, detail="Unlock the console with its operator token")


    # Return deliberately safe refusal text with its intended HTTP status
    @app.exception_handler(Refused)
    async def refused(request, error):
        return JSONResponse({"detail": str(error)}, status_code=error.status)


    # Allow only one local worker per release
    def start(release_id):
        with mutex:
            if release_id in workers:
                raise Refused("This release already has an active local worker")
            workers.add(release_id)


        # Record safe worker failures and always release local worker ownership
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


    # Keep operator input fixed and reuse the existing authenticated loopback boundary
    @app.get("/api/operator/{action}", dependencies=[Depends(operator)])
    def operator_read(action: str, identifier: str = ""):
        from .delivery.operator import Operator
        if action not in {"status", "requests", "request", "delegations", "audit"}:
            raise Refused("Unknown operator observation", 404)
        return Operator(engine).call(action, identifier)


    # Validate decision bodies without echoing rejected values into responses
    @app.post("/api/operator/{action}", dependencies=[Depends(operator)])
    async def operator_write(action: str, request: Request):
        from .delivery.operator import Operator
        try:
            body = await request.json()
        except ValueError:
            raise Refused("Invalid operator action body", 422) from None
        if not isinstance(body, dict):
            raise Refused("Invalid operator action body", 422)
        if action not in {"decision", "resolve", "pause", "resume", "revoke"}:
            raise Refused("Unknown operator action", 404)
        if set(body) - {"identifier", "values"} or not isinstance(body.get("values", {}), dict):
            raise Refused("Invalid operator action fields", 422)
        identifier = body.get("identifier", "")
        if not isinstance(identifier, str):
            raise Refused("Invalid operator identifier", 422)
        return Operator(engine).call(action, identifier, body.get("values", {}))


    # Read back process-owned listeners without creating or adopting a session
    @app.get("/api/serving", dependencies=[Depends(operator)])
    def serving_status():
        return serving.list()


    # Fixed serving inputs cannot select host paths, commands or another owner's process
    @app.post("/api/serving/{action}", dependencies=[Depends(operator)])
    async def serving_action(action: str, request: Request):
        try:
            body = await request.json()
        except ValueError:
            raise Refused("Invalid serving request", 422) from None
        if not isinstance(body, dict):
            raise Refused("Invalid serving fields", 422)
        if action == "start":
            if set(body) - {"release", "component", "port"} or not all(isinstance(body.get(key), str) for key in ("release", "component")):
                raise Refused("Choose a release, component and optional port", 422)
            return serving.start(engine, body["release"], body["component"], body.get("port", 0))
        if (action == "stop" and set(body) == {"id"} and isinstance(body["id"], str)
                and len(body["id"]) == 32 and all(char in "0123456789abcdef" for char in body["id"])):
            return serving.stop(body["id"])
        raise Refused("Unknown serving action or fields", 422)


    # Load the packaged console without granting API access
    @app.get("/")
    def index():
        return FileResponse(assets / "index.html")


    # Serve only known UI assets rather than arbitrary package files
    @app.get("/assets/{name}")
    @app.get("/{name}")
    def asset(name: str):
        if name not in ("app.js", "shell.js", "operator.js", "local-settings.js", "paging.js", "isolated.js", "style.css", "appearance.mjs", "theme.mjs"):
            raise HTTPException(404)
        return FileResponse(assets / name)


    # Require the same explicit operator registration used by the application receipt gate
    def isolated_route():
        from .isolated import IsolatedGate
        if not isinstance(engine.gate, IsolatedGate):
            raise Refused("Configure an isolated operator grant before using local GCP infrastructure", 403)
        return engine.gate.route


    # Read public recipe and approval evidence without inspecting credentials or cloud resources
    @app.get("/api/isolated", dependencies=[Depends(operator)])
    def isolated_status():
        from .isolated import IsolatedGate
        if not isinstance(engine.gate, IsolatedGate):
            return {"enabled": False, "available": False, "reason": "No isolated operator grant configured",
                    "requests": [], "active": [], "destroy_supported": False}
        result = isolated_route().status()
        with mutex:
            result["active"] = [name.removeprefix("isolated:") for name in workers if name.startswith("isolated:")]
        return result


    # Freeze the current public packet for a separate planning approval
    @app.post("/api/isolated/prepare", dependencies=[Depends(operator)])
    def isolated_prepare(data: Input):
        current_contract()
        return isolated_route().prepare()


    # Keep exact approval distinct from asynchronously starting an already approved operation
    @app.post("/api/isolated/requests/{request_id}/{action}", dependencies=[Depends(operator)])
    def isolated_action(request_id: str, action: str, data: Input):
        current_contract()
        route = isolated_route()
        request = route.request(request_id)
        if request["digest"] != data.digest:
            raise Refused("Isolated request digest changed; inspect the current request")
        if action == "reconcile":
            with mutex:
                if "isolated:" + request_id in workers:
                    raise Refused("Wait for the active isolated worker before recording an inspection")
                return route.reconcile(request_id, data.digest, data.note)
        if action == "approve":
            return route.approve(request_id, data.digest)
        if action != "execute":
            raise Refused("Unknown isolated infrastructure action", 404)
        route.current()
        if request["status"] != "approved":
            raise Refused("Approve the exact isolated request before execution")
        key = "isolated:" + request_id
        with mutex:
            if any(name.startswith("isolated:") for name in workers):
                raise Refused("An isolated infrastructure worker is already active")
            workers.add(key)

        # Persist uncertainty in the controller while keeping diagnostics out of browser responses
        def work():
            try:
                route.execute(request_id)
            except Exception as error:
                route.failed(request_id, error)
            finally:
                with mutex:
                    workers.discard(key)
        threading.Thread(target=work, daemon=True, name="deploy-ops-isolated-" + request_id[:8]).start()
        return {"request_id": request_id, "started": True}


    # Expose the current draft model without credential references
    @app.get("/api/workspace", dependencies=[Depends(operator)])
    def workspace():
        from .delivery.configuration import inspect
        source = engine.path.read_text()
        return {**inspect(source), "toml": source,
                "cloud_access": getattr(engine.gate, "access_error", "Remote controller owns authentication"),
                "mode": "remote-controller" if engine.gate.__class__.__name__ == "RemoteGate" else "local-operator"}


    # Validate drafts separately from conflict-checked local saves
    @app.post("/api/config/{action}", dependencies=[Depends(operator)])
    def configure(action: str, data: Input):
        nonlocal engine
        from .delivery.engine import Engine
        from .delivery.configuration import add, edit, inspect, patch, save, source_hash, validate
        if action in {"edit", "reset"}:
            if (action == "reset" and not data.field) or (action == "edit" and data.field):
                raise Refused("Choose a field only for override reset", 422)
            return edit(data.configuration, data.name, data.scope, data.values, data.edit_environment, data.field)
        if action == "inspect":
            return inspect(data.configuration)
        if action == "patch":
            return patch(data.configuration, data.section, data.name, data.values)
        if action == "add":
            return {**add(data.configuration, data.section, data.name, data.values),
                    "saved_source_hash": source_hash(engine.path.read_text())}
        if action in {"validate", "diff"}:
            return validate(data.configuration, engine.path.read_text())
        if action != "save":
            raise Refused("Unknown configuration action", 404)

        # The console mutex also excludes its workers while the shared owner checks other processes
        with mutex:
            result = save(engine, data.configuration, data.source_hash, bool(workers))
            engine = Engine(engine.path, engine.state)
        return result


    # Expose paging drafts and per-destination evidence without credential values
    @app.get("/api/paging/{view}", dependencies=[Depends(operator)])
    def paging_view(view: str):
        from .delivery import paging
        if view == "config":
            return paging.config(engine.ledger)
        if view == "status":
            return paging.status(engine.ledger)
        raise Refused("Unknown paging view", 404)


    # Keep draft activation and actual sending as separately named operations
    @app.post("/api/paging/{action}", dependencies=[Depends(operator)])
    def paging_action(action: str, data: Input):
        from .delivery import paging
        if action == "save":
            return paging.save_config(engine.ledger, data.paging_configuration, data.revision)
        if action == "activate":
            return paging.activate(engine.ledger, data.revision)
        if action == "preview":
            return paging.preview(engine.ledger, data.context, data.paging_configuration)
        if action == "test":
            return paging.test_destination(engine.ledger, data.name, data.revision)
        if action == "drain":
            return paging.drain(engine.ledger)
        if action == "readback":
            return paging.readback(engine.ledger, data.binding, data.version)
        if action in ("acknowledge", "resolve", "retry"):
            return paging.action(engine.ledger, data.binding, action, data.version, data.allow_duplicate)
        raise Refused("Unknown paging operation", 404)


    # Inspect delivery state without sending another notification
    @app.get("/api/notifications", dependencies=[Depends(operator)])
    def notifications():
        from .delivery.notify import status
        return status(engine.ledger)

    # Provider readback and change-set cleanup never replay deployment commands
    @app.post("/api/releases/{release_id}/provider/{action}", dependencies=[Depends(operator)])
    def provider(release_id: str, action: str, data: Input):
        if action not in {"inspect", "discard"} or not data.component:
            raise Refused("Choose a component and supported provider operation", 422)
        return engine.provider_status(release_id, data.component, action == "discard")


    # Read incident state independently of release outcomes
    @app.get("/api/incidents", dependencies=[Depends(operator)])
    def incidents():
        from .delivery.incidents import list_incidents
        return list_incidents(engine.ledger)


    # Create an incident through the durable shared lifecycle
    @app.post("/api/incidents", dependencies=[Depends(operator)])
    def open_incident(data: Input):
        from .delivery.incidents import open_incident
        return open_incident(engine.ledger, data.release_id, data.owner, data.note, data.interval_seconds, data.limit)


    # Advance only the explicitly configured escalation schedule
    @app.post("/api/incidents/tick", dependencies=[Depends(operator)])
    def tick_incidents():
        from .delivery.incidents import tick
        return tick(engine.ledger)


    # Apply a versioned incident decision through the shared owner
    @app.post("/api/incidents/{incident_id}/{action}", dependencies=[Depends(operator)])
    def incident_action(incident_id: str, action: str, data: Input):
        from .delivery.incidents import transition
        return transition(engine.ledger, incident_id, action, data.version, data.note)


    # List retained releases for the authenticated operator
    @app.get("/api/releases", dependencies=[Depends(operator)])
    def releases():
        return engine.ledger.list()


    # Combine release evidence with currently available follow-up actions
    @app.get("/api/releases/{release_id}", dependencies=[Depends(operator)])
    def detail(release_id: str):
        release = engine.ledger.get(release_id)
        cleanup = release.get("cleanup", {})

        # Read current approval without exposing controller identities or execution credentials
        request_statuses = {}
        for name, node in release["nodes"].items():
            if node.get("status") != "waiting" or not node.get("request_id"):
                continue
            try:
                request = engine.gate.status(node["request_id"])
                request_statuses[name] = {"status": request["status"], "digest": request["digest"]}
            except Exception:
                request_statuses[name] = {"status": "unavailable", "digest": None}
        return {"serving_sessions": [item for item in serving.list() if item["release"] == release_id],
                "release": release, "events": engine.ledger.events(release_id),
                "request_statuses": request_statuses,
                "cleanup_request": engine.gate.status(cleanup["request_id"]) if cleanup.get("request_id") else None,
                "artifact_actions": engine.artifact_actions(release)}


    # Describe the current release graph without preparing provider changes
    @app.post("/api/plan", dependencies=[Depends(operator)])
    def plan(data: Input):
        current_contract()
        return engine.describe(data.environment, data.component, not data.only)


    # Refuse execution when an external edit changed the loaded workspace
    def current_contract():
        from .delivery.configuration import inspect
        if inspect(engine.path.read_text())["digest"] != engine.spec.digest:
            raise Refused("Workspace changed outside this console. Review and save it here, or restart before preparing releases")


    # Create a release under the same mutex used for configuration saves
    @app.post("/api/releases", dependencies=[Depends(operator)])
    def run(data: Input):
        with mutex:
            current_contract()
            release = engine.create(data.environment, data.component, not data.only,
                                    promote=data.origin, rollback=data.rollback, image=data.image, receipts=data.receipts,
                                    preview_owner=data.preview_owner, preview_hours=data.preview_hours,
                                    retain_preview_data=data.retain_preview_data)
        start(release["id"])
        return release


    # Keep approval, cancellation, observation and recovery distinct
    @app.post("/api/releases/{release_id}/{action}", dependencies=[Depends(operator)])
    def action(release_id: str, action: str, data: Input):
        if action == "observe":
            return engine.observe(release_id)
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


    # List isolated preview ownership and cleanup state
    @app.get("/api/previews", dependencies=[Depends(operator)])
    def previews():
        from .delivery.previews import list_previews
        return list_previews(engine)


    # Separate cleanup review from execution and uncertain-outcome reconciliation
    @app.post("/api/previews/{release_id}/{action}", dependencies=[Depends(operator)])
    def preview_action(release_id: str, action: str, data: Input):
        from .delivery.previews import prepare_cleanup, approve_cleanup, execute_cleanup, reconcile_cleanup
        if action == "prepare-cleanup":
            return prepare_cleanup(engine, release_id)
        if action == "approve-cleanup":
            return approve_cleanup(engine, release_id, data.digest)
        if action == "cleanup":
            return execute_cleanup(engine, release_id)
        if action == "reconcile":
            return reconcile_cleanup(engine, release_id, data.note, data.confirm_removed)
        raise Refused("Unknown preview action", 404)


    # Permit bounded local delegation only under the current workspace contract
    @app.post("/api/delegations", dependencies=[Depends(operator)])
    def delegate(data: Input):
        if engine.gate.__class__.__name__ != "LocalGate":
            raise Refused("Manage remote delegation through the separate operator controller", 403)
        with mutex:
            current_contract()
            return engine.gate.delegate(data.environment, data.hours, data.uses)

    return app


# Create or reuse an owner-only token file for the loopback console
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
