# src/deploy_ops/api.py - Expose authenticated controller operations and signed Slack callbacks.

import hmac
import os
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .config import Settings, load
from .errors import Refused
from .identity import GithubVerifier
from .models import Claim, Completion, Decision, DelegationSpec, Proposal, Resolution
from .service import Controller
from .slack import Slack
from .store import Store


class BodyLimit:
    """Bound actual streamed bytes, not just the caller's Content-Length header."""

    def __init__(self, app, limit: int):
        self.app = app
        self.limit = limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > self.limit:
                response = JSONResponse({"detail": "Request exceeds the body limit"}, status_code=413)
                return await response(scope, receive, send)
            if not message.get("more_body", False):
                break
        delivered = False

        async def replay():
            nonlocal delivered
            if delivered:
                return await receive()
            delivered = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        await self.app(scope, replay, send)


def create_app(settings: Settings, admin_token: str, verifier=None) -> FastAPI:
    """Require an operator credential even when Slack is configured."""
    if len(admin_token) < 43:
        raise ValueError("DEPLOY_OPS_ADMIN_TOKEN must be a random secret of at least 43 characters")
    app = FastAPI(title="DeployOps", version="0.1.0", docs_url=None, redoc_url=None, openapi_url=None)
    controller = Controller(settings, Store(settings.database))
    identity = verifier or GithubVerifier(settings.audience)
    slack = Slack(controller, settings.slack, os.environ.get("DEPLOY_OPS_SLACK_SIGNING_SECRET", ""),
                  os.environ.get("DEPLOY_OPS_SLACK_BOT_TOKEN", ""))
    app.state.controller = controller
    app.state.slack = slack
    app.add_middleware(BodyLimit, limit=settings.max_body_bytes)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)

    def token(request: Request) -> str:
        kind, _, value = request.headers.get("authorization", "").partition(" ")
        if kind.lower() != "bearer" or not value:
            raise Refused("Bearer authentication is required", 401)
        return value

    def admin(request: Request) -> None:
        if not hmac.compare_digest(token(request).encode(), admin_token.encode()):
            raise Refused("Operator authentication is required", 401)

    def machine(request: Request):
        return identity.verify(token(request))

    @app.exception_handler(Refused)
    async def refusal(request, error):
        return JSONResponse({"detail": str(error)}, status_code=error.status)

    @app.exception_handler(RequestValidationError)
    @app.exception_handler(ValidationError)
    async def validation(request, error):
        # Pydantic's default errors can echo the submitted plan, including its secrets
        return JSONResponse({"detail": "Invalid request fields or values"}, status_code=422)

    @app.middleware("http")
    async def response_headers(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.get("/healthz")
    def health():
        return {"status": "ok"}

    @app.get("/v1/targets/{target_name}/plan-context")
    def plan_context(target_name: str, request: Request):
        source = machine(request)
        target = controller.target(target_name)
        controller.check_source(target, source, "plan")
        return {"cloud": target.cloud, "identity": target.planner_identity,
                "workdir": target.workdir, "engines": target.engines, "commit": source.sha,
                "receipt_outputs": target.receipt_outputs.model_dump() if target.receipt_outputs else None}

    @app.get("/v1/targets/{target_name}/delivery-context")
    def delivery_context(target_name: str, request: Request):
        source = machine(request)
        target = controller.target(target_name)
        controller.check_source(target, source, "execute")
        if "delivery" not in target.engines:
            raise Refused("Target does not enable delivery", 403)
        return {"cloud": target.cloud, "identity": target.planner_identity,
                "config_digest": target.delivery_digest, "receipt_dependencies": target.receipt_dependencies}

    @app.get("/v1/targets/{target_name}/receipts/{receipt_id}")
    def output_receipt(target_name: str, receipt_id: str, request: Request):
        return controller.output_receipt(receipt_id, target_name, machine(request))

    @app.post("/v1/admin/receipts/{receipt_id}/revoke")
    def revoke_receipt(receipt_id: str, request: Request):
        admin(request)
        return controller.revoke_receipt(receipt_id, "operator-token")

    @app.post("/v1/requests", status_code=201)
    def submit(body: Proposal, request: Request, tasks: BackgroundTasks):
        record = controller.submit(body, machine(request))
        if record["status"] in ("pending", "denied"):
            tasks.add_task(slack.notify, record)
        return record

    @app.get("/v1/requests/{request_id}")
    def get_request(request_id: str, request: Request):
        credential = token(request)
        is_operator = hmac.compare_digest(credential.encode(), admin_token.encode())
        source = None if is_operator else identity.verify(credential)
        return controller.get(request_id, source)

    @app.post("/v1/requests/{request_id}/claim")
    def claim(request_id: str, body: Claim, request: Request):
        return controller.claim(request_id, body, machine(request))

    @app.post("/v1/executions/{request_id}/heartbeat")
    def heartbeat(request_id: str, request: Request):
        return controller.heartbeat(request_id, token(request))

    @app.post("/v1/executions/{request_id}/complete")
    def complete(request_id: str, body: Completion, request: Request):
        return controller.complete(request_id, token(request), body)

    @app.get("/v1/admin/requests")
    def list_requests(request: Request):
        admin(request)
        return controller.list_requests()

    @app.post("/v1/admin/requests/{request_id}/decision")
    def decide(request_id: str, body: Decision, request: Request):
        admin(request)
        return controller.decide(request_id, body, "operator-token")

    @app.post("/v1/admin/requests/{request_id}/resolve")
    def resolve(request_id: str, body: Resolution, request: Request):
        admin(request)
        return controller.resolve(request_id, body, "operator-token")

    @app.post("/v1/admin/delegations", status_code=201)
    def delegate(body: DelegationSpec, request: Request):
        admin(request)
        return controller.delegate(body, "operator-token")

    @app.get("/v1/admin/delegations")
    def delegations(request: Request):
        admin(request)
        import json
        with controller.store.connect() as connection:
            rows = connection.execute("SELECT data FROM delegations ORDER BY rowid DESC")
            return [json.loads(row[0]) for row in rows]

    @app.post("/v1/admin/delegations/{delegation_id}/revoke")
    def revoke(delegation_id: str, request: Request):
        admin(request)
        return controller.revoke(delegation_id, "operator-token")

    @app.post("/v1/admin/pause")
    def pause(request: Request):
        admin(request)
        return controller.pause(True, "operator-token")

    @app.post("/v1/admin/resume")
    def resume(request: Request):
        admin(request)
        return controller.pause(False, "operator-token")

    @app.get("/v1/admin/audit")
    def audit(request: Request):
        admin(request)
        return controller.store.audit()

    @app.get("/v1/admin/audit/verify")
    def verify_audit(request: Request):
        admin(request)
        return controller.store.verify()

    @app.post("/v1/slack")
    async def slack_callback(request: Request):
        return slack.handle(await request.body(), request.headers.get("x-slack-request-timestamp", ""),
                            request.headers.get("x-slack-signature", ""))

    return app


def from_environment() -> FastAPI:
    path = Path(os.environ.get("DEPLOY_OPS_CONFIG", "config/controller.toml"))
    return create_app(load(path), os.environ.get("DEPLOY_OPS_ADMIN_TOKEN", ""))
