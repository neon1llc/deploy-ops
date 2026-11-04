# src/deploy_ops/gate_cli.py - Provide local, operator, and trusted-workflow commands.


import argparse
import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

from pydantic import ValidationError

from .client import Client, file_digest, github_token, private_json
from .config import load
from .errors import Refused
from .models import Agent, Proposal


# Expose planning, review and execution as separate commands
def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="deploy-ops gate",
        description="Approve exact deployment plans and bounded work windows",
    )
    root.add_argument("--url", help="Controller HTTPS URL, otherwise DEPLOY_OPS_URL")
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("demo", help="Run an offline example with no cloud calls")
    secret = commands.add_parser("secret", help="Write a random operator token to an owner-only file")
    secret.add_argument("--out", type=Path, required=True)
    for name in ("serve", "config-check"):
        command = commands.add_parser(name)
        command.add_argument("--config", type=Path, default=Path("config/controller.toml"))
        if name == "serve":
            command.add_argument("--host", default="127.0.0.1")
            command.add_argument("--port", type=int, default=8787)
    commands.add_parser("inspect-identity", help="Print verified non-secret OIDC claims for trust setup")
    plan = commands.add_parser("plan", help="Create and submit a plan using a separate read identity")
    plan.add_argument("--target", required=True)
    plan.add_argument("--engine", choices=["terraform", "opentofu"], default="opentofu")
    plan.add_argument("--workspace", type=Path, default=Path.cwd())
    plan.add_argument("--out", type=Path, default=Path(".deploy_ops"))
    plan.add_argument("--cost-file", type=Path)
    plan.add_argument("--agent-label", default="")
    prepare = commands.add_parser("prepare", help="Wrap a plan produced by an existing trusted pipeline")
    prepare.add_argument("--target", required=True)
    prepare.add_argument("--engine", choices=["terraform", "opentofu", "manifest"], required=True)
    prepare.add_argument("--plan-json", type=Path, required=True)
    prepare.add_argument("--artifact", type=Path, required=True)
    prepare.add_argument("--lock-file", type=Path)
    prepare.add_argument("--tool-version", required=True)
    prepare.add_argument("--cost-file", type=Path)
    prepare.add_argument("--agent-label", default="")
    prepare.add_argument("--out", type=Path, required=True)
    submit = commands.add_parser("submit")
    submit.add_argument("proposal", type=Path)
    submit.add_argument("--out", type=Path, default=Path(".deploy_ops/ticket.json"))
    status = commands.add_parser("status")
    status.add_argument("request_id")
    status.add_argument("--operator", action="store_true")
    wait = commands.add_parser("wait")
    wait.add_argument("--ticket", type=Path, required=True)
    wait.add_argument("--seconds", type=int, default=1800)
    wait.add_argument("--poll", type=int, default=10)
    claim = commands.add_parser("claim")
    claim.add_argument("--ticket", type=Path, required=True)
    claim.add_argument("--artifact", type=Path, required=True)
    claim.add_argument("--out", type=Path, default=Path(".deploy_ops/lease.json"))
    # Execution commands consume a secret lease instead of operator authority
    for name in ("execute", "heartbeat", "complete"):
        command = commands.add_parser(name)
        command.add_argument("--lease", type=Path, required=True)
        if name == "execute":
            command.add_argument("--artifact", type=Path, required=True)
            command.add_argument("--workspace", type=Path, default=Path.cwd())
        if name == "complete":
            command.add_argument("--outcome", choices=["succeeded", "failed", "aborted"], required=True)
    # Human decisions must supply the exact reviewed digest
    for name in ("approve", "reject"):
        decision = commands.add_parser(name)
        decision.add_argument("request_id")
        decision.add_argument("--digest", required=True, help="Exact digest from the reviewed request")
        decision.add_argument("--note", default="")
    for name in ("list", "delegations", "pause", "resume"):
        commands.add_parser(name)
    grant = commands.add_parser("delegate")
    grant.add_argument("target")
    grant.add_argument("--hours", type=int, required=True)
    grant.add_argument("--uses", type=int, required=True)
    revoke = commands.add_parser("revoke")
    revoke.add_argument("delegation_id")
    receipt = commands.add_parser("receipt", help="Verify a public infrastructure receipt for a registered consumer")
    receipt.add_argument("receipt_id")
    receipt.add_argument("--target", required=True)
    revoke_receipt = commands.add_parser("revoke-receipt")
    revoke_receipt.add_argument("receipt_id")
    resolve = commands.add_parser("resolve")
    resolve.add_argument("request_id")
    resolve.add_argument("--outcome", choices=["succeeded", "failed", "cancelled"], required=True)
    resolve.add_argument("--note", required=True)
    audit = commands.add_parser("audit")
    audit.add_argument("--out", type=Path, help="Export the full audit ledger as JSON")
    return root


