# src/deploy_ops/isolated.py - Run an approved GCP recipe through explicit local leases and an isolated guest.
# Usage: python -m deploy_ops.isolated --grant /private/grant.json --state /private/state prepare


import argparse
import base64
import contextlib
import hashlib
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time
import uuid

from .config import Policy, ReceiptPolicy, Settings, Target, canonical, fingerprint
from .delivery.access import private_file
from .delivery.files import private_directory
from .delivery.gate import LocalGate
from .errors import Refused
from .isolated_recipe import HELPER, IsolatedGrant, VERSIONS
from .models import Claim, Completion, Decision, IsolatedPacket, Proposal, Resolution, Source
from .receipts import extract_state
from .runner import _stop
from .service import Controller
from .store import Store


# Read operator configuration without following contributor-selected credential helpers
def load_grant(path: Path, allow_expired: bool = False) -> IsolatedGrant:
    try:
        grant = IsolatedGrant.model_validate_json(private_file(str(path)).read_bytes())
    except Refused:
        raise
    except Exception:
        raise Refused("Invalid isolated operator grant; inspect it privately", 422) from None
    if not allow_expired and time.time() >= grant.expires_at:
        raise Refused("Isolated operator grant expired", 403)
    return grant


# Reject writable shared tools before they can consume operator credentials
def native_tool(value: str):
    path = Path(value).resolve()
    if not path.is_file() or not os.access(path, os.X_OK):
        raise Refused("Registered native tool is not executable", 422)
    for item in (path, *path.parents):
        info = item.stat()
        # macOS administrators already control the trusted host and Homebrew's shared Cellar
        administrative_group = False
        if sys.platform == "darwin":
            import grp
            administrative_group = grp.getgrgid(info.st_gid).gr_name in {"admin", "wheel"}
        if (info.st_uid not in {0, os.getuid()} or info.st_mode & 0o002
                or info.st_mode & 0o020 and not administrative_group):
            raise Refused("Native tool or its directory has untrusted ownership", 403)
    return str(path)


# Keep executable and lock files owned by the operator and outside source control
class NativeBroker:
    # Reuse a short private native CLI home for each impersonation
    def token(self, grant: IsolatedGrant, account: str, heartbeat):
        heartbeat()
        key = private_file(grant.credentials_file)
        try:
            metadata = json.loads(key.read_bytes())
            allowed = {grant.planner_service_account, grant.executor_service_account,
                       grant.app_planner_service_account, grant.app_executor_service_account} - {None}
            if (account not in allowed or metadata.get("type") != "service_account"
                    or metadata.get("project_id") != grant.recipe.project
                    or metadata.get("client_email") != grant.broker_service_account
                    or metadata.get("token_uri") != "https://oauth2.googleapis.com/token"):
                raise ValueError("Credential metadata differs from the registered broker")
        except Exception:
            raise Refused("Broker credential metadata does not match the operator grant", 403) from None
        with tempfile.TemporaryDirectory(prefix="deploy-ops-broker-") as home:
            env = {"PATH": "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin", "HOME": home,
                   "CLOUDSDK_CONFIG": home, "CLOUDSDK_CORE_DISABLE_PROMPTS": "1",
                   "CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE": str(key),
                   "CLOUDSDK_CORE_PROJECT": grant.recipe.project}
            # Native gcloud consumes the existing key and returns only the narrow impersonated token
            process = subprocess.Popen([native_tool(grant.gcloud), "auth", "print-access-token", "--quiet", "--lifetime=900s",
                "--impersonate-service-account=" + account], env=env, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)
            try:
                token, _ = process.communicate(timeout=30)
                heartbeat()
                value = token.decode().strip()
                if process.returncode or not 20 <= len(value) <= 16000 or any(c.isspace() for c in value):
                    raise Refused("Native service-account impersonation failed", 503)
                return value
            finally:
                _stop(process)


# Reap an exited Lima leader before signalling any surviving transport descendants
def _stop_lima(process):
    process.poll()
    try:
        _stop(process)
    except PermissionError:
        # Darwin can deny a signal to a group containing only an unreaped zombie
        if process.poll() is None:
            raise
        _stop(process)


