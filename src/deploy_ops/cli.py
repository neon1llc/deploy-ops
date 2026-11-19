# src/deploy_ops/cli.py - Run deployments, inspect evidence, and open the local operator console.


import argparse
import hashlib
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
    # Inspect source capture evidence even when current workspace configuration is unavailable
    captures = commands.add_parser('captures', help='Read source capture attempts separately from registered releases')
    capture_actions = captures.add_subparsers(dest='capture_action', required=True)
    capture_list = capture_actions.add_parser('list')
    capture_list.add_argument('--offset', type=int, default=0)
    capture_list.add_argument('--limit', type=int, default=20)
    capture_show = capture_actions.add_parser('show')
    capture_show.add_argument('capture_id')
    backup = commands.add_parser('backup', help='Capture or restore private state; restored copies remain inspection-only')
    backup_actions = backup.add_subparsers(dest='backup_action', required=True)
    backup_actions.add_parser('options', help='Suggest new private targets and report capture bounds without creating files')
    backup_capture = backup_actions.add_parser('capture')
    backup_capture.add_argument('--out', type=Path, required=True, help='New private directory outside workspace and state')
    backup_inspect = backup_actions.add_parser('inspect')
    backup_inspect.add_argument('directory', type=Path)
    backup_restored = backup_actions.add_parser('inspect-restored', help='Verify a restored copy using its retained receipt and manifest')
    backup_restored.add_argument('directory', type=Path, help='Restored workspace directory')
    backup_restored.add_argument('--state-directory', type=Path, help='Restored state directory when separate from the workspace')
    backup_restore = backup_actions.add_parser('restore')
    backup_restore.add_argument('directory', type=Path)
    backup_restore.add_argument('--workspace-out', type=Path, required=True, help='New workspace directory; existing work is preserved')
    backup_restore.add_argument('--state-out', type=Path, help='New state directory; default is .deploy-ops inside the new workspace')
    backup_restore.add_argument('--digest', help='Exact backup manifest digest from inspect; refuse a changed extent')
    artifacts = commands.add_parser('artifact', help='Observe a reviewed frozen static artifact with maintained tools; no release policy change')
    artifact_actions = artifacts.add_subparsers(dest='artifact_action', required=True)
    artifact_actions.add_parser('options', help='Suggest private output and show supported observation bounds')
    artifact_collect = artifact_actions.add_parser('collect')
    artifact_collect.add_argument('release')
    artifact_collect.add_argument('--component', required=True)
    artifact_collect.add_argument('--out', type=Path, required=True, help='New private directory outside workspace and state')
    artifact_collect.add_argument('--inventory', action=argparse.BooleanOptionalAction, default=True)
    artifact_collect.add_argument('--vulnerabilities', action='store_true')
    artifact_collect.add_argument('--allow-network', action='store_true', help='Allow Trivy advisory DB retrieval; dependency network and telemetry remain disabled')
    artifact_collect.add_argument('--timeout-seconds', type=int, default=60, help='Maximum duration per native command, 10–300 seconds')
    artifact_inspect = artifact_actions.add_parser('inspect')
    artifact_inspect.add_argument('directory', type=Path)
    artifact_actions.add_parser('signature-options', help='Show explicit selected-key signature scope and tool availability')
    key_info = artifact_actions.add_parser('key-info', help='Read a local public-key file fingerprint; this does not establish trust')
    key_info.add_argument('file', type=Path)
    signature_inspect = artifact_actions.add_parser('signature-inspect', help='Check a retained signature report offline; no native verification or policy admission')
    signature_inspect.add_argument('directory', type=Path)
    artifact_files = artifact_actions.add_parser('files', help='Search reviewed frozen files without current configuration or tools')
    artifact_files.add_argument('release')
    artifact_files.add_argument('--component', required=True)
    artifact_files.add_argument('--query', default='')
    artifact_files.add_argument('--offset', type=int, default=0)
    artifact_files.add_argument('--limit', type=int, default=100)
    signature_verify = artifact_actions.add_parser('verify', help='Verify one frozen file with a local selected public key and modern bundle; not whole-artifact trust or release policy')
    signature_verify.add_argument('release')
    signature_verify.add_argument('--component', required=True)
    signature_verify.add_argument('--file', required=True, help='Exact relative file inside the frozen static artifact')
    signature_verify.add_argument('--key', type=Path, required=True, help='Independently selected local PEM PUBLIC KEY; no private keys, certificates or remote references')
    signature_verify.add_argument('--bundle', type=Path, required=True)
    signature_verify.add_argument('--key-sha256', help='Optional expected SHA-256 of the exact public-key file bytes')
    signature_verify.add_argument('--out', type=Path, required=True, help='New private directory; includes a bounded copy of the selected file')
    signature_verify.add_argument('--timeout-seconds', type=int, default=60)
    evidence = commands.add_parser("evidence", help="Save or inspect source-free operation facts; never execute imported evidence")
    evidence_actions = evidence.add_subparsers(dest="evidence_action", required=True)
    capture = evidence_actions.add_parser("capture", help="Read one retained release without loading its workspace or credentials")
    capture.add_argument("release")
    capture.add_argument("--out", type=Path, required=True, help="New owner-only JSON file; existing files are preserved")
    capture.add_argument("--run-id")
    capture.add_argument("--run-attempt")
    inspection = evidence_actions.add_parser("inspect", help="Validate a portable summary offline, without opening its recorded paths")
    inspection.add_argument("file", type=Path)
    inspection.add_argument("--html", type=Path, help="Save a readable offline desktop report to a new file")
    provider = commands.add_parser("provider", help="Read native provider status or discard an unexecuted change set")
    provider.add_argument("action", choices=["inspect", "discard"])
    provider.add_argument("release")
    provider.add_argument("component")
    config = commands.add_parser("config", help="Inspect, edit, validate, compare or explicitly save a workspace draft")
    config.add_argument("action", choices=["inspect", "add", "patch", "edit", "reset", "validate", "diff", "save", "recovery", "reconcile"])
    config.add_argument("--file", type=Path, help="Draft TOML input; required for validate, diff and save")
    config.add_argument("--source-hash", help="Exact saved workspace hash from inspect; required for save")
    config.add_argument("--operation-id", help="Exact interrupted Save identity from config recovery")
    config.add_argument("--note", help="Inspection note for config reconcile, which keeps the current file")
    config.add_argument("--section", choices=["repositories", "environments", "components", "options"])
    config.add_argument("--name")
    config.add_argument("--scope", choices=["build", "base", "environment"], help="Local component or native environment edit scope")
    config.add_argument("--environment", help="Local environment for an override or reset")
    config.add_argument("--field", help="One optional component option or environment override to remove with config reset")
    config.add_argument("--values", type=json.loads, default={})
    config.add_argument("--draft-out", type=Path, help="Write successful add/patch/edit/reset TOML to a separate draft file")
    # Common creation inputs remain readable while structured values cover advanced contracts
    config.add_argument("--path", help="Repository location or component-relative source path")
    config.add_argument("--provider", choices=["local", "aws", "azure", "gcp"])
    config.add_argument("--repository")
    config.add_argument("--kind", choices=["infrastructure", "application", "workflow"])
    config.add_argument("--adapter")
    config.add_argument("--need", action="append", help="Declared dependency name; repeat for multiple dependencies")
    config.add_argument("--resource-name", help="Directory resource slug")
    config.add_argument("--artifact-directory", help="Static artifact path inside the component source")
    config.add_argument("--destination", help="Deployment-relative path or whole dependency output reference")
    config.add_argument("--compose-file", help="Compose file inside the component source")
    config.add_argument("--compose-project", help="Explicit Compose project name; omission uses environment-component")
    config.add_argument("--build-command", type=json.loads, help='Argument list, e.g. \'["python3","build.py"]\'')
    config.add_argument("--check", action="append", type=json.loads, help='Check argument list; repeat for multiple checks')
    config.epilog = ('Example: config add --section components --name site --repository app --kind application '
                     '--adapter static --need foundation --artifact-directory dist '
                     '--build-command \'["python3","build.py"]\' --draft-out draft.toml. '
                     'Use --file draft.toml for the next addition; save is separate.')

    # Planning describes the graph while running freezes and prepares release evidence
    for name in ("plan", "run"):
        command = commands.add_parser(name)
        command.add_argument("environment")
        command.add_argument("--component")
        command.add_argument("--only", action="store_true", help="Reuse recorded dependency outputs instead of redeploying them")
        if name == "run":
            command.add_argument("--caller-label", dest="agent", default="", help="Self-reported caller label")
            command.add_argument("--agent", dest="agent", help=argparse.SUPPRESS)
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
    status.add_argument('--history', action='store_true', help='Browse all retained history with bounded pages; no execution')
    status.add_argument('--query', default='', help='Literal project, component or release-ID search')
    status.add_argument('--environment', default='', help='Exact recorded environment filter')
    status.add_argument('--component', default='', help='Exact recorded component filter')
    status.add_argument('--outcome', default='', help='Exact recorded release outcome, such as waiting, failed or succeeded')
    status.add_argument('--limit', type=int, help='History page size from 1 to 100; default20')
    status.add_argument('--cursor', default='', help='Next history cursor returned by the same filters/store; not execution authority')
    approval = commands.add_parser("approve")
    approval.add_argument("release")
    approval.add_argument("component")
    approval.add_argument("--digest", required=True)
    grant = commands.add_parser("delegate")
    grant.add_argument("environment")
    grant.add_argument("--hours", type=int, required=True)
    grant.add_argument("--uses", type=int, required=True)
    recovery = commands.add_parser("recovery", help="Inspect current local worker evidence and recovery availability")
    recovery.add_argument("release")
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
    for name in ("prepare-cleanup", "approve-cleanup", "cleanup", "reconcile", "recovery"):
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
    preview.add_argument('--retained', action='store_true', help='Read a retained original-path artifact without loading workspace or execution authority')
    preview.add_argument('--active-target', action='store_true', help='Follow successful approved activations of this owned local target on one URL; default stays on the selected revision')
    rollout = commands.add_parser('rollout', help='Measure a staged local candidate, separately switch its route and deliberately retire previous files')
    rollout_actions = rollout.add_subparsers(dest='rollout_action', required=True)
    rollout_read = rollout_actions.add_parser('read', help='Validate an inspection-only rollout snapshot offline')
    rollout_read.add_argument('file', type=Path)
    for name in ('prepare', 'approve', 'cancel', 'switch', 'inspect', 'check', 'prepare-retirement', 'approve-retirement', 'cancel-retirement', 'retire', 'recovery', 'reconcile'):
        command = rollout_actions.add_parser(name)
        command.add_argument('release')
        if name in {'prepare', 'prepare-retirement'}:
            command.add_argument('--component', required=True)
        if name in {'approve', 'approve-retirement', 'cancel', 'cancel-retirement'}:
            command.add_argument('--digest', required=True)
        if name == 'reconcile':
            command.add_argument('--note', required=True)

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
    opening.add_argument('--observation-sequence', type=int, help='Exact saved adverse observation sequence; deployment outcome is unchanged')
    opening.add_argument('--observation-digest', default='', help='Digest returned with the saved observation reference')
    handoff_preview = actions.add_parser('handoff-preview', help='Review active route and quiet/not-handed-off history without dispatch')
    handoff_preview.add_argument('incident')
    handoff_action = actions.add_parser('handoff', help='Explicitly queue the exact reviewed incident responder route')
    handoff_action.add_argument('incident')
    handoff_action.add_argument('--version', type=int, required=True)
    handoff_action.add_argument('--route-digest', required=True)
    handoff_action.add_argument('--note', required=True)
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

    # Local controller operations reuse the same owner as the console
    operator = commands.add_parser("operator", help="Inspect and stop local controller authority")
    operator.add_argument("action", choices=["status", "requests", "request", "decision", "resolve", "pause", "resume", "delegations", "revoke", "audit"])
    operator.add_argument("identifier", nargs="?", default="")
    operator.add_argument("--values", type=json.loads, default={})

    # Console access and the independent authority controller retain their own interfaces
    ui = commands.add_parser("ui")
    ui.add_argument("--port", type=int, default=8765)
    ui.add_argument("--token-file", type=Path, required=True)
    # Isolated local infrastructure shares its core methods with the operator console
    from .isolated import arguments
    arguments(commands.add_parser("isolated", help="Approve and run one registered isolated GCP recipe"))
    gate = commands.add_parser("gate", help="Use the independent approval controller and IaC worker", add_help=False)
    gate.add_argument('-h', '--help', dest='gate_help', action='store_true')
    gate.add_argument("--url", help="Controller HTTPS URL, otherwise DEPLOY_OPS_URL")
    gate.add_argument("args", nargs=argparse.REMAINDER)
    return root


