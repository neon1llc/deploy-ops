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
from .config import fingerprint


# Bound console input without granting authority through unknown fields
class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Bind later incidents and explicit handoff to displayed immutable references
    observation_sequence: int | None = Field(default=None, ge=1, strict=True)
    observation_digest: str = Field(default="", pattern=r"^(|[0-9a-f]{64})$")
    route_digest: str = Field(default="", pattern=r"^(|[0-9a-f]{64})$")

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
    operation_id: str = ""
    backup_directory: str = Field(default='', max_length=4096)
    workspace_directory: str = Field(default='', max_length=4096)
    state_directory: str = Field(default='', max_length=4096)
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


# Keep native artifact observation options strict and separate from deployment authority
class ArtifactInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    component: str = Field(min_length=1, max_length=48)
    output_directory: str = Field(min_length=1, max_length=4096)
    inventory: bool = True
    vulnerabilities: bool = False
    allow_network: bool = False
    timeout_seconds: int = Field(default=60, ge=10, le=300)


# No browser input can select a route URL, host path, process or arbitrary native command
class RolloutInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    component: str | None = Field(default=None, max_length=48)
    digest: str = Field(default='', max_length=128)
    note: str = Field(default='', max_length=500)


class SignatureInspectionInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    component: str = Field(min_length=1, max_length=48)
    output_directory: str = Field(min_length=1, max_length=4096)


class SignatureInput(SignatureInspectionInput):
    selected_file: str = Field(min_length=1, max_length=2048)
    key_file: str = Field(min_length=1, max_length=4096)
    bundle_file: str = Field(min_length=1, max_length=4096)
    expected_key_sha256: str | None = Field(default=None, pattern=r'^[0-9a-f]{64}$')
    timeout_seconds: int = Field(default=60, ge=10, le=300)


class KeyInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    key_file: str = Field(min_length=1, max_length=4096)


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
            if set(body) - {"release", "component", "port", "mode"} or not all(isinstance(body.get(key), str) for key in ("release", "component")):
                raise Refused("Choose a release, component and optional port", 422)
            return serving.start(engine, body["release"], body["component"], body.get("port", 0), body.get('mode', 'revision'))
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
        if name not in ("app.js", "shell.js", "operator.js", "local-settings.js", "paging.js", "isolated.js", "backups.js", "artifacts.js", "signatures.js", "style.css", "appearance.mjs", "theme.mjs"):
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
        from .delivery.configuration import inspect, read_source
        from .delivery.docker import readiness
        source = read_source(engine.path)
        return {**inspect(source), "toml": source,
                "cloud_access": getattr(engine.gate, "access_error", "Remote controller owns authentication"),
                "docker_readiness": readiness(engine.path),
                "mode": "remote-controller" if engine.gate.__class__.__name__ == "RemoteGate" else "local-operator"}

    # Read selected tool/socket metadata only; this endpoint never contacts a daemon
    @app.get('/api/docker/readiness', dependencies=[Depends(operator)])
    def docker_readiness():
        from .delivery.docker import readiness
        return readiness(engine.path)


    # Explicit native collection reads only the selected frozen subject; it cannot deploy or approve
    @app.get('/api/artifacts/options', dependencies=[Depends(operator)])
    def artifact_options():
        from .delivery import artifacts
        return artifacts.options()


    # Browser paths need an explicit local context and can never become remote key or bundle references
    def signature_path(value):
        if not (value.startswith('/') or value.startswith('~/')):
            raise Refused('Choose an absolute local path or a path beginning with ~/', 422)
        return Path(value)


    @app.get('/api/signatures/options', dependencies=[Depends(operator)])
    def signature_options():
        from .delivery import signatures
        return signatures.options()


    @app.post('/api/signatures/key-info', dependencies=[Depends(operator)])
    def signature_key(body: KeyInput):
        from .delivery import signatures
        return signatures.key_info(signature_path(body.key_file))


    @app.get('/api/releases/{identifier}/signatures/files', dependencies=[Depends(operator)])
    def signature_files(identifier: str, component: str, query: str = '', offset: int = 0, limit: int = 100):
        from .delivery import signatures
        return signatures.files(engine.state, identifier, component, query, offset, limit)


    @app.post('/api/releases/{identifier}/signatures/verify', dependencies=[Depends(operator)])
    def signature_verify(identifier: str, body: SignatureInput):
        from .delivery import signatures
        profile = signatures.Profile(expected_key_sha256=body.expected_key_sha256, timeout_seconds=body.timeout_seconds)
        return signatures.verify(engine.state, identifier, body.component, body.selected_file,
                                 signature_path(body.key_file), signature_path(body.bundle_file),
                                 signature_path(body.output_directory), profile, engine.path.parent)


    @app.post('/api/releases/{identifier}/signatures/inspect', dependencies=[Depends(operator)])
    def signature_inspect(identifier: str, body: SignatureInspectionInput):
        from .delivery import signatures
        result = signatures.inspect(signature_path(body.output_directory))
        if result['report']['release_id'] != identifier or result['report']['component'] != body.component:
            raise Refused('This signature report belongs to another release or component', 409)
        return result


    @app.post('/api/releases/{identifier}/artifacts/{action}', dependencies=[Depends(operator)])
    def artifact_collect(identifier: str, action: str, body: ArtifactInput):
        from .delivery import artifacts
        if not (body.output_directory.startswith('/') or body.output_directory.startswith('~/')):
            raise Refused('Choose an absolute private output path or ~/', 422)
        if action == 'inspect':
            result = artifacts.inspect(Path(body.output_directory))
            if result['report']['release_id'] != identifier or result['report']['component'] != body.component:
                raise Refused('This report belongs to another release or component; keep its original identity', 409)
            return result
        if action != 'collect':
            raise Refused('Choose collect or inspect for artifact evidence', 422)
        profile = artifacts.Profile(**body.model_dump(exclude={'component', 'output_directory'}))
        return artifacts.collect(engine.state, identifier, body.component, Path(body.output_directory), profile, engine.path.parent)


    # Offer private paths and bounds without opening a backup or changing local state
    @app.get('/api/backups/options', dependencies=[Depends(operator)])
    def backup_options():
        from .delivery import backup
        return backup.options(engine.path, engine.state)


    # Share CLI capture/inspection/restore semantics; imported state never gains execution authority
    @app.post('/api/backups/{action}', dependencies=[Depends(operator)])
    def backups(action: str, data: Input):
        from .delivery import backup

        # A browser has no filesystem working-directory context; require an absolute or home-based selection
        def selected(value):
            path = Path(value)
            if not value or (not path.is_absolute() and not value.startswith('~/')):
                raise Refused('Choose an absolute directory or a path beginning with ~/', 422)
            return backup.location(path)

        try:
            if action == 'inspect-restored':
                return backup.inspect_restored(selected(data.workspace_directory), selected(data.state_directory) if data.state_directory else None)
            target = selected(data.backup_directory)
            if action == 'inspect':
                return backup.inspect(target)
            if action == 'restore':
                if not data.digest:
                    raise Refused('Inspect the backup and provide its exact manifest digest before restoring', 422)
                return backup.restore(target, selected(data.workspace_directory), selected(data.state_directory) if data.state_directory else None,
                                      expected_digest=data.digest)
            if action == 'capture':
                with mutex:
                    if workers:
                        raise Refused('Wait for current workers before coherent private capture')
                    return backup.capture(engine.path, engine.state, target)
            raise Refused('Unknown backup action', 404)
        except OSError:
            raise Refused('Backup operation could not finish. Preserve any incomplete new directory; existing targets were not overwritten', 422) from None


    # Read interrupted Save facts even when an existing console's current TOML was externally damaged
    @app.get("/api/config/recovery", dependencies=[Depends(operator)])
    def config_recovery():
        from .delivery.configuration import save_recovery
        return save_recovery(engine.path, engine.state)


    # Validate drafts separately from conflict-checked local saves
    @app.post("/api/config/{action}", dependencies=[Depends(operator)])
    def configure(action: str, data: Input):
        nonlocal engine
        from .delivery.engine import Engine
        from .delivery.configuration import add, edit, inspect, patch, read_source, save, source_hash, validate
        if action == 'reconcile':
            from .delivery.configuration import reconcile_save
            if engine.gate.__class__.__name__ != 'LocalGate':
                raise Refused('Reconcile a local Save through its local operator context', 403)
            with mutex:
                if workers:
                    raise Refused('Wait for current workers before reconciling Save')
                result = reconcile_save(engine.path, engine.state, data.operation_id, data.source_hash, data.note)
                try:
                    inspect(read_source(engine.path))
                except Refused:
                    return {**result, 'reload': False, 'configuration_valid': False}
                engine = Engine(engine.path, engine.state)
                return {**result, 'reload': True, 'configuration_valid': True}
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
                    "saved_source_hash": source_hash(read_source(engine.path))}
        if action in {"validate", "diff"}:
            return validate(data.configuration, read_source(engine.path))
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
        return open_incident(engine.ledger, data.release_id, data.owner, data.note, data.interval_seconds, data.limit,
                             data.observation_sequence, data.observation_digest)


    # Advance only the explicitly configured escalation schedule
    @app.post("/api/incidents/tick", dependencies=[Depends(operator)])
    def tick_incidents():
        from .delivery.incidents import tick
        return tick(engine.ledger)


    # Preview current routing separately from a recorded opening decision or a dispatch
    @app.get("/api/incidents/{incident_id}/handoff", dependencies=[Depends(operator)])
    def incident_handoff_preview(incident_id: str):
        from .delivery.incidents import handoff_preview
        return handoff_preview(engine.ledger, incident_id)


    # Apply a versioned incident decision through the shared owner
    @app.post("/api/incidents/{incident_id}/{action}", dependencies=[Depends(operator)])
    def incident_action(incident_id: str, action: str, data: Input):
        from .delivery.incidents import handoff, transition
        if action == 'handoff':
            return handoff(engine.ledger, incident_id, data.version, data.route_digest, data.note)
        return transition(engine.ledger, incident_id, action, data.version, data.note)


    # List retained releases for the authenticated operator
    # Source capture evidence does not register a deployment or expose continuation actions
    @app.get('/api/captures', dependencies=[Depends(operator)])
    def capture_records(offset: int = 0, limit: int = 20):
        from .delivery.captures import list_records
        return list_records(engine.state, offset, limit)


    # An exact ID reads only its recorded marker and separately observed registration
    @app.get('/api/captures/{capture_id}', dependencies=[Depends(operator)])
    def capture_record(capture_id: str):
        from .delivery.captures import inspect
        return inspect(engine.state, capture_id)


    # Inspect a selected original or restored location without borrowing this console's execution owner
    @app.post('/api/captures/{capture_id}/inspect', dependencies=[Depends(operator)])
    def retained_capture(capture_id: str, data: Input):
        from .delivery.captures import inspect
        from .delivery.files import location
        selected = Path(data.state_directory)
        if not data.state_directory or not (selected.is_absolute() or data.state_directory.startswith('~/')):
            raise Refused('Choose an absolute retained state directory or a path beginning with ~/', 422)
        return inspect(location(selected), capture_id)


    @app.get("/api/releases", dependencies=[Depends(operator)])
    def releases():
        return engine.ledger.list()


    # Use the same source-free capture and report owner as offline CLI inspection
    @app.get("/api/releases/{release_id}/inspection", dependencies=[Depends(operator)])
    def inspection(release_id: str, report: bool = False):
        from .delivery import evidence
        document = evidence.capture(engine.state, release_id)
        return {'html': evidence.report(document)} if report else document


    # Read actual routing separately from deployment success and current controller initialization
    @app.get('/api/releases/{release_id}/rollout', dependencies=[Depends(operator)])
    def rollout_inspect(release_id: str):
        from .delivery import rollouts
        return rollouts.inspect(engine, release_id)


    # Share all bounded local rollout actions with the foreground CLI
    @app.post('/api/releases/{release_id}/rollout/{action}', dependencies=[Depends(operator)])
    def rollout_action(release_id: str, action: str, data: RolloutInput):
        from .delivery import rollouts
        fields = ({'component'} if action in {'prepare', 'prepare-retirement'} else
                  {'digest'} if action in {'approve', 'approve-retirement', 'cancel', 'cancel-retirement'} else {'note'} if action == 'reconcile' else set())
        if data.model_fields_set - fields:
            raise Refused('Fields do not match the selected rollout action', 422)
        if action in {'prepare', 'prepare-retirement'}:
            current_contract()
            return rollouts.prepare(engine, release_id, data.component, action == 'prepare-retirement')
        if action in {'approve', 'approve-retirement'}:
            return rollouts.approve(engine, release_id, data.digest, action == 'approve-retirement')
        if action in {'cancel', 'cancel-retirement'}:
            return rollouts.cancel(engine, release_id, data.digest, action == 'cancel-retirement')
        if action in {'switch', 'retire'}:
            current_contract()
            return rollouts.execute(engine, release_id, action == 'retire')
        if action == 'check':
            return rollouts.check(engine, release_id)
        if action == 'reconcile':
            return rollouts.reconcile(engine, release_id, data.note)
        raise Refused('Unknown rollout action', 422)


    # Combine release evidence with currently available follow-up actions
    @app.get("/api/releases/{release_id}", dependencies=[Depends(operator)])
    def detail(release_id: str):
        release = engine.ledger.get(release_id)
        cleanup = release.get("cleanup", {})
        from .delivery.previews import recovery_status as cleanup_recovery_status

        # Optional current authority failure cannot hide retained retirement evidence
        cleanup_request = None
        if cleanup.get("request_id"):
            try:
                cleanup_request = engine.gate.status(cleanup["request_id"])
            except Exception:
                cleanup_request = {"status": "unavailable", "digest": None}

        # Read current approval without exposing controller identities or execution credentials
        request_statuses = {}
        for name, node in release["nodes"].items():
            if not node.get("request_id"):
                continue
            try:
                request = engine.gate.status(node["request_id"])
                request_statuses[name] = {"status": request["status"], "digest": request["digest"],
                                          "reason": request.get("reason"), "timing": request.get("timing"),
                                          "execution_blocker": request.get("execution_blocker")}
                if request['status'] in {'pending', 'approved'} and node.get('artifact_digest'):
                    try:
                        from .delivery.engine import require_execution_contract
                        require_execution_contract(engine._reviewed_envelope(release, name))
                    except Refused as error:
                        request_statuses[name]['execution_blocker'] = str(error)
                    except Exception:
                        request_statuses[name]['execution_blocker'] = 'Prepared compatibility is unavailable. Inspect the retained evidence before execution.'
            except Exception:
                request_statuses[name] = {"status": "unavailable", "digest": None}
        sessions = serving.list()
        rollout_requests = {}
        from .delivery import rollouts
        for kind, operation in (('switch', release.get('rollout', {})), ('retirement', release.get('rollout', {}).get('retirement', {}))):
            if operation.get('request_id'):
                try:
                    current = engine.gate.status(operation['request_id'])
                    rollout_requests[kind] = {key: current.get(key) for key in ('status', 'digest', 'timing')}
                except Exception:
                    rollout_requests[kind] = {'status': 'unavailable', 'digest': None}
        events = engine.ledger.events(release_id)
        for event in events:
            if event['name'] == 'release.observed':
                event['reference'] = {'sequence': event['sequence'], 'digest': fingerprint(event['detail'])}
        return {"serving_sessions": [item for item in sessions if item["release"] == release_id
                                      or (item['mode'] == 'target' and item.get('active_release') == release_id)],
                "release": release, "events": events,
                "request_statuses": request_statuses,
                "recovery": engine.recovery_status(release),
                "cleanup_recovery": cleanup_recovery_status(engine, release) if cleanup else None,
                "cleanup_request": cleanup_request,
                'rollout_requests': rollout_requests,
                'rollout_recovery': rollouts.recovery_status(engine, release_id) if release.get('rollout') else None,
                "artifact_actions": engine.artifact_actions(release)}


    # Describe the current release graph without preparing provider changes
    @app.post("/api/plan", dependencies=[Depends(operator)])
    def plan(data: Input):
        current_contract()
        return engine.describe(data.environment, data.component, not data.only)


    # Refuse execution when an external edit changed the loaded workspace
    def current_contract():
        from .delivery.configuration import inspect, read_source, require_saved
        require_saved(engine.path, engine.state)
        if inspect(read_source(engine.path))["digest"] != engine.spec.digest:
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
