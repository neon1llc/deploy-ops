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


# Expose read, review and mutation commands as separate operator choices
def parser():
    root = argparse.ArgumentParser(prog="deploy-ops", description="Deploy infrastructure and applications with explicit authority")
    root.add_argument("--project", type=Path, default=Path("deploy-ops.toml"))
    root.add_argument("--state", type=Path)
    root.add_argument("--controller", default=os.environ.get("DEPLOY_OPS_URL"))
    commands = root.add_subparsers(dest="command", required=True)

    # Discovery and draft editing do not start deployment workers
    for name in ("init", "demo"):
        command = commands.add_parser(name)
        command.add_argument("directory", type=Path)
    commands.add_parser("doctor")
    commands.add_parser("catalog")
    commands.add_parser("schema")
    provider = commands.add_parser("provider", help="Read native provider status or discard an unexecuted change set")
    provider.add_argument("action", choices=["inspect", "discard"])
    provider.add_argument("release")
    provider.add_argument("component")
    config = commands.add_parser("config", help="Inspect or edit a configuration draft without saving it")
    config.add_argument("action", choices=["inspect", "patch"])
    config.add_argument("--section", choices=["repositories", "environments", "components", "options"])
    config.add_argument("--name")
    config.add_argument("--values", type=json.loads, default={})

    # Planning describes the graph while running freezes and prepares release evidence
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
            command.add_argument("--preview-owner", default="")
            command.add_argument("--preview-hours", type=int, default=1)
            command.add_argument("--retain-preview-data", action="store_true")

    # Existing releases keep execution, approval and recovery as explicit actions
    for name in ("resume", "cancel", "events", "observe"):
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

    # Promotion reuses artifacts while preview cleanup uses separate deletion approval
    for name in ("promote", "rollback"):
        command = commands.add_parser(name)
        command.add_argument("release")
        command.add_argument("environment")
        command.add_argument("--component", required=True)
        command.add_argument("--receipts", type=json.loads, default={}, help="Target-specific infrastructure receipt IDs as JSON")
    previews = commands.add_parser("previews", help="Inspect isolated static previews and exact cleanup")
    preview_actions = previews.add_subparsers(dest="preview_action", required=True)
    preview_actions.add_parser("list")
    for name in ("prepare-cleanup", "approve-cleanup", "cleanup", "reconcile"):
        command = preview_actions.add_parser(name)
        command.add_argument("release")
        if name == "approve-cleanup":
            command.add_argument("--digest", required=True)
        if name == "reconcile":
            command.add_argument("--note", required=True)
            command.add_argument("--confirm-removed", action="store_true")
    preview = commands.add_parser("preview", help="Serve a successful static release on loopback until stopped")
    preview.add_argument("release")
    preview.add_argument("--component", required=True)
    preview.add_argument("--port", type=int, default=8000)

    # Incident decisions remain independent of transport delivery and release status
    incidents = commands.add_parser("incidents", help="Track local incidents separately from deployment outcomes")
    actions = incidents.add_subparsers(dest="incident_action", required=True)
    actions.add_parser("list")
    actions.add_parser("tick")
    opening = actions.add_parser("open")
    opening.add_argument("release")
    opening.add_argument("--owner", required=True)
    opening.add_argument("--note", required=True)
    opening.add_argument("--interval-seconds", type=int, default=900)
    opening.add_argument("--limit", type=int, default=3)
    for name in ("acknowledge", "resolve"):
        action = actions.add_parser(name)
        action.add_argument("incident")
        action.add_argument("--version", type=int, required=True)
        action.add_argument("--note", required=True)
    notify = commands.add_parser("notify")
    notify.add_argument("--status", action="store_true", help="Inspect the outbox without sending")
    notify.add_argument("--url", default=os.environ.get("DEPLOY_OPS_NOTIFY_URL"))
    notify.add_argument("--kind", choices=["json", "slack", "ntfy"], default="json")

    # Paging configuration, explicit sends and unattended delivery share one durable owner
    paging = commands.add_parser("paging", help="Configure named destinations and inspect native incident delivery")
    paging_actions = paging.add_subparsers(dest="paging_action", required=True)
    for name in ("config", "status"):
        paging_actions.add_parser(name)
    draft = paging_actions.add_parser("save")
    draft.add_argument("file", type=Path, help="Secret-free paging TOML")
    draft.add_argument("--revision", type=int, required=True)
    activation = paging_actions.add_parser("activate")
    activation.add_argument("--revision", type=int, required=True)
    route = paging_actions.add_parser("preview")
    for name in ("project", "environment", "component", "event", "severity"):
        route.add_argument("--" + name, dest="paging_" + name, default="incident.opened" if name == "event" else "")
    route.add_argument("--file", type=Path, help="Preview an unsaved TOML draft without sending")
    test = paging_actions.add_parser("test", help="Explicitly queue a synthetic page that can contact a responder")
    test.add_argument("destination")
    test.add_argument("--revision", type=int, required=True)
    drain = paging_actions.add_parser("drain")
    drain.add_argument("--limit", type=int, default=50)
    paging_actions.add_parser("worker", help="Run the foreground delivery worker until interrupted")
    for name in ("acknowledge", "resolve", "retry", "readback"):
        action = paging_actions.add_parser(name)
        action.add_argument("binding")
        action.add_argument("--version", type=int, required=True)
        action.add_argument("--allow-duplicate", action="store_true")

    # Console access and the independent authority controller retain their own interfaces
    ui = commands.add_parser("ui")
    ui.add_argument("--port", type=int, default=8765)
    ui.add_argument("--token-file", type=Path, required=True)
    gate = commands.add_parser("gate", help="Use the independent approval controller and IaC worker")
    gate.add_argument("args", nargs=argparse.REMAINDER)
    return root


