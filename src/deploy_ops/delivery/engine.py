# src/deploy_ops/delivery/engine.py - Move complete releases through preparation, approval, and verified delivery.

import hashlib
import json
import os
import re
import shutil
import time
import uuid
from pathlib import Path

from .. import __version__
from ..client import file_digest, private_json
from ..config import fingerprint
from ..errors import Refused
from ..models import Agent, Proposal
from ..store import Store
from .adapters import REGISTRY
from .adapters.base import Context
from .files import digest_tree, inside, private_directory, snapshot
from .gate import LocalGate, RemoteGate, target_name
from .ledger import Ledger
from .process import Process
from .spec import load, options


class Engine:
    def __init__(self, project: Path, state: Path | None = None, controller: str | None = None):
        self.path = project.resolve()
        self.spec = load(self.path)
        self.state = (state or self.path.parent / ".deploy-ops").resolve()
        private_directory(self.state)
        self.ledger = Ledger(Store(str(self.state / "state.db")))
        self.gate = RemoteGate(controller) if controller else LocalGate(self.spec, self.ledger.store)
        self.deployments = self.state / "deployments"
        private_directory(self.deployments)

    def deployment_root(self, release: dict) -> Path:
        if release.get("preview"):
            from .previews import namespace
            return namespace(self, release)
        return self.deployments

    def observe(self, release_id: str) -> dict:
        from .observation import observe
        return observe(self, release_id)

    def describe(self, environment: str, component: str | None = None, with_dependencies: bool = True):
        """Inspect the graph without running repository code, builds, or cloud commands."""
        if environment not in self.spec.environments:
            raise Refused("Unknown environment", 422)
        order = self.spec.order(component, with_dependencies)
        return {"project": self.spec.name, "environment": environment, "config_digest": self.spec.digest,
                "provider": self.spec.environments[environment].provider,
                "components": [{"name": name, "adapter": self.spec.component(name, environment).adapter,
                                "repository": self.spec.components[name].repository,
                                "needs": self.spec.components[name].needs,
                                "target": target_name(self.spec.name, environment, name)} for name in order]}

    def _check_promotion(self, origin: dict, environment: str, component: str | None, rollback: bool):
        if origin.get("preview") or self.spec.environments[environment].preview:
            raise Refused("Create a fresh isolated preview; preview promotion and rollback are not supported", 422)
        configured = self.spec.components.get(component)
        if not configured or configured.kind != "application":
            raise Refused("Promotion and rollback require one application component", 422)
        if origin["config_digest"] != self.spec.digest or origin["nodes"].get(component, {}).get("status") != "succeeded":
            raise Refused("Promote a successful artifact under the same workspace contract")
        allowed = self.spec.environments[environment].promote_from
        if (not rollback and origin["environment"] not in allowed) or (rollback and origin["environment"] != environment):
            raise Refused("This promotion or rollback path is not declared", 403)
        source_adapter = origin["nodes"][component]["adapter"]
        target_adapter = self.spec.component(component, environment).adapter
        reusable = {"static", "ecs", "container-apps", "cloud-run", "kubernetes"}
        if source_adapter not in reusable or target_adapter not in reusable:
            raise Refused("This adapter needs its own reviewed promotion workflow", 422)
        if (source_adapter == "static") != (target_adapter == "static"):
            raise Refused("Promotions must preserve artifact kind", 422)

    def artifact_actions(self, release: dict) -> list[dict]:
        """Describe the same artifact reuse paths accepted by release creation."""
        actions = []
        for name in release["nodes"]:
            for environment in self.spec.environments:
                rollback = environment == release["environment"]
                try:
                    self._check_promotion(release, environment, name, rollback)
                except Refused:
                    continue
                missing = [dependency for dependency in self.spec.components[name].needs
                           if not isinstance(self.gate, RemoteGate)
                           and not self.ledger.latest_outputs(environment, dependency, self.spec.digest)]
                actions.append({"component": name, "environment": environment, "rollback": rollback,
                                "available": not missing,
                                "reason": "Deploy dependencies first: " + ", ".join(missing) if missing else ""})
        return actions

    def _reviewed_envelope(self, release: dict, name: str, reader=None) -> dict:
        # Authenticate the exact bytes we parse, including when reusing an earlier release
        path = self.state / "releases" / release["id"] / name / "prepared.json"
        content = reader(path) if reader else path.read_bytes()
        if hashlib.sha256(content).hexdigest() != release["nodes"][name]["artifact_digest"]:
            raise Refused("Reviewed deployment artifact changed")
        return json.loads(content)

    def create(self, environment: str, component: str | None = None, with_dependencies: bool = True,
               agent: str = "", promote: str | None = None, rollback: bool = False,
               image: str | None = None, receipts: dict[str, str] | None = None,
               preview_owner: str = "", preview_hours: int = 1, retain_preview_data: bool = False) -> dict:
        if receipts is not None and (not isinstance(receipts, dict) or any(not isinstance(key, str) or not isinstance(value, str) for key, value in receipts.items())):
            raise Refused("Infrastructure receipts must map component names to receipt IDs", 422)
        receipts = receipts or {}
        if receipts and (not isinstance(self.gate, RemoteGate) or not component):
            raise Refused("Infrastructure receipts require one remote application component", 422)
        if any(not re.fullmatch(r"[0-9a-f]{32}", value) for value in receipts.values()):
            raise Refused("Infrastructure receipt IDs must be 32 lowercase hexadecimal characters", 422)
        graph = self.describe(environment, component, with_dependencies)
        preview_metadata = None
        if self.spec.environments[environment].preview:
            from .previews import validate_creation
            preview_metadata = validate_creation(self, graph, preview_owner, preview_hours, retain_preview_data, with_dependencies)
        elif preview_owner or retain_preview_data:
            raise Refused("Declare an opt-in local preview environment first", 422)
        for node in graph["components"]:
            context = self.gate.context(node["target"])
            if context["config_digest"] != self.spec.digest:
                raise Refused("Register this workspace digest with the authority controller first", 403)
        # Artifact inputs vary by release without widening the registered deployment contract
        artifact_inputs = {}
        if image:
            if not component or promote:
                raise Refused("An image input requires one component and cannot replace a promoted artifact", 422)
            configured = self.spec.component(component, environment)
            repository = configured.options.get("image_repository")
            if configured.adapter not in {"ecs", "container-apps", "cloud-run", "kubernetes"}:
                raise Refused("This adapter does not accept a release-time image", 422)
            reference = isinstance(repository, str) and re.fullmatch(r"\$\{outputs\.[a-z][a-z0-9-]*\.image_repository\}", repository)
            if not isinstance(repository, str) or (not reference and not re.fullmatch(r"[a-z0-9][a-z0-9._:/-]*", repository)):
                raise Refused("Configure an exact image_repository before accepting release-time images", 422)
            if not re.fullmatch((r"[a-z0-9][a-z0-9._:/-]*" if reference else re.escape(repository)) + r"@sha256:[0-9a-f]{64}", image):
                raise Refused("The image must be an immutable digest in the configured image_repository", 422)
            artifact_inputs[component] = {"image": image}
        origin = self.ledger.get(promote) if promote else None
        if origin:
            self._check_promotion(origin, environment, component, rollback)
        release_id = uuid.uuid4().hex
        root = self.state / "releases" / release_id
        private_directory(root)
        nodes, snapshots = {}, {}
        for node in graph["components"]:
            name = node["name"]
            configured = self.spec.component(name, environment)
            folder = root / name
            private_directory(folder)
            source = folder / "source"
            if origin and name == component:
                original = self.state / "releases" / origin["id"] / name
                envelope = self._reviewed_envelope(origin, name)
                if digest_tree(original / "source") != envelope["source_digest"]:
                    raise Refused("Original release source changed. Promotion is not safe")
                shutil.copytree(original / "source", source, ignore=shutil.ignore_patterns(".terraform"))
                source_record = origin["nodes"][name]["source"]
            else:
                source_record = snapshot(self.spec.repositories[configured.repository], self.path.parent, source)
            snapshots[name] = source_record
            nodes[name] = {"status": "queued", "adapter": configured.adapter, "source": source_record, "outputs": {}}
        release = {"id": release_id, "project": self.spec.name, "environment": environment,
                   "config_digest": self.spec.digest, "source_digest": fingerprint(snapshots),
                   "created_at": int(time.time()), "status": "queued", "order": list(nodes), "nodes": nodes,
                   "agent": agent, "origin_release": promote, "rollback": rollback, "artifact_inputs": artifact_inputs,
                   "receipt_inputs": receipts}
        if preview_metadata:
            from .previews import initialize
            release["preview"] = preview_metadata
            initialize(self, release)
        self.ledger.save(release, "release.created", {"environment": environment, "order": list(nodes),
                                                    "actor_kind": "github" if isinstance(self.gate, RemoteGate) else "local"})
        return release

    def _context(self, release: dict, name: str, credentials: dict, lease=None) -> Context:
        component = self.spec.component(name, release["environment"])
        environment = self.spec.environments[release["environment"]]
        folder = self.state / "releases" / release["id"] / name
        source = inside(folder / "source", component.path)
        if not source.is_dir():
            raise Refused("Component working directory does not exist", 422)
        private_directory(folder / "artifacts")
        private_directory(folder / "home")
        private_directory(folder / "build-home")
        outputs = {key: value["outputs"] for key, value in release["nodes"].items() if value["status"] == "succeeded"}
        if isinstance(self.gate, RemoteGate):
            target = target_name(self.spec.name, release["environment"], name)
            bindings = self.gate.context(target).get("receipt_dependencies", {})
            inputs = release.get("receipt_inputs", {})
            if set(bindings) != set(inputs) or set(bindings) != set(component.needs):
                raise Refused("Provide exactly the infrastructure receipts registered for this application", 422)
            for dependency, receipt_id in inputs.items():
                receipt = self.gate.receipt(target, receipt_id)
                outputs[dependency] = {**receipt["outputs"], "region": receipt["region"]}
                release["nodes"][name].setdefault("infrastructure_receipts", {})[dependency] = receipt
        for dependency in component.needs:
            if dependency in outputs:
                continue
            if release.get("preview"):
                raise Refused("Preview dependencies must be deployed in the same isolated release", 422)
            receipt = self.ledger.latest_outputs(release["environment"], dependency, self.spec.digest)
            if not receipt:
                raise Refused(f"Deploy dependency {dependency} first, or include dependencies", 422)
            outputs[dependency] = receipt["outputs"]
            release["nodes"][name].setdefault("dependency_receipts", {})[dependency] = receipt["receipt"]
        deployment_root = self.deployment_root(release)
        if release.get("preview"):
            from .previews import active
            active(self, release)
        resolved = options(component, environment, release["environment"], outputs)
        resolved.update(release.get("artifact_inputs", {}).get(name, {}))
        if release.get("artifact_inputs", {}).get(name, {}).get("image"):
            repository = resolved.get("image_repository", "")
            if not re.fullmatch(re.escape(repository) + r"@sha256:[0-9a-f]{64}", resolved["image"]):
                raise Refused("Image does not match the verified infrastructure registry", 422)

        def heartbeat():
            current = self.ledger.get(release["id"])
            if current["status"] == "cancel_requested":
                raise Refused("Release cancellation requested. Inspect any in-flight provider operation")
            if lease:
                self.gate.heartbeat(lease)

        if component.adapter == "kubernetes":
            path = os.environ.get("DEPLOY_OPS_KUBECONFIG_FILE")
            if not path or not Path(path).is_file():
                raise Refused("Provide a dedicated least-privilege DEPLOY_OPS_KUBECONFIG_FILE", 422)
            credentials = {**credentials, "KUBECONFIG": str(Path(path).resolve())}
        process = Process(folder / "home", lambda event, detail: self.ledger.event(
            release["id"], event, {"component": name, **detail}), credentials, heartbeat,
            lease["deadline"] if lease else None)
        return Context(component, name, release["environment"], environment.provider, source,
                       folder / "artifacts", deployment_root, resolved, process, release["id"])

    def _login(self, context: Context, credentials: dict):
        # CLI login caches remain in a per-stage private home, never in the human's home
        if context.component.adapter == "container-apps":
            context.command(["az", "login", "--service-principal", "--username", credentials["ARM_CLIENT_ID"],
                             "--tenant", credentials["ARM_TENANT_ID"], "--federated-token", credentials["ARM_OIDC_TOKEN"],
                             "--output", "none"])

    def _build(self, context: Context, release: dict):
        origin = release.get("origin_release")
        previous_release = self.ledger.get(origin) if origin else None
        if previous_release and previous_release["nodes"].get(context.name, {}).get("status") == "succeeded":
            folder = self.state / "releases" / origin / context.name
            previous = self._reviewed_envelope(previous_release, context.name)
            if digest_tree(folder / "artifacts") != previous["artifact_tree"]:
                raise Refused("Original release artifacts changed. Promotion is not safe")
            if context.component.adapter == "static":
                shutil.copytree(folder / "artifacts" / "site", context.stage / "site")
            elif "image" in previous["prepared"]:
                context.options["image"] = previous["prepared"]["image"]
            return
        # Build commands receive no cloud writer, GitHub OIDC, or operator credentials
        clean = Process(context.stage.parent / "build-home", context.process.emit, heartbeat=context.process.heartbeat)
        for command in context.component.build.checks:
            clean.run(command, context.source, context.component.timeout_seconds)
        if context.component.build.command:
            clean.run(context.component.build.command, context.source, context.component.timeout_seconds)
        build = context.component.build
        if build.image:
            if not build.push or not re.fullmatch(r"[a-z0-9][a-z0-9./_-]+", build.image):
                raise Refused("Automatic container builds require a registry repository and push=true", 422)
            metadata = context.stage / "build-metadata.json"
            tag = build.image + ":" + release["nodes"][context.name]["source"]["digest"][:16]
            registry_file = os.environ.get("DEPLOY_OPS_REGISTRY_CONFIG_FILE")
            if not registry_file:
                raise Refused("Set DEPLOY_OPS_REGISTRY_CONFIG_FILE to a scoped Docker auths-only config", 422)
            registry = json.loads(Path(registry_file).read_text())
            if set(registry) != {"auths"} or not isinstance(registry["auths"], dict):
                raise Refused("Registry credentials must use auths only, without personal credential helpers", 422)
            private_json(context.stage.parent / "build-home" / "config.json", registry)
            clean.env["DOCKER_CONFIG"] = str(context.stage.parent / "build-home")
            try:
                clean.run(["docker", "buildx", "build", "--push", "--metadata-file", str(metadata),
                           "--file", str(inside(context.source, build.dockerfile)), "--tag", tag,
                           str(inside(context.source, build.context))], context.source, context.component.timeout_seconds)
            finally:
                (context.stage.parent / "build-home" / "config.json").unlink(missing_ok=True)
            digest = json.loads(metadata.read_text()).get("containerimage.digest", "")
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
                raise Refused("Builder did not return an immutable registry digest")
            context.options["image"] = build.image + "@" + digest

    def _prepare(self, release: dict, name: str):
        node = release["nodes"][name]
        component = self.spec.component(name, release["environment"])
        if isinstance(self.gate, RemoteGate):
            if component.adapter in {"opentofu", "terraform", "workflow"}:
                raise Refused("Use the separate planning/execution gate workflow for cloud IaC and custom commands")
            if not release.get("origin_release") and (component.build.checks or component.build.command or component.build.image):
                raise Refused("Build in a separate unprivileged CI job, then supply an immutable image digest")
        node["status"] = "preparing"
        release["status"] = "running"
        self.ledger.save(release, "component.preparing", {"component": name})
        target = target_name(self.spec.name, release["environment"], name)
        authority = self.gate.context(target)
        if authority["config_digest"] != self.spec.digest:
            raise Refused("The registered workspace contract changed")
        credentials = self.gate.credentials(authority)
        context = self._context(release, name, credentials)
        self._build(context, release)
        receipts = node.get("infrastructure_receipts", {})
        if receipts:
            repositories = {receipt["outputs"]["image_repository"] for receipt in receipts.values()}
            if not any(re.fullmatch(re.escape(repository) + r"@sha256:[0-9a-f]{64}", context.options.get("image", ""))
                       for repository in repositories):
                raise Refused("Artifact is not in the target receipt's image repository", 422)
        self._login(context, credentials)
        adapter = REGISTRY[context.component.adapter]
        if release.get("origin_release") and context.component.adapter == "static" and (context.stage / "site").exists():
            from .adapters.base import preview
            from .adapters.local import pointer
            prepared = {"preview": preview(name, "application_static", False),
                        "destination": context.options.get("destination", str(self.deployments / context.environment / name)),
                        "site_digest": digest_tree(context.stage / "site"),
                        "previous_pointer": pointer(self.deployments, context.options.get("destination", str(self.deployments / context.environment / name)))}
        else:
            prepared = adapter.prepare(context)
        envelope = {"schema_version": 1, "config_digest": self.spec.digest, "adapter": context.component.adapter,
                    "source_digest": digest_tree(context.stage.parent / "source"),
                    "artifact_tree": digest_tree(context.stage), "options": context.options,
                    "prepared": prepared, "infrastructure_receipts": receipts}
        path = context.stage.parent / "prepared.json"
        private_json(path, envelope)
        proposal = Proposal(target=target, engine="delivery", artifact_digest=file_digest(path),
                            preview={"config_digest": self.spec.digest, "preview": prepared["preview"]},
                            tool_version=__version__, estimated_monthly_delta_cents=context.component.estimated_monthly_delta_cents,
                            agent=Agent(label=release["agent"]),
                            receipts={key: {"id": value["id"], "digest": value["digest"]} for key, value in receipts.items()})
        request = self.gate.submit(proposal, release)
        node.update(status="prepared", request_id=request["id"], request_digest=request["digest"],
                    artifact_digest=proposal.artifact_digest, preview=prepared["preview"])
        self.ledger.save(release, "component.prepared", {"component": name, "request_id": request["id"]})

    def _apply(self, release: dict, name: str, request: dict):
        node = release["nodes"][name]
        folder = self.state / "releases" / release["id"] / name
        envelope = self._reviewed_envelope(release, name)
        if envelope["config_digest"] != self.spec.digest or digest_tree(folder / "source") != envelope["source_digest"]:
            raise Refused("Reviewed source or workspace changed")
        if digest_tree(folder / "artifacts") != envelope["artifact_tree"]:
            raise Refused("Prepared files changed after approval")
        lease = self.gate.claim(request, release)
        node["status"] = "running"
        release["status"] = "running"
        self.ledger.save(release, "component.running", {"component": name})
        outcome = "aborted"
        try:
            credentials = self.gate.credentials(lease)
            context = self._context(release, name, credentials, lease)
            context.options = envelope["options"]
            self._login(context, credentials)
            outputs = REGISTRY[context.component.adapter].apply(context, envelope["prepared"])
            self.gate.heartbeat(lease)
            outcome = "succeeded"
            node.update(status="succeeded", outputs=outputs)
        finally:
            report = self.gate.complete(lease, outcome)
            if report["status"] != "succeeded":
                node["status"] = "uncertain"
                raise Refused("Execution needs operator reconciliation before this target can run again")
        self.ledger.save(release, "component.succeeded", {"component": name})

    def advance(self, release_id: str) -> dict:
        release = self.ledger.get(release_id)
        if release["config_digest"] != self.spec.digest:
            raise Refused("Workspace changed. Create a new release instead of resuming")
        if release["status"] in ("succeeded", "failed", "cancelled", "uncertain"):
            raise Refused("Terminal or interrupted releases cannot resume")
        scope = "release:" + release_id
        self.ledger.lock(scope, release_id)
        current_name = None
        try:
            for name in release["order"]:
                current_name = name
                node = release["nodes"][name]
                if node["status"] == "succeeded":
                    continue
                if self.ledger.get(release_id)["status"] == "cancel_requested":
                    release["status"] = "cancelled"
                    return self.ledger.save(release, "release.cancelled")
                if node["status"] in ("running", "preparing"):
                    raise Refused("Interrupted work must be reconciled before a new release")
                if node["status"] == "queued":
                    self._prepare(release, name)
                request = self.gate.status(node["request_id"])
                if request["status"] == "pending":
                    node["status"] = "waiting"
                    release["status"] = "waiting"
                    return self.ledger.save(release, "release.waiting", {"component": name})
                if request["status"] != "approved":
                    raise Refused("Deployment request is " + request["status"])
                self._apply(release, name, request)
            release["status"] = "succeeded"
            return self.ledger.save(release, "release.succeeded")
        except Exception as error:
            node = release["nodes"].get(current_name, {})
            uncertain = node.get("status") in ("running", "uncertain", "preparing")
            node["status"] = "uncertain" if uncertain else "failed"
            release["status"] = node["status"]
            release["reason"] = str(error) if isinstance(error, Refused) else type(error).__name__ + ": inspect the local setup"
            return self.ledger.save(release, "release." + release["status"], {"component": current_name})
        finally:
            # Credential caches are not release artifacts and must not survive a stage
            for name in release["order"]:
                for directory in ("home", "build-home"):
                    shutil.rmtree(self.state / "releases" / release_id / name / directory, ignore_errors=True)
            self.ledger.unlock(scope, release_id)

    def approve(self, release_id: str, component: str, digest: str):
        node = self.ledger.get(release_id)["nodes"].get(component, {})
        if node.get("status") != "waiting" or node.get("request_digest") != digest:
            raise Refused("Review the current pending request and its exact digest")
        return self.gate.approve(node["request_id"], digest)

    def cancel(self, release_id: str):
        release = self.ledger.get(release_id)
        if release["status"] in ("uncertain", "succeeded", "failed", "cancelled"):
            raise Refused("This release is already finished or needs reconciliation")
        if any(node["status"] in ("running", "preparing") for node in release["nodes"].values()):
            release["status"] = "cancel_requested"
        else:
            for node in release["nodes"].values():
                if node.get("request_id") and node["status"] == "waiting":
                    self.gate.approve(node["request_id"], node["request_digest"], "reject")
            release["status"] = "cancelled"
        return self.ledger.save(release, "release.cancel_requested")

    def recover(self, release_id: str, note: str):
        """Release a locked target only after the operator has inspected its actual state."""
        if not isinstance(note, str) or not 10 <= len(note.strip()) <= 500:
            raise Refused("Recovery needs an inspection note of 10 to 500 characters", 422)
        scope = "release:" + release_id
        self.ledger.recovery_lock(scope, release_id)
        try:
            release = self.ledger.get(release_id)
            if release["status"] not in ("running", "uncertain", "cancel_requested"):
                raise Refused("Recovery needs an interrupted release", 422)
            for node in release["nodes"].values():
                if node["status"] == "preparing":
                    node["status"] = "failed"
                if node["status"] in ("running", "uncertain") and node.get("request_id"):
                    self.gate.recover(node["request_id"], note)
                    node["status"] = "failed"
            release.update(status="failed", reason=note)
            return self.ledger.save(release, "release.reconciled", {"note": note})
        finally:
            self.ledger.unlock(scope, release_id)