# Route commands through the shared controller and trusted runner
def _dispatch(args):
    if args.command == "demo":
        from .demo import run
        return run()
    if args.command == "secret":
        if args.out.exists():
            raise Refused("Refusing to replace an existing operator token", 422)
        args.out.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as handle:
            handle.write(secrets.token_urlsafe(32) + "\n")
        return {"written": str(args.out), "mode": "0600"}
    if args.command in ("serve", "config-check"):
        settings = load(args.config)
        if args.command == "config-check":
            return {"valid": True, "targets": list(settings.targets),
                    "note": "Schema check only. This does not verify cloud trust or account permissions"}
        import uvicorn
        from .api import create_app
        uvicorn.run(create_app(settings, os.environ.get("DEPLOY_OPS_ADMIN_TOKEN", "")), host=args.host,
                    port=args.port, access_log=False, proxy_headers=False)
        return None
    client = Client(args.url)
    if args.command == "inspect-identity":
        from .identity import GithubVerifier
        return GithubVerifier(client.audience).verify(github_token(client.audience)).model_dump()
    if args.command == "plan":
        from .runner import plan
        return plan(client, args.target, args.engine, args.workspace, args.out,
                    Agent(label=args.agent_label), args.cost_file)
    if args.command == "prepare":
        from .plans import cost_delta
        cost = cost_delta(json.loads(args.cost_file.read_text())) if args.cost_file else None
        proposal = Proposal(target=args.target, engine=args.engine,
                            artifact_digest=file_digest(args.artifact),
                            preview=json.loads(args.plan_json.read_text()), tool_version=args.tool_version,
                            lock_digest=file_digest(args.lock_file) if args.lock_file else None,
                            estimated_monthly_delta_cents=cost, agent=Agent(label=args.agent_label))
        private_json(args.out, proposal.model_dump())
        return {"written": str(args.out), "note": "Contains raw plan data. Do not commit this file"}
    if args.command == "submit":
        proposal = Proposal.model_validate_json(args.proposal.read_text())
        request = client.call("POST", "/v1/requests", proposal.model_dump())
        private_json(args.out, {"id": request["id"], "digest": request["digest"]})
        return request
    if args.command == "status":
        return client.call("GET", f"/v1/requests/{args.request_id}", operator=args.operator)
    if args.command == "receipt":
        return client.call("GET", f"/v1/targets/{args.target}/receipts/{args.receipt_id}")
    if args.command == "revoke-receipt":
        return client.call("POST", f"/v1/admin/receipts/{args.receipt_id}/revoke", operator=True)
    # Polling never starts deployment and stops at a bounded deadline
    if args.command == "wait":
        ticket = json.loads(args.ticket.read_text())
        if not 1 <= args.seconds <= 86400 or not 1 <= args.poll <= 60:
            raise Refused("Use a wait of 1 to 86400 seconds and a poll interval of 1 to 60 seconds", 422)
        end = time.monotonic() + args.seconds
        while time.monotonic() < end:
            request = client.call("GET", f"/v1/requests/{ticket['id']}")
            if request["digest"] != ticket["digest"]:
                raise Refused("Ticket digest changed")
            if request["status"] == "approved":
                return {"id": request["id"], "status": "approved"}
            if request["status"] != "pending":
                raise Refused("Request cannot execute: " + request["status"])
            time.sleep(args.poll)
        raise Refused("Still pending. No execution was started", 408)
    if args.command == "claim":
        ticket = json.loads(args.ticket.read_text())
        lease = client.call("POST", f"/v1/requests/{ticket['id']}/claim",
                            {"digest": ticket["digest"], "artifact_digest": file_digest(args.artifact)})
        private_json(args.out, lease)
        return {"request_id": lease["request_id"], "deadline": lease["deadline"],
                "lease_file": str(args.out), "note": "The lease file is a secret. Do not upload it"}
    if args.command in ("execute", "heartbeat", "complete"):
        lease = json.loads(args.lease.read_text())
        route = f"/v1/executions/{lease['request_id']}"
        if args.command == "heartbeat":
            return client.call("POST", route + "/heartbeat", execution_token=lease["token"])
        if args.command == "complete":
            return client.call("POST", route + "/complete", {"outcome": args.outcome}, execution_token=lease["token"])
        from .runner import execute
        code = execute(client, lease, args.artifact, args.workspace)
        if code:
            raise Refused(f"Deployment command returned exit code {code}")
        return {"request_id": lease["request_id"], "status": "succeeded"}
    if args.command in ("approve", "reject"):
        return client.call("POST", f"/v1/admin/requests/{args.request_id}/decision",
                           {"digest": args.digest, "decision": args.command, "note": args.note}, operator=True)
    if args.command in ("list", "delegations"):
        resource = "requests" if args.command == "list" else "delegations"
        return client.call("GET", "/v1/admin/" + resource, operator=True)
    if args.command in ("pause", "resume"):
        return client.call("POST", "/v1/admin/" + args.command, operator=True)
    if args.command == "delegate":
        return client.call(
            "POST", "/v1/admin/delegations",
            {"target": args.target, "hours": args.hours, "uses": args.uses},
            operator=True,
        )
    if args.command == "revoke":
        return client.call("POST", f"/v1/admin/delegations/{args.delegation_id}/revoke", operator=True)
    if args.command == "resolve":
        return client.call("POST", f"/v1/admin/requests/{args.request_id}/resolve",
                           {"outcome": args.outcome, "note": args.note}, operator=True)
    if args.command == "audit":
        result = client.call("GET", "/v1/admin/audit/verify", operator=True)
        if args.out:
            private_json(args.out, client.call("GET", "/v1/admin/audit", operator=True))
            result["export"] = str(args.out)
        return result
    raise Refused("Unsupported command", 422)


# Keep exception details and credential-bearing provider output off the CLI
def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        result = _dispatch(args)
        if result is not None:
            print(json.dumps(result, indent=2))
        return 0
    except Refused as error:
        print(str(error), file=sys.stderr)
    except (ValidationError, json.JSONDecodeError):
        print("Invalid configuration or request data. Check the documented schema", file=sys.stderr)
    except (OSError, subprocess.SubprocessError, ValueError, KeyError):
        print("Local command or file operation failed. Check paths, tool versions, and permissions", file=sys.stderr)
    except KeyboardInterrupt:
        return 130
    return 1