# Wait only within the requested bound while preserving pending approvals
def advance(engine, release, wait_seconds):
    if not 0 <= wait_seconds <= 3600:
        raise Refused("Wait must be between 0 and 3600 seconds", 422)
    deadline = time.monotonic() + wait_seconds
    result = None
    while True:
        try:
            result = engine.advance(release)
        except Refused:
            # Cancellation may settle while this already-waiting caller sleeps; report it without resuming
            if result and result["status"] == "waiting":
                current = engine.ledger.get(release)
                if current["status"] == "cancelled":
                    return current
            raise
        if result["status"] != "waiting" or time.monotonic() >= deadline:
            return result
        time.sleep(min(5, max(0, deadline - time.monotonic())))


# Serve through the same owned listener for normal and explicitly retained read-only artifact inspection
def serve_preview(owner, release, component, port, active_target=False):
    import signal
    from .delivery.serving import Sessions
    sessions = Sessions()
    previous = signal.getsignal(signal.SIGTERM)

    # Termination closes only this foreground process's owned listener
    def terminate(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, terminate)
    try:
        session = sessions.start(owner, release, component, port, 'target' if active_target else 'revision')
        print(f"Static {session['mode']}: {session['url']} | Ctrl-C stops this session", flush=True)
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        sessions.close()
        signal.signal(signal.SIGTERM, previous)
    return {'status': 'preview-stopped'}