# Validate the actual Lima runtime before the helper can request any token
class LimaRunner:
    # Recheck immutable tool references on every isolated operation
    def __init__(self, grant: IsolatedGrant):
        self.grant = grant

    # Require a running dedicated VM with no host mount or agent forwarding
    def verify(self):
        grant = self.grant
        try:
            raw = subprocess.run([native_tool(grant.limactl), "list", grant.vm, "--json"], capture_output=True,
                                 check=True, timeout=10, env={"PATH": "/usr/bin:/bin", "HOME": str(Path.home())})
            info = json.loads(raw.stdout)
            config = info["config"]
            forwarding = config.get("portForwards", [])
            if (info["name"] != grant.vm or info["status"] != "Running" or info["arch"] != "x86_64"
                    or fingerprint(config) != grant.vm_config_digest or config.get("mounts")
                    or config.get("ssh", {}).get("forwardAgent") or config.get("ssh", {}).get("forwardX11")
                    or config.get("propagateProxyEnv") is not False
                    or not forwarding or not all(item.get("ignore") is True for item in forwarding)):
                raise ValueError("Runtime isolation changed")
        except Exception:
            raise Refused("Dedicated Lima runtime differs from the reviewed isolation configuration", 403) from None

    # Transfer only generated source and temporary authority over the native management channel
    def run(self, packet: dict, mint, heartbeat) -> dict:
        self.verify()
        command = [native_tool(self.grant.limactl), "shell", self.grant.vm, "--", "sudo", "-n", "/usr/bin/python3", "-I", HELPER]
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   start_new_session=True, env={"PATH": "/usr/bin:/bin", "HOME": str(Path.home())})
        incoming = queue.Queue()
        write_error = queue.Queue()

        # Bound guest protocol output without retaining raw provider diagnostics
        def read():
            try:
                while True:
                    line = process.stdout.readline(20_000_001)
                    if not line or len(line) > 20_000_000:
                        raise ValueError("Guest protocol ended")
                    incoming.put(json.loads(line))
            except Exception:
                incoming.put({"error": "Guest channel closed"})

        # Large frozen plans cannot prevent the supervising thread from enforcing deadlines
        def write(value):
            try:
                process.stdin.write((canonical(value) + "\n").encode())
                process.stdin.flush()
            except Exception:
                write_error.put(True)

        threading.Thread(target=read, daemon=True).start()
        sender = threading.Thread(target=write, args=(packet,), daemon=True)
        sender.start()
        ready = False
        last_ping = 0
        try:
            while True:
                heartbeat()
                if time.time() >= packet["deadline"] or not write_error.empty():
                    raise Refused("Isolated authority channel expired")
                try:
                    value = incoming.get(timeout=0.2)
                except queue.Empty:
                    value = None
                if value:
                    if value.get("error"):
                        raise Refused("Isolated operation failed; inspect the target before retrying")
                    if not ready:
                        sender.join(timeout=1)
                        if value != {"ready": True, "packet_digest": packet["packet_digest"]} or sender.is_alive():
                            raise Refused("Guest readiness did not bind the approved packet")
                        # Credentials are minted only after the current helper has verified its trusted runtime
                        secret = mint()
                        heartbeat()
                        write({"token": secret})
                        ready = True
                        last_ping = time.monotonic()
                    elif set(value) == {"result"}:
                        heartbeat()
                        return value["result"]
                    else:
                        raise Refused("Unexpected isolated guest response")
                if ready and time.monotonic() - last_ping >= 5:
                    write({"heartbeat": True})
                    last_ping = time.monotonic()
        finally:
            # EOF reaches the guest supervisor before the local SSH process is terminated
            with contextlib.suppress(Exception):
                process.stdin.close()
            _stop_lima(process)
            process.stdout.close()