# Wait only within the requested bound while preserving pending approvals
def advance(engine, release, wait_seconds):
    if not 0 <= wait_seconds <= 3600:
        raise Refused("Wait must be between 0 and 3600 seconds", 422)
    deadline = time.monotonic() + wait_seconds
    while True:
        result = engine.advance(release)
        if result["status"] != "waiting" or time.monotonic() >= deadline:
            return result
        time.sleep(min(5, max(0, deadline - time.monotonic())))


# Route every entry point through the shared deployment owners
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

    # Stateful actions share the same workspace and ledger owner
    from .delivery.engine import Engine
    engine = Engine(args.project, args.state, args.controller)
    if args.command == "provider":
        return engine.provider_status(args.release, args.component, args.action == "discard")
    if args.command == "plan":
        return engine.describe(args.environment, args.component, not args.only)
    if args.command == "run":
        release = engine.create(args.environment, args.component, not args.only, args.agent, image=args.image, receipts=args.receipts,
                                preview_owner=args.preview_owner, preview_hours=args.preview_hours,
                                retain_preview_data=args.retain_preview_data)
        return advance(engine, release["id"], args.wait_seconds)
    if args.command == "resume":
        return advance(engine, args.release, args.wait_seconds)
    if args.command == "status":
        return engine.ledger.get(args.release) if args.release else engine.ledger.list()
    if args.command == "events":
        return engine.ledger.events(args.release)
    if args.command == "observe":
        return engine.observe(args.release)
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

    # Reuse, cleanup and preview serving keep their distinct artifact and lifetime checks
    if args.command in ("promote", "rollback"):
        release = engine.create(args.environment, args.component, False, promote=args.release,
                                rollback=args.command == "rollback", receipts=args.receipts)
        return advance(engine, release["id"], 0)
    if args.command == "previews":
        from .delivery.previews import list_previews, prepare_cleanup, approve_cleanup, execute_cleanup, reconcile_cleanup
        if args.preview_action == "list":
            return list_previews(engine)
        if args.preview_action == "prepare-cleanup":
            return prepare_cleanup(engine, args.release)
        if args.preview_action == "approve-cleanup":
            return approve_cleanup(engine, args.release, args.digest)
        if args.preview_action == "cleanup":
            return execute_cleanup(engine, args.release)
        return reconcile_cleanup(engine, args.release, args.note, args.confirm_removed)
    if args.command == "preview":
        from functools import partial
        from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
        from .delivery.files import inside
        release = engine.ledger.get(args.release)
        node = release["nodes"].get(args.component, {})
        if node.get("status") != "succeeded" or node.get("adapter") != "static" or not 1024 <= args.port <= 65535:
            raise Refused("Choose a successful static component and an unprivileged port", 422)
        if release.get("preview"):
            from .delivery.previews import active
            active(engine, release)
        site = inside(engine.deployment_root(release), node["outputs"]["path"])


        # Serve only the selected artifact while checking preview lifetime on each request
        class PreviewHandler(SimpleHTTPRequestHandler):
            # Reject expired previews and paths outside the deployed artifact
            def send_head(self):
                if release.get("preview"):
                    from .delivery.previews import active
                    try:
                        active(engine, engine.ledger.get(release["id"]))
                    except (Refused, OSError):
                        self.send_error(410, "Preview expired or retired")
                        return None
                requested = Path(self.translate_path(self.path)).resolve()
                if not requested.is_relative_to(site):
                    self.send_error(403, "Path leaves the preview")
                    return None
                return super().send_head()
        server = ThreadingHTTPServer(("127.0.0.1", args.port), partial(PreviewHandler, directory=str(site)))
        print(f"Static release: http://127.0.0.1:{args.port} | Ctrl-C stops this preview")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return {"status": "preview-stopped"}

    # Notification delivery cannot acknowledge or resolve an incident
    if args.command == "incidents":
        from .delivery.incidents import list_incidents, open_incident, tick, transition
        if args.incident_action == "list":
            return list_incidents(engine.ledger)
        if args.incident_action == "tick":
            return tick(engine.ledger)
        if args.incident_action == "open":
            return open_incident(engine.ledger, args.release, args.owner, args.note, args.interval_seconds, args.limit)
        return transition(engine.ledger, args.incident, args.incident_action, args.version, args.note)
    if args.command == "notify":
        from .delivery.notify import flush, status
        if args.status:
            return status(engine.ledger)
        if not args.url:
            raise Refused("Set DEPLOY_OPS_NOTIFY_URL or pass --url", 422)
        return flush(engine.ledger, args.url, args.kind)
    if args.command == "paging":
        from .delivery import paging
        action = args.paging_action
        if action == "config":
            return paging.config(engine.ledger)
        if action == "status":
            return paging.status(engine.ledger)
        if action == "save":
            return paging.save_config(engine.ledger, args.file.read_text(), args.revision)
        if action == "activate":
            return paging.activate(engine.ledger, args.revision)
        if action == "preview":
            context = {key: getattr(args, "paging_" + key) for key in ("project", "environment", "component", "event", "severity")}
            draft = args.file.read_text() if args.file else None
            return paging.preview(engine.ledger, context, draft)
        if action == "test":
            return paging.test_destination(engine.ledger, args.destination, args.revision)
        if action == "drain":
            return paging.drain(engine.ledger, args.limit)
        if action == "worker":
            paging.run_worker(engine.ledger)
            return {"stopped": True}
        if action == "readback":
            return paging.readback(engine.ledger, args.binding, args.version)
        return paging.action(engine.ledger, args.binding, action, args.version, args.allow_duplicate)
    if args.command == "ui":
        from .console import serve
        return serve(engine, args.port, args.token_file)
    raise Refused("Unknown command", 422)


# Give automation distinct exits for pending approval and unsuccessful delivery
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


# Preserve direct execution alongside the installed console entry point
if __name__ == "__main__":
    raise SystemExit(main())