# Route every entry point through the shared deployment owners
def dispatch(args):
    if args.command == 'captures':
        from .delivery import captures
        state = (args.state or args.project.resolve().parent / '.deploy-ops').resolve()
        return captures.list_records(state, args.offset, args.limit) if args.capture_action == 'list' else captures.inspect(state, args.capture_id)
    if args.command == 'rollout' and args.rollout_action == 'read':
        from .delivery.rollouts import read_snapshot
        return read_snapshot(args.file)
    if args.command == 'rollout' and args.rollout_action in {'inspect', 'recovery'}:
        from .delivery import rollouts
        from .delivery.retained import Retained
        state = (args.state or args.project.resolve().parent / '.deploy-ops').resolve()
        owner = Retained(state)
        return rollouts.inspect(owner, args.release) if args.rollout_action == 'inspect' else rollouts.recovery_status(owner, args.release)
    if args.command == 'artifact':
        from .delivery import artifacts
        from .delivery import signatures
        if args.artifact_action == 'signature-options':
            return signatures.options()
        if args.artifact_action == 'key-info':
            return signatures.key_info(args.file)
        if args.artifact_action == 'signature-inspect':
            return signatures.inspect(args.directory)
        if args.artifact_action in {'files', 'verify'}:
            state = (args.state or args.project.resolve().parent / '.deploy-ops').resolve()
            if args.artifact_action == 'files':
                return signatures.files(state, args.release, args.component, args.query, args.offset, args.limit)
            profile = signatures.Profile(expected_key_sha256=args.key_sha256, timeout_seconds=args.timeout_seconds)
            return signatures.verify(state, args.release, args.component, args.file, args.key, args.bundle, args.out, profile, args.project.resolve().parent)
        if args.artifact_action == 'options':
            return artifacts.options()
        if args.artifact_action == 'inspect':
            return artifacts.inspect(args.directory)
        profile = artifacts.Profile(inventory=args.inventory, vulnerabilities=args.vulnerabilities,
                                    allow_network=args.allow_network, timeout_seconds=args.timeout_seconds)
        state = (args.state or args.project.resolve().parent / '.deploy-ops').resolve()
        return artifacts.collect(state, args.release, args.component, args.out, profile, args.project.resolve().parent)
    if args.command == 'backup':
        from .delivery import backup
        if args.backup_action == 'options':
            state = (args.state or args.project.resolve().parent / '.deploy-ops').resolve()
            return backup.options(args.project, state)
        if args.backup_action == 'inspect':
            return backup.inspect(args.directory)
        if args.backup_action == 'inspect-restored':
            return backup.inspect_restored(args.directory, args.state_directory)
        if args.backup_action == 'restore':
            return backup.restore(args.directory, args.workspace_out, args.state_out, expected_digest=args.digest)
        state = (args.state or args.project.resolve().parent / '.deploy-ops').resolve()
        return backup.capture(args.project, state, args.out)
    if args.command == 'preview' and args.retained:
        from .delivery.retained import Retained
        state = (args.state or args.project.resolve().parent / '.deploy-ops').resolve()
        return serve_preview(Retained(state), args.release, args.component, args.port, args.active_target)
    if args.command == "isolated":
        from .isolated import dispatch as isolated
        return isolated(args)
    if args.command == "gate":
        from . import gate_cli
        # Forward the leading option before the delegated command without duplicating URL validation
        forwarded = ["--url", args.url] if args.url is not None else []
        if args.gate_help:
            forwarded.insert(0, '--help')
        return gate_cli._dispatch(gate_cli.parser().parse_args(forwarded + args.args))
    if args.command == "init":
        from .delivery.scaffold import create
        return {"project": str(create(args.directory))}
    if args.command == "demo":
        from .delivery.demo import run
        return run(args.directory)
    if args.command == "doctor":
        from .delivery.docker import readiness
        return {"python": sys.version.split()[0], "tools": {name: shutil.which(name) for name in
                ("git", "docker", "tofu", "terraform", "aws", "az", "gcloud", "kubectl")},
                "docker_readiness": readiness(args.project.resolve()),
                "note": "Availability is not authentication or a successful live deployment"}
    if args.command == "schema":
        from .delivery.spec import Workspace
        return Workspace.model_json_schema()
    if args.command == "catalog":
        from importlib.resources import files
        return json.loads(files("deploy_ops").joinpath("catalog.json").read_text())
    if args.command == "config":
        if args.action in {'recovery', 'reconcile'}:
            from .delivery.configuration import reconcile_save, save_recovery
            state = (args.state or args.project.resolve().parent / '.deploy-ops').resolve()
            if args.action == 'recovery':
                return save_recovery(args.project, state)
            if args.controller:
                raise Refused('Reconcile a local Save through its local operator context', 403)
            return reconcile_save(args.project, state, args.operation_id or '', args.source_hash or '', args.note or '')
        from .delivery.configuration import add, edit, inspect, patch, read_source, save, source_hash, validate
        if args.action in {"validate", "diff", "save"} and not args.file:
            raise Refused("Choose the proposed TOML draft with --file", 422)
        if args.action == "save" and not args.source_hash:
            raise Refused("Provide the saved workspace --source-hash from config inspect", 422)
        source = read_source((args.file or args.project).resolve())
        if args.draft_out and (args.action not in {"add", "patch", "edit", "reset"} or args.draft_out.resolve() == args.project.resolve()
                               or (args.draft_out.exists() and args.draft_out.samefile(args.project))):
            raise Refused("--draft-out requires add/patch/edit/reset and a different file from the saved workspace", 422)
        creation_fields = ("path", "provider", "repository", "kind", "adapter", "need", "resource_name",
                           "artifact_directory", "destination", "compose_file", "compose_project", "build_command", "check")
        if args.action != "add" and any(getattr(args, key) is not None for key in creation_fields):
            raise Refused("Creation fields require config add", 422)
        if args.action in {"edit", "reset"}:
            if not args.scope or (args.action == "reset" and not args.field) or (args.action == "edit" and args.field):
                raise Refused("Choose --scope and use --field only for config reset", 422)
            result = edit(source, args.name, args.scope, args.values, args.environment or "", args.field or "")
            if args.draft_out:
                args.draft_out.write_text(result["toml"])
            return result
        if args.scope or args.environment or args.field:
            raise Refused("Edit scope fields require config edit or reset", 422)
        if args.action == "inspect":
            return inspect(source)
        if args.action in {"add", "patch"}:
            values = args.values
            if args.action == "add":
                if not isinstance(values, dict):
                    raise Refused("--values must be an object", 422)
                values = dict(values)
                fields = {key: getattr(args, key) for key in ("path", "provider", "repository", "kind", "adapter")}
                fields["needs"] = args.need
                fields["options"] = {key: value for key, value in (("name", args.resource_name),
                    ("directory", args.artifact_directory), ("destination", args.destination),
                    ("file", args.compose_file), ("project", args.compose_project)) if value is not None}
                fields["build"] = {key: value for key, value in (("command", args.build_command),
                    ("checks", args.check)) if value is not None}
                for key, value in fields.items():
                    if value is None or (key in {"build", "options"} and not value):
                        continue
                    if key in values:
                        raise Refused(f"Choose either --values or creation flags for {key}", 422)
                    values[key] = value
            result = (add if args.action == "add" else patch)(source, args.section, args.name, values)
            if args.action == "add":
                result["saved_source_hash"] = source_hash(read_source(args.project.resolve()))
            if args.draft_out:
                args.draft_out.write_text(result["toml"])
            return result
        if args.action in {"validate", "diff"}:
            return validate(source, read_source(args.project.resolve()))
        from .delivery.engine import Engine
        return save(Engine(args.project, args.state, args.controller), source, args.source_hash)

    # Operator decisions never start a deployment worker
    if args.command == "operator":
        from .delivery.engine import Engine
        from .delivery.operator import Operator
        return Operator(Engine(args.project, args.state, args.controller)).call(args.action, args.identifier, args.values)

    # Portable evidence never initializes execution owners or grants imported facts authority
    if args.command == "evidence":
        from .delivery import evidence
        if args.evidence_action == 'inspect':
            document = evidence.inspect(args.file)
            if args.html:
                evidence.write(args.html, evidence.report(document))
            return document
        state = (args.state or args.project.resolve().parent / '.deploy-ops').resolve()
        document = evidence.capture(state, args.release, args.run_id, args.run_attempt)
        content = json.dumps(document, indent=2) + '\n'
        evidence.write(args.out, content)
        return {'capture': document['capture'], 'outcome': document['outcome'], 'upload': document['upload'],
                'sha256': document['sha256'], 'file_sha256': hashlib.sha256(content.encode()).hexdigest(),
                'file': str(args.out)}

    # Retained history is readable without loading workspace, credentials or execution owners
    if args.command in {"status", "events"}:
        from .delivery.ledger import Ledger
        state = (args.state or args.project.resolve().parent / '.deploy-ops').resolve()
        ledger = Ledger.open_existing(state)
        if args.command == "events":
            ledger.get(args.release)
            return ledger.events(args.release)
        history = args.history or args.query or args.environment or args.component or args.outcome or args.limit is not None or args.cursor
        if history:
            if args.release:
                raise Refused('Choose one release ID or history filters, not both', 422)
            return ledger.history(args.query, args.environment, args.component, args.outcome,
                                  args.limit if args.limit is not None else 20, args.cursor)
        return ledger.get(args.release) if args.release else ledger.list()

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
    if args.command == 'rollout':
        from .delivery import rollouts
        action = args.rollout_action
        if action in {'prepare', 'prepare-retirement'}:
            return rollouts.prepare(engine, args.release, args.component, action == 'prepare-retirement')
        if action in {'approve', 'approve-retirement'}:
            return rollouts.approve(engine, args.release, args.digest, action == 'approve-retirement')
        if action in {'cancel', 'cancel-retirement'}:
            return rollouts.cancel(engine, args.release, args.digest, action == 'cancel-retirement')
        if action in {'switch', 'retire'}:
            return rollouts.execute(engine, args.release, action == 'retire')
        if action == 'check':
            return rollouts.check(engine, args.release)
        return rollouts.reconcile(engine, args.release, args.note)
    if args.command == "approve":
        return engine.approve(args.release, args.component, args.digest)
    if args.command == "cancel":
        return engine.cancel(args.release)
    if args.command == "recovery":
        return engine.recovery_status(engine.ledger.get(args.release))
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
        if args.preview_action == "recovery":
            from .delivery.previews import recovery_status
            return recovery_status(engine, engine.ledger.get(args.release))
        if args.preview_action == "prepare-cleanup":
            return prepare_cleanup(engine, args.release)
        if args.preview_action == "approve-cleanup":
            return approve_cleanup(engine, args.release, args.digest)
        if args.preview_action == "cleanup":
            return execute_cleanup(engine, args.release)
        return reconcile_cleanup(engine, args.release, args.note, args.confirm_removed)
    if args.command == "preview":
        return serve_preview(engine, args.release, args.component, args.port, args.active_target)

    # Notification delivery cannot acknowledge or resolve an incident
    if args.command == "incidents":
        from .delivery.incidents import handoff, handoff_preview, list_incidents, open_incident, tick, transition
        if args.incident_action == "list":
            return list_incidents(engine.ledger)
        if args.incident_action == "tick":
            return tick(engine.ledger)
        if args.incident_action == "open":
            return open_incident(engine.ledger, args.release, args.owner, args.note, args.interval_seconds, args.limit,
                                 args.observation_sequence, args.observation_digest)
        if args.incident_action == 'handoff-preview':
            return handoff_preview(engine.ledger, args.incident)
        if args.incident_action == 'handoff':
            return handoff(engine.ledger, args.incident, args.version, args.route_digest, args.note)
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
        if isinstance(result, dict) and args.command == 'artifact' and args.artifact_action == 'verify':
            status = result['report']['status']
            return 0 if status == 'verified' else 3 if status == 'unavailable' else 1
        return 0
    except (Refused, ValidationError, OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2


# Preserve direct execution alongside the installed console entry point
if __name__ == "__main__":
    raise SystemExit(main())