# Use one controller for planning leases, approved native plans and application receipts
class IsolatedRoute:
    # Keep operator authorization and runtime injection outside repository content
    def __init__(self, grant_file: Path, state: Path, broker=None, runner=None, workspace=None):
        self.path = grant_file.resolve()
        self.grant = load_grant(self.path, allow_expired=True)
        self.state = state.resolve()
        private_directory(self.state)
        self.workspace = workspace
        self.broker = broker or NativeBroker()
        self.runner = runner or LimaRunner(self.grant)
        self.plan_target = self.grant.name + "-plan"
        self.apply_target = self.grant.name + "-apply"
        targets = {self.plan_target: self.target("manifest", planning=True),
                   self.apply_target: self.target(self.grant.engine)}
        if workspace:
            self._check_workspace(workspace)
            from .delivery.gate import target_name
            app_target = target_name(workspace.name, self.grant.environment, self.grant.app_component)
            targets[app_target] = self.target("delivery")
        self.controller = Controller(Settings(targets=targets, database=str(self.state / "state.db"),
                                     execution_seconds=900), Store(str(self.state / "state.db")))

    # Return only public recipe and controller records without operator file references
    def status(self):
        available, reason = True, "Grant is current; native runtime and cloud access are checked during execution"
        try:
            self.current()
        except Refused as error:
            available, reason = False, str(error)
        requests = [item for item in self.controller.list_requests()
                    if item["manifest"]["target"] in {self.plan_target, self.apply_target}]
        with self.controller.store.connect() as connection:
            rows = connection.execute("SELECT payload FROM audit ORDER BY sequence DESC LIMIT 200").fetchall()
        errors = {}
        for row in rows:
            event = json.loads(row[0])
            if event["event"] == "isolated.worker.refused":
                errors.setdefault(event["object_id"], event["detail"]["reason"])
        for request in requests:
            request["worker_error"] = errors.get(request["id"])
            if request.get("output_receipt"):
                valid, detail = None, "Revalidate this receipt through the registered application before use"
                if not available:
                    valid, detail = False, reason
                elif self.workspace and self.grant.app_component:
                    from .delivery.gate import target_name
                    target = target_name(self.workspace.name, self.grant.environment, self.grant.app_component)
                    try:
                        self.controller.output_receipt(request["id"], target, self.source(target, "receipt-inspection"))
                        valid, detail = True, "Current for the registered application; revalidated when used"
                    except Refused as error:
                        valid, detail = False, str(error)
                request["receipt_status"] = {"valid": valid, "reason": detail}
        return {"enabled": True, "available": available, "reason": reason, "name": self.grant.name,
                "environment": self.grant.environment, "engine": self.grant.engine,
                "recipe": self.grant.recipe.model_dump(), "expires_at": self.grant.expires_at,
                "packet_digest": self.grant.packet_digest,
                "identities": {"planner": self.grant.planner_service_account, "executor": self.grant.executor_service_account,
                               "application_planner": self.grant.app_planner_service_account,
                               "application_executor": self.grant.app_executor_service_account}, "tools": {"version": VERSIONS[self.grant.engine],
                    "provider": "registry.opentofu.org/hashicorp/google@6.50.0", "lock_digest": self.grant.lock_digest,
                    "tool_digest": self.grant.tool_digest, "provider_digest": self.grant.provider_digest},
                "application": {"component": self.grant.app_component, "dependency": self.grant.app_dependency,
                                "workspace_digest": self.grant.workspace_digest} if self.grant.app_component else None,
                "requests": requests, "active": [], "destroy_supported": False}

    # Reject application and unrelated controller IDs at infrastructure entry points
    def request(self, request_id):
        request = self.controller.get(request_id)
        if request["manifest"]["target"] not in {self.plan_target, self.apply_target}:
            raise Refused("Request does not belong to this isolated infrastructure registration", 403)
        return request

    # Record an inspected failure without renewing authority or reviving an invalidated receipt
    def reconcile(self, request_id: str, digest: str, note: str):
        request = self.request(request_id)
        if request["digest"] != digest:
            raise Refused("Reconciliation digest differs from the retained request")
        if not 10 <= len(note) <= 500:
            raise Refused("Record an inspection note between 10 and 500 characters", 422)
        return self.controller.resolve(request_id, Resolution(outcome="failed", note=note), "local-operator")

    # Preserve safe worker refusal evidence in the existing audit owner
    def failed(self, request_id, error):
        reason = str(error) if isinstance(error, Refused) else "Isolated worker failed; inspect the operator setup privately"
        with self.controller.store.transaction() as connection:
            self.controller.store.event(connection, self.controller.now(), "local-operator",
                                        "isolated.worker.refused", request_id, {"reason": reason})

    # Bind app-only releases to one reviewed workspace without permitting source build hooks
    def _check_workspace(self, workspace):
        grant = self.grant
        if workspace.digest != grant.workspace_digest or grant.environment not in workspace.environments:
            raise Refused("Application workspace differs from the isolated registration", 403)
        environment = workspace.environments[grant.environment]
        component = workspace.component(grant.app_component, grant.environment)
        expected = {"project_id": "project", "region": "region", "name": "name", "image_repository": "image_repository"}
        if any(component.options.get(key) != "${outputs." + grant.app_dependency + "." + value + "}"
               for key, value in expected.items()):
            raise Refused("Application destination must come entirely from its registered infrastructure receipt", 403)
        if (environment.provider != "gcp" or environment.production or component.adapter != "cloud-run"
                or component.needs != [grant.app_dependency] or component.build.checks
                or component.build.command or component.build.image):
            raise Refused("Isolated receipt consumption requires one unbuilt nonproduction Cloud Run application", 403)

    # Construct explicit local identities without manufacturing verified workflow claims
    def target(self, engine: str, planning=False):
        grant = self.grant
        accounts = (grant.app_planner_service_account, grant.app_executor_service_account) if engine == "delivery" else (
            grant.planner_service_account, grant.planner_service_account if planning else grant.executor_service_account)
        common = {"project_id": grant.recipe.project, "grant_digest": grant.digest, "packet_digest": grant.packet_digest}
        return Target(repository="local/" + grant.name, ref="local",
            environment=grant.environment, planner_environment=grant.environment + "-plan", cloud="gcp",
            engines=[engine], identity={**common, "service_account": accounts[1]},
            planner_identity={**common, "service_account": accounts[0]}, allow_local_operator=True,
            local_isolated_iac=True, policy=Policy(allow_delegation=False, require_cost_estimate=False),
            receipt_outputs=ReceiptPolicy(format="gcp-cloud-run-v1", backend=grant.recipe.backend) if engine in VERSIONS else None,
            receipt_region=grant.recipe.region if not planning else None,
            receipt_dependencies={grant.app_dependency: self.apply_target} if engine == "delivery" else {},
            delivery_digest=grant.workspace_digest if engine == "delivery" else None)

    # Reload the grant and provider lock before every credential or authority boundary
    def current(self):
        grant = load_grant(self.path)
        if grant != self.grant:
            raise Refused("Isolated grant changed; prepare fresh evidence", 403)
        lock = private_file(grant.lock_file).read_bytes()
        if hashlib.sha256(lock).hexdigest() != grant.lock_digest:
            raise Refused("Reviewed provider lock changed", 403)
        return lock

    # Keep operator identity visibly local while retaining the complete packet digest in target policy
    def source(self, target: str, run: str, planning=False):
        configured = self.controller.target(target)
        return Source(kind="local", repository=configured.repository,
            ref=configured.ref, sha=self.grant.packet_digest[:40], actor="local-operator", actor_id="local",
            run_id=run, run_attempt="1", event_name="workflow_dispatch",
            environment=configured.planner_environment if planning else configured.environment,
            subject="local-operator")

    # Make credentialless planning authorization concrete before any native command starts
    def prepare(self):
        self.current()
        return self.controller.submit(Proposal(target=self.plan_target, engine="manifest", tool_version="isolated-v1",
            artifact_digest=self.grant.packet_digest,
            isolated_packet=IsolatedPacket(module=self.grant.recipe.module(self.grant.engine),
                lock_digest=self.grant.lock_digest, tool_digest=self.grant.tool_digest,
                provider_digest=self.grant.provider_digest),
            preview={"changes": [], "manual_only": True,
            "findings": []}), self.source(self.plan_target, uuid.uuid4().hex, planning=True))

    # Bind the operator decision to the exact displayed request digest
    def approve(self, request_id: str, digest: str):
        self.current()
        return self.controller.decide(request_id, Decision(digest=digest, decision="approve"), "local-operator")

    # Build a fixed guest packet only from the current operator-owned recipe
    def packet(self, stage: str, deadline: int):
        return {"stage": stage, "engine": self.grant.engine,
            "module": base64.b64encode(self.grant.module_bytes()).decode(),
            "lock": base64.b64encode(self.current()).decode(), "lock_digest": self.grant.lock_digest,
            "tool_digest": self.grant.tool_digest, "provider_digest": self.grant.provider_digest,
            "helper_digest": hashlib.sha256(Path(__file__).with_name("isolated_guest.py").read_bytes()).hexdigest(),
            "packet_digest": self.grant.packet_digest, "backend": self.grant.recipe.backend.model_dump(),
            "deadline": min(deadline, self.grant.expires_at, int(time.time()) + 900)}

    # Claim one exact approval and supervise the chosen planning or apply identity
    def execute(self, request_id: str):
        self.current()
        request = self.controller.get(request_id)
        target = request["manifest"]["target"]
        if target not in {self.plan_target, self.apply_target}:
            raise Refused("Use the application worker for this target", 422)
        planning = target == self.plan_target
        stage = "plan" if planning else "apply"
        # Authenticate retained bytes before consuming an apply lease or minting any token
        plan = None
        if not planning:
            plan = private_file(str(self.state / (request_id + ".plan"))).read_bytes()
            if hashlib.sha256(plan).hexdigest() != request["manifest"]["artifact_digest"]:
                raise Refused("Saved plan differs from its approval")
        lease = self.controller.claim(request_id, Claim(digest=request["digest"],
            artifact_digest=request["manifest"]["artifact_digest"]),
            self.source(target, request["manifest"]["source"]["run_id"]))

        # Any grant edit or authority loss closes the guest channel on the next heartbeat
        def heartbeat():
            self.current()
            return self.controller.heartbeat(request_id, lease["token"])

        account = self.grant.planner_service_account if planning else self.grant.executor_service_account
        outcome = Completion(outcome="aborted")
        result = None
        try:
            packet = self.packet(stage, lease["deadline"])
            if plan is not None:
                packet.update(plan=base64.b64encode(plan).decode(), artifact_digest=request["manifest"]["artifact_digest"])
            result = self.runner.run(packet, lambda: self.broker.token(self.grant, account, heartbeat), heartbeat)
            heartbeat()
            if planning:
                content = base64.b64decode(result["plan"], validate=True)
                if len(content) > 8_000_000 or hashlib.sha256(content).hexdigest() != result["artifact_digest"]:
                    raise Refused("Guest plan evidence did not match its retained bytes")
                preview = result["preview"]
                preview["deploy_ops_backend"] = self.grant.recipe.backend.model_dump()
                changes = preview.get("resource_changes", [])
                if any(item.get("address") != "google_cloud_run_v2_service.web"
                       or item.get("type") != "google_cloud_run_v2_service" for item in changes):
                    raise Refused("Native plan contains resources outside the fixed recipe")
                outcome = Completion(outcome="succeeded", exit_code=0)
            else:
                evidence = extract_state(base64.b64decode(result["state"], validate=True), "gcp-cloud-run-v1")
                if evidence.outputs.model_dump() != {key: getattr(self.grant.recipe, key)
                                                     for key in ("project", "region", "name", "image_repository")}:
                    raise Refused("Observed infrastructure outputs differ from the approved recipe")
                outcome = Completion(outcome="succeeded", exit_code=0, infrastructure=evidence)
        finally:
            completed = self.controller.complete(request_id, lease["token"], outcome)
        if completed["status"] != "succeeded":
            raise Refused("Isolated execution requires operator reconciliation")
        if planning:
            proposed = self.controller.submit(Proposal(target=self.apply_target, engine=self.grant.engine,
                tool_version=VERSIONS[self.grant.engine], lock_digest=self.grant.lock_digest,
                artifact_digest=result["artifact_digest"], preview=preview, planning_request=request_id),
                self.source(self.apply_target, request["manifest"]["source"]["run_id"], planning=True))
            path = self.state / (proposed["id"] + ".plan")
            with path.open("xb") as stream:
                os.chmod(path, 0o600)
                stream.write(content)
            return proposed
        return completed


