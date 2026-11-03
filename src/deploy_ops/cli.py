# src/deploy_ops/cli.py - Run deployments, inspect evidence, and open the local operator console.

import argparse
import json
import os
import secrets
import shutil
import sys
import time
from pathlib import Path

from pydantic import ValidationError

from .errors import Refused


def parser():
    root = argparse.ArgumentParser(prog="deploy-ops", description="Deploy infrastructure and applications with explicit authority")
    root.add_argument("--project", type=Path, default=Path("deploy-ops.toml"))
    root.add_argument("--state", type=Path)
    root.add_argument("--controller", default=os.environ.get("DEPLOY_OPS_URL"))
    commands = root.add_subparsers(dest="command", required=True)
    for name in ("init", "demo"):
        command = commands.add_parser(name)
        command.add_argument("directory", type=Path)
    commands.add_parser("doctor")
    commands.add_parser("catalog")
    commands.add_parser("schema")
    config = commands.add_parser("config", help="Inspect or edit a configuration draft without saving it")
    config.add_argument("action", choices=["inspect", "patch"])
    config.add_argument("--section", choices=["repositories", "environments", "components"])
    config.add_argument("--name")
    config.add_argument("--values", type=json.loads, default={})
    for name in ("plan", "run"):
        command = commands.add_parser(name)
        command.add_argument("environment")
        command.add_argument("--component")
        command.add_argument("--only", action="store_true", help="Reuse recorded dependency outputs instead of redeploying them")
        if name == "run":
            command.add_argument("--agent", default="")
            command.add_argument("--image", help="Immutable image in the component's configured image_repository")
            command.add_argument("--receipts", type=json.loads, default={}, help='Exact infrastructure receipt IDs as JSON: {"foundation":"ID"}')
            command.add_argument("--wait-seconds", type=int, default=0)
    for name in ("resume", "cancel", "events"):
        command = commands.add_parser(name)
        command.add_argument("release")
        if name == "resume":
            command.add_argument("--wait-seconds", type=int, default=0)
    status = commands.add_parser("status")
    status.add_argument("release", nargs="?")
    approval = commands.add_parser("approve")
    approval.add_argument("release")
    approval.add_argument("component")
    approval.add_argument("--digest", required=True)
    grant = commands.add_parser("delegate")
    grant.add_argument("environment")
    grant.add_argument("--hours", type=int, required=True)
    grant.add_argument("--uses", type=int, required=True)
    recover = commands.add_parser("recover")
    recover.add_argument("release")
    recover.add_argument("--note", required=True)
    for name in ("promote", "rollback"):
        command = commands.add_parser(name)
        command.add_argument("release")
        command.add_argument("environment")
        command.add_argument("--component", required=True)
        command.add_argument("--receipts", type=json.loads, default={}, help="Target-specific infrastructure receipt IDs as JSON")
    preview = commands.add_parser("preview", help="Serve a successful static release on loopback until stopped")
    preview.add_argument("release")
    preview.add_argument("--component", required=True)
    preview.add_argument("--port", type=int, default=8000)
    notify = commands.add_parser("notify")
    notify.add_argument("--url", default=os.environ.get("DEPLOY_OPS_NOTIFY_URL"))
    notify.add_argument("--kind", choices=["json", "slack", "ntfy"], default="json")
    ui = commands.add_parser("ui")
    ui.add_argument("--port", type=int, default=8765)
    ui.add_argument("--token-file", type=Path, required=True)
    gate = commands.add_parser("gate", help="Use the independent approval controller and IaC worker")
    gate.add_argument("args", nargs=argparse.REMAINDER)
    return root


def advance(engine, release, wait_seconds):
    if not 0 <= wait_seconds <= 3600:
        raise Refused("Wait must be between 0 and 3600 seconds", 422)
    deadline = time.monotonic() + wait_seconds
    while True:
        result = engine.advance(release)
        if result["status"] != "waiting" or time.monotonic() >= deadline:
            return result
        time.sleep(min(5, max(0, deadline - time.monotonic())))