# Consume isolated infrastructure receipts through the existing application lifecycle
class IsolatedGate(LocalGate):
    supports_receipts = True

    # Share the application ledger database with the infrastructure controller
    def __init__(self, spec, state: Path, grant_file: Path):
        self.route = IsolatedRoute(grant_file, state, workspace=spec)
        self.controller = self.route.controller
        self.spec = spec

    # Keep application source provenance local and bound to its frozen snapshot
    def source(self, release, environment):
        from .delivery.gate import target_name
        target = target_name(self.spec.name, self.route.grant.environment, self.route.grant.app_component)
        source = self.route.source(target, release["id"])
        return source.model_copy(update={"sha": release["source_digest"][:40], "environment": environment})

    # Refresh operator scope before source preparation or any native read
    def context(self, target):
        self.route.current()
        configured = self.controller.target(target)
        if configured.engines != ["delivery"]:
            raise Refused("Use the isolated native runner for infrastructure", 403)
        return {"cloud": "gcp", "identity": configured.planner_identity, "config_digest": self.spec.digest,
                "receipt_dependencies": configured.receipt_dependencies, "local_target": target}

    # Recheck grant expiry and edits before accepting an application approval
    def approve(self, request_id, digest, decision="approve"):
        self.route.current()
        return self.controller.decide(request_id, Decision(digest=digest, decision=decision), "local-operator")

    # Reuse the controller's expiry, generation and unresolved-state receipt checks
    def receipt(self, target, receipt_id):
        self.context(target)
        return self.controller.output_receipt(receipt_id, target, self.route.source(target, "receipt-read"))

    # Satisfy the shared local gate's pre-claim and heartbeat scope checks
    def grant(self, target):
        self.route.current()
        return None

    # Require fresh public receipt references before approving any application write
    def submit(self, proposal, release):
        self.context(proposal.target)
        return self.controller.submit(proposal, self.source(release, release["environment"]))

    # Obtain reader or writer tokens only from the corresponding registered identity
    def credentials(self, context):
        self.route.current()
        if context.get("request_id"):
            heartbeat = lambda: self.heartbeat(context)
            identity = self.controller.heartbeat(context["request_id"], context["token"])["identity"]
        else:
            heartbeat = self.route.current
            identity = self.context(context["local_target"])["identity"]
        value = self.route.broker.token(self.route.grant, identity["service_account"], heartbeat)
        return {"GOOGLE_OAUTH_ACCESS_TOKEN": value, "CLOUDSDK_AUTH_ACCESS_TOKEN": value,
                "CLOUDSDK_CORE_PROJECT": self.route.grant.recipe.project}

    # Isolated cloud authority is always manual and cannot inherit local sandbox delegation
    def delegate(self, environment, hours, uses):
        raise Refused("Isolated cloud targets do not allow unattended delegation", 403)


# Share the same explicit action grammar between the main CLI and standalone module
def arguments(parser):
    parser.add_argument("--grant", type=Path, default=os.environ.get("DEPLOY_OPS_ISOLATED_GRANT_FILE"))
    actions = parser.add_subparsers(dest="isolated_action", required=True)
    actions.add_parser("prepare")
    status = actions.add_parser("status")
    status.add_argument("request", nargs="?")
    for name in ("execute", "approve", "reconcile"):
        command = actions.add_parser(name)
        command.add_argument("request")
        if name in {"approve", "reconcile"}:
            command.add_argument("--digest", required=True)
        if name == "reconcile":
            command.add_argument("--note", required=True)


# Keep infrastructure and application operations on one explicitly selected private store
def dispatch(args):
    if not args.grant or not args.state:
        raise Refused("Isolated operations need an operator grant and the shared explicit --state directory", 422)
    route = IsolatedRoute(Path(args.grant), args.state)
    if args.isolated_action == "prepare":
        return route.prepare()
    if args.isolated_action == "status":
        return route.request(args.request) if args.request else route.status()
    route.request(args.request)
    if args.isolated_action == "approve":
        return route.approve(args.request, args.digest)
    if args.isolated_action == "reconcile":
        return route.reconcile(args.request, args.digest, args.note)
    try:
        return route.execute(args.request)
    except Exception as error:
        route.failed(args.request, error)
        raise


# Preserve a standalone entry point for installed isolated workers
def main():
    parser = argparse.ArgumentParser(description="Operate one explicitly registered isolated GCP recipe")
    parser.add_argument("--state", type=Path, required=True)
    arguments(parser)
    try:
        print(json.dumps(dispatch(parser.parse_args()), indent=2))
    except Refused as error:
        parser.exit(1, str(error) + "\n")


# Keep the public module entry point separate from imported orchestration
if __name__ == "__main__":
    main()