def dispatch(args):
    if args.command == "gate":
        from . import gate_cli
        return gate_cli._dispatch(gate_cli.parser().parse_args(args.args))
    if args.command == "init":
        from .delivery.scaffold import create
        return {"project": str(create(args.directory))}
    if args.command == "demo":
        from .delivery.demo import run
        return run(args.directory)
    if args.command == "doctor":
        return {"python": sys.version.split()[0], "tools": {name: shutil.which(name) for name in
                ("git", "docker", "tofu", "terraform", "aws", "az", "gcloud", "kubectl")},
                "note": "Availability is not authentication or a successful live deployment"}
    if args.command == "schema":
        from .delivery.spec import Workspace
        return Workspace.model_json_schema()
    if args.command == "catalog":
        from importlib.resources import files
        return json.loads(files("deploy_ops").joinpath("catalog.json").read_text())
    if args.command == "config":
        from .delivery.configuration import inspect, patch
        source = args.project.read_text()
        return inspect(source) if args.action == "inspect" else patch(source, args.section, args.name, args.values)
    from .delivery.engine import Engine
    engine = Engine(args.project, args.state, args.controller)
    if args.command == "plan":
        return engine.describe(args.environment, args.component, not args.only)
    if args.command == "run":
        release = engine.create(args.environment, args.component, not args.only, args.agent, image=args.image, receipts=args.receipts)
        return advance(engine, release["id"], args.wait_seconds)
    if args.command == "resume":
        return advance(engine, args.release, args.wait_seconds)
    if args.command == "status":
        return engine.ledger.get(args.release) if args.release else engine.ledger.list()
    if args.command == "events":
        return engine.ledger.events(args.release)
    if args.command == "approve":
        return engine.approve(args.release, args.component, args.digest)
    if args.command == "cancel":
        return engine.cancel(args.release)
    if args.command == "recover":
        return engine.recover(args.release, args.note)
    if args.command == "delegate":
        if args.controller:
            raise Refused("Create remote grants through the operator gate command, not the execution worker")
        return engine.gate.delegate(args.environment, args.hours, args.uses)
    if args.command in ("promote", "rollback"):
        release = engine.create(args.environment, args.component, False, promote=args.release,
                                rollback=args.command == "rollback", receipts=args.receipts)
        return advance(engine, release["id"], 0)
    if args.command == "preview":
        from functools import partial
        from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
        from .delivery.files import inside
        release = engine.ledger.get(args.release)
        node = release["nodes"].get(args.component, {})
        if node.get("status") != "succeeded" or node.get("adapter") != "static" or not 1024 <= args.port <= 65535:
            raise Refused("Choose a successful static component and an unprivileged port", 422)
        site = inside(engine.deployments, node["outputs"]["path"])
        server = ThreadingHTTPServer(("127.0.0.1", args.port), partial(SimpleHTTPRequestHandler, directory=str(site)))
        print(f"Static release: http://127.0.0.1:{args.port} | Ctrl-C stops this preview")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return {"status": "preview-stopped"}
    if args.command == "notify":
        from .delivery.notify import flush
        if not args.url:
            raise Refused("Set DEPLOY_OPS_NOTIFY_URL or pass --url", 422)
        return flush(engine.ledger, args.url, args.kind)
    if args.command == "ui":
        from .console import serve
        return serve(engine, args.port, args.token_file)
    raise Refused("Unknown command", 422)


def main(argv=None):
    try:
        args = parser().parse_args(argv)
        result = dispatch(args)
        if result is not None:
            print(json.dumps(result, indent=2, default=str))
        if isinstance(result, dict) and args.command in ("run", "resume", "promote", "rollback"):
            if result.get("status") == "waiting":
                return 3
            if result.get("status") in ("failed", "uncertain", "cancelled"):
                return 1
        return 0
    except (Refused, ValidationError, OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
