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
from ..models import Caller, Proposal
from ..store import Store
from .adapters import REGISTRY
from .adapters.base import Context
from .files import digest_tree, inside, private_cache_directory, private_directory, remove_private_cache, snapshot
from .gate import LocalGate, RemoteGate, target_name
from .ledger import Ledger
from .process import Process
from .spec import load, options


# Share read-only compatibility reasons with execution admission without claiming current authority
def require_execution_contract(envelope: dict) -> None:
    if envelope['adapter'] == 'compose' and (envelope.get('adapter_contract_version') != 4
            or not envelope.get('prepared', {}).get('docker_binding')):
        raise Refused('Legacy Compose evidence lacks a reviewed Docker target; preserve it and prepare fresh evidence', 422)
    if (envelope['adapter'] == 'static' and envelope['prepared'].get('activation') == 'separate'
            and envelope.get('adapter_contract_version') != 3):
        raise Refused('Separate activation requires a newly reviewed adapter contract 3')
    if envelope['adapter'] == 'kubernetes':
        from .adapters.kubernetes import reviewed
        reviewed(envelope['prepared'])
    if envelope["adapter"] in {"terraform", "opentofu"}:
        from .adapters.iac import require_contract
        require_contract(envelope["prepared"], envelope["adapter"])
    if envelope["adapter"] in {"cloudformation", "cdk"}:
        from .adapters.cloudformation import require_destroy_contract
        require_destroy_contract(envelope["prepared"])


# Coordinate immutable release evidence through one approval and deployment lifecycle
class Engine:
    # Bind the workspace, private ledger and selected authority backend
    def __init__(self, project: Path, state: Path | None = None, controller: str | None = None):
        self.path = project.resolve()
        # Refuse restored write admission before loading configuration, grants or creating directories
        from ..store import require_writable
        require_writable(self.path)
        require_writable((state or self.path.parent / '.deploy-ops').resolve() / 'state.db')
        self.spec = load(self.path)
        self.state = (state or self.path.parent / ".deploy-ops").resolve()
        private_directory(self.state)
        self.ledger = Ledger(Store(str(self.state / "state.db")))
        # Keep operator grants outside snapshots and browser-editable workspace content
        access_path = os.environ.get("DEPLOY_OPS_CLOUD_ACCESS_FILE")
        access = Path(access_path).expanduser().resolve() if access_path else None
        if access and (access.is_relative_to(self.path.parent) or any(
                access.is_relative_to((self.path.parent / repository.path).resolve())
                for repository in self.spec.repositories.values())):
            raise Refused("Keep local cloud access grants outside workspace and source repositories", 422)
        isolated = os.environ.get("DEPLOY_OPS_ISOLATED_GRANT_FILE")
        if isolated:
            from ..isolated import IsolatedGate
            isolated_path = Path(isolated).expanduser().resolve()
            roots = [self.path.parent, *[(self.path.parent / repository.path).resolve() for repository in self.spec.repositories.values()]]
            if controller or access or any(isolated_path.is_relative_to(root) for root in roots):
                raise Refused("Use one isolated operator grant outside workspace and source repositories", 422)
            self.gate = IsolatedGate(self.spec, self.state, isolated_path)
            references = (self.gate.route.grant.credentials_file, self.gate.route.grant.lock_file,
                          self.gate.route.grant.gcloud, self.gate.route.grant.limactl)
            if any(Path(value).resolve().is_relative_to(root) for value in references for root in roots):
                raise Refused("Keep isolated credentials, tools and provider lock outside contributor source", 422)
        else:
            self.gate = RemoteGate(controller) if controller else LocalGate(self.spec, self.ledger.store, access,
                [self.path.parent, *[(self.path.parent / repository.path).resolve() for repository in self.spec.repositories.values()]])
        self.deployments = self.state / "deployments"
        private_directory(self.deployments)


    # Route owned previews into their isolated namespace
    def deployment_root(self, release: dict) -> Path:
        if release.get("preview"):
            from .previews import namespace
            return namespace(self, release)
        return self.deployments


    # Record read-only local drift through the shared observation owner
    def observe(self, release_id: str) -> dict:
        from .observation import observe
        return observe(self, release_id)


    # Read uncertain provider outcomes without replaying an accepted write
    def provider_status(self, release_id: str, name: str, discard: bool = False) -> dict:
        scope = "release:" + release_id
        self.ledger.lock(scope, release_id)
        release, observed = None, None
        cleanup_allowed = False
        try:
            # Revalidate readback and discard against the state owned by this lock, not a pre-lock snapshot
            release = self.ledger.get(release_id)
            if release["config_digest"] != self.spec.digest or name not in release["nodes"]:
                raise Refused("Select a component under the unchanged workspace contract", 422)
            node = release["nodes"][name]
            adapter = REGISTRY[node["adapter"]]
            action = getattr(adapter, "discard" if discard else "inspect", None)
            if action is None:
                raise Refused("This adapter does not support the requested provider operation", 422)
            if discard and release["status"] not in {"waiting", "cancelled", "failed"}:
                raise Refused("Only an unexecuted reviewed change set can be discarded", 409)
            # Pending readback uses durable operation identity without inventing reviewed evidence
            prepared_path = self.state / "releases" / release_id / name / "prepared.json"
            envelope = None
            if node.get("artifact_digest") and prepared_path.is_file():
                envelope = self._reviewed_envelope(release, name)
            elif discard:
                raise Refused("Discard requires a reviewed provider preview; inspect the pending operation first", 409)
            else:
                action = getattr(adapter, "inspect_pending", None)
                if action is None:
                    raise Refused("This adapter has no pending-operation readback contract", 422)
            # Readback and preview cleanup still require the current provider grant
            target = target_name(self.spec.name, release["environment"], name)
            credentials = self.gate.credentials(self.gate.context(target))
            cleanup_allowed = True
            context = self._context(release, name, credentials)
            if envelope:
                context.options = envelope["options"]
            self._login(context, credentials)
            observed = action(context, envelope["prepared"]) if envelope else action(context)
            self.ledger.event(release_id, "provider.discarded" if discard else "provider.observed",
                              {"component": name, "result": observed})
            if discard and release["status"] == "waiting":
                self.cancel(release_id)
            return observed
        finally:
            try:
                if cleanup_allowed:
                    cleanup = self._cleanup_caches(release, [name], 'provider-discard' if discard else 'provider-readback')
                    if isinstance(observed, dict) and name in cleanup:
                        observed['private_cache_cleanup'] = cleanup[name]
            finally:
                self.ledger.unlock(scope, release_id)


    # Keep primary operation results distinct from private-cache removal and recording
    def _cleanup_caches(self, release: dict, names: list[str], operation: str) -> dict:
        observed = {}
        for name in names:
            previous = release['nodes'][name].get('private_cache_cleanup', {})
            if not previous:
                continue
            categories = {category: remove_private_cache(self.state, release['id'], name, category)
                          for category in ('home', 'build-home')}
            if all(value == 'absent' for value in categories.values()) and (
                    not previous or previous.get('state') in {'removed', 'absent'}):
                continue
            state = ('failed' if 'failed' in categories.values() else 'unverified' if 'unverified' in categories.values()
                     else 'absent' if all(value == 'absent' for value in categories.values()) else 'removed')
            result = {'state': state, 'categories': categories, 'operation': operation,
                      'observed_at': int(time.time()), 'recording': 'recorded'}
            if state not in {'removed', 'absent'}:
                result['warning'] = 'Temporary private cache cleanup was not verified. Inspect it through the original local operator; do not repeat a deployment to clear it.'
            release['nodes'][name]['private_cache_cleanup'] = result
            observed[name] = result
        if observed:
            try:
                self.ledger.cache_observation(release['id'], observed, record_event=True)
            except Exception:
                # A recording failure cannot undo a confirmed result or justify replaying it
                for result in observed.values():
                    result.update(recording='unverified', warning='Private cache cleanup recording is unavailable. Preserve the primary operation result and inspect through the original local operator; do not replay deployment.')
        return observed


    # Expose the dependency graph without executing source or provider commands
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


    # Require a successful compatible artifact and a declared environment path
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


    # Offer only the reuse paths that release creation will accept
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
                           if not self.gate.supports_receipts
                           and not self.ledger.latest_outputs(environment, dependency, self.spec.digest)]
                actions.append({"component": name, "environment": environment, "rollback": rollback,
                                "available": not missing,
                                "reason": "Deploy dependencies first: " + ", ".join(missing) if missing else ""})
        return actions


    # Authenticate retained bytes before parsing deployment evidence
    def _reviewed_envelope(self, release: dict, name: str, reader=None) -> dict:
        # Authenticate the exact bytes we parse, including when reusing an earlier release
        path = self.state / "releases" / release["id"] / name / "prepared.json"
        content = reader(path) if reader else path.read_bytes()
        if hashlib.sha256(content).hexdigest() != release["nodes"][name]["artifact_digest"]:
            raise Refused("Reviewed deployment artifact changed")
        envelope = json.loads(content)
        if envelope.get('schema_version') not in {1, 2} or envelope.get('adapter_contract_version', 1) not in {1, 2, 3, 4}:
            raise Refused("Unsupported prepared format. Inspect with the original runtime or prepare fresh evidence", 422)
        if envelope.get('adapter_contract_version', 1) == 2 and envelope.get('adapter') not in {'directory', 'static'}:
            raise Refused("Unsupported adapter contract. Inspect with the original runtime", 422)
        if envelope.get('adapter_contract_version') == 3 and (envelope.get('adapter') != 'static'
                or envelope.get('prepared', {}).get('activation') != 'separate'):
            raise Refused('Unsupported separate-activation contract', 422)
        if envelope.get('adapter_contract_version') == 4 and envelope.get('adapter') != 'compose':
            raise Refused('Unsupported Docker-bound adapter contract', 422)
        if envelope.get('adapter') != release['nodes'][name]['adapter']:
            raise Refused("Prepared adapter differs from the retained release", 422)
        return envelope


    # Freeze selected repositories and bind release inputs before preparing changes
    def create(self, environment: str, component: str | None = None, with_dependencies: bool = True,
               agent: str = "", promote: str | None = None, rollback: bool = False,
               image: str | None = None, receipts: dict[str, str] | None = None,
               preview_owner: str = "", preview_hours: int = 1, retain_preview_data: bool = False) -> dict:
        from .configuration import boundary
        with boundary(self):
            if load(self.path).digest != self.spec.digest:
                raise Refused("The workspace changed; reload it before creating a release")
            return self._create(environment, component, with_dependencies, agent, promote, rollback, image,
                                receipts, preview_owner, preview_hours, retain_preview_data)


    # Capture source and persist the release while holding the workspace boundary
    def _create(self, environment: str, component: str | None, with_dependencies: bool,
                agent: str, promote: str | None, rollback: bool, image: str | None,
                receipts: dict[str, str] | None, preview_owner: str, preview_hours: int,
                retain_preview_data: bool) -> dict:
        if receipts is not None and (not isinstance(receipts, dict) or any(not isinstance(key, str) or not isinstance(value, str) for key, value in receipts.items())):
            raise Refused("Infrastructure receipts must map component names to receipt IDs", 422)
        receipts = receipts or {}
        if receipts and (not self.gate.supports_receipts or not component):
            raise Refused("Infrastructure receipts require one registered application component", 422)
        if any(not re.fullmatch(r"[0-9a-f]{32}", value) for value in receipts.values()):
            raise Refused("Infrastructure receipt IDs must be 32 lowercase hexadecimal characters", 422)
        graph = self.describe(environment, component, with_dependencies)
        # Known local Docker prerequisites stop before capture or any dependency can execute
        from .docker import binding
        for selected in graph['components']:
            if selected['adapter'] == 'compose':
                if type(self.gate) is not LocalGate:
                    raise Refused('Explicit Docker profiles require the existing local operator context', 403)
                binding(self.path, self.spec, environment, selected['name'])
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
        # Reuse an earlier artifact only after checking the declared promotion contract
        origin = self.ledger.get(promote) if promote else None
        if origin:
            self._check_promotion(origin, environment, component, rollback)
        # Refuse known missing source before allocating an attempt; promotion can reuse its retained source
        from . import captures
        selected = {node['name']: self.spec.component(node['name'], environment) for node in graph['components']}
        for name, configured in selected.items():
            if origin and name == component:
                continue
            repository = self.spec.repositories[configured.repository]
            source_root = inside(self.path.parent, repository.path)
            if not source_root.is_dir():
                raise Refused('Repository ' + configured.repository + ' directory is missing; no source capture started', 422)
            if not repository.allow_uncommitted and not (source_root / '.git').exists():
                raise Refused('Initialize Git or explicitly enable local working-tree snapshots; no source capture started', 422)
        # Capture evidence uses the intended release ID without registering a deployment early
        release_id = uuid.uuid4().hex
        chosen = {value.repository: self.spec.repositories[value.repository] for value in selected.values()}
        root, attempt = captures.begin(self.state, release_id, self.spec.name, environment, self.spec.digest,
                                       list(selected), chosen, origin['id'] if origin else None, component if origin else None)
        try:
            nodes, snapshots, repositories = {}, {}, {}
            for node in graph["components"]:
                name = node["name"]
                configured = selected[name]
                folder = root / name
                private_directory(folder)
                source = folder / "source"
                if origin and name == component:
                    captures.update(root, attempt, "copying-component", component=name, repository=configured.repository)
                    original = self.state / "releases" / origin["id"] / name
                    envelope = self._reviewed_envelope(origin, name)
                    if digest_tree(original / "source") != envelope["source_digest"]:
                        raise Refused("Original release source changed. Promotion is not safe")
                    shutil.copytree(original / "source", source, ignore=shutil.ignore_patterns(".terraform"))
                    source_record = origin["nodes"][name]["source"]
                else:
                    # Capture each selected repository once so components cannot observe different moving refs
                    if configured.repository not in repositories:
                        captures.update(root, attempt, 'capturing-repository', repository=configured.repository, component=name)
                        captured = root / 'repositories' / configured.repository
                        source_record = snapshot(self.spec.repositories[configured.repository], self.path.parent, captured)
                        repositories[configured.repository] = (captured, source_record)
                        captures.update(root, attempt, 'capturing-repository', captured_repositories={
                            **attempt['captured_repositories'], configured.repository: source_record})
                    captured, source_record = repositories[configured.repository]
                    captures.update(root, attempt, 'copying-component', component=name, repository=configured.repository)
                    shutil.copytree(captured, source)
                snapshots[name] = source_record
                captures.update(root, attempt, 'copying-component', copied_components=list(snapshots))
                nodes[name] = {"status": "queued", "adapter": configured.adapter, "source": source_record, "outputs": {}}
            release = {"id": release_id, "project": self.spec.name, "environment": environment,
                       "config_digest": self.spec.digest, "source_digest": fingerprint(snapshots),
                       "created_at": int(time.time()), "status": "queued", "order": list(nodes), "nodes": nodes,
                       "caller_label": agent, "origin_release": promote, "rollback": rollback, "artifact_inputs": artifact_inputs,
                       "receipt_inputs": receipts}
            captures.update(root, attempt, 'validating-workspace', source_digest=fingerprint(snapshots))
            if preview_metadata:
                from .previews import initialize
                release["preview"] = preview_metadata
                initialize(self, release)
            if load(self.path).digest != self.spec.digest:
                raise Refused("The workspace changed during source capture; reload it before creating a release")
            captures.update(root, attempt, 'registration-requested')
            self.ledger.save(release, "release.created", {"environment": environment, "order": list(nodes),
                                                        "actor_kind": "github" if isinstance(self.gate, RemoteGate) else "local"})
            return release
        except BaseException as error:
            reason = str(error) if isinstance(error, Refused) else 'Source capture stopped before returning a registration result'
            # Preserve the last durable phase if recording this refusal itself cannot finish
            from contextlib import suppress
            with suppress(Exception):
                captures.update(root, attempt, 'refused' if isinstance(error, Exception) else 'interrupted',
                                stopped_during=attempt['phase'], reason=reason)
            if not isinstance(error, Exception):
                raise
            raise Refused('Source capture ' + release_id + ' did not finish. ' + reason +
                          '. Inspect with captures show ' + release_id, getattr(error, 'status', 409)) from error


    # Resolve component dependencies and keep process authority scoped to this release
    def _context(self, release: dict, name: str, credentials: dict, lease=None, reviewed=None) -> Context:
        component = self.spec.component(name, release["environment"])
        environment = self.spec.environments[release["environment"]]
        folder = self.state / "releases" / release["id"] / name
        source = inside(folder / "source", component.path)
        if not source.is_dir():
            raise Refused("Component working directory does not exist", 422)
        private_directory(folder / "artifacts")
        recorded = bool(release['nodes'][name].get('private_cache_cleanup'))
        # Refuse known unknown entries before allocating either runtime home
        for category in ('home', 'build-home'):
            private_cache_directory(self.state, folder / category, recorded, create=False)
        for category in ('home', 'build-home'):
            private_cache_directory(self.state, folder / category, recorded)
        # Publish an incomplete cleanup observation before caches can be used by native commands
        release['nodes'][name]['private_cache_cleanup'] = {
            'state': 'unverified', 'operation': 'cache-use', 'observed_at': int(time.time()),
            'recording': 'pending', 'warning': 'No completed private cache cleanup observation for this operation.'}
        self.ledger.cache_observation(release['id'], {name: release['nodes'][name]['private_cache_cleanup']})
        if reviewed is not None:
            # Execution consumes the exact frozen resolution without rewriting producer provenance
            resolved = reviewed['options']
            deployment_root = self.deployment_root(release)
        else:
            outputs = {key: value["outputs"] for key, value in release["nodes"].items() if value["status"] == "succeeded"}
            if self.gate.supports_receipts:
                target = target_name(self.spec.name, release["environment"], name)
                bindings = self.gate.context(target).get("receipt_dependencies", {})
                inputs = release.get("receipt_inputs", {})
                if set(bindings) != set(inputs) or set(bindings) != set(component.needs):
                    raise Refused("Provide exactly the infrastructure receipts registered for this application", 422)
                for dependency, receipt_id in inputs.items():
                    receipt = self.gate.receipt(target, receipt_id)
                    outputs[dependency] = {**receipt["outputs"], "region": receipt["region"]}
                    release["nodes"][name].setdefault("infrastructure_receipts", {})[dependency] = receipt
            # Reuse completed outputs only under this same environment and workspace contract
            local_dependencies = {}
            for dependency in component.needs:
                if dependency in outputs:
                    if dependency not in release['nodes'][name].get('infrastructure_receipts', {}):
                        local_dependencies[dependency] = {'release_id': release['id'], 'outputs_digest': fingerprint(outputs[dependency])}
                    continue
                if release.get("preview"):
                    raise Refused("Preview dependencies must be deployed in the same isolated release", 422)
                receipt = self.ledger.latest_outputs(release["environment"], dependency, self.spec.digest)
                if not receipt:
                    raise Refused(f"Deploy dependency {dependency} first, or include dependencies", 422)
                outputs[dependency] = receipt["outputs"]
                release["nodes"][name].setdefault("dependency_receipts", {})[dependency] = receipt["receipt"]
                local_dependencies[dependency] = {'release_id': receipt['receipt'], 'outputs_digest': fingerprint(receipt['outputs'])}
            if not lease:
                release['nodes'][name]['local_dependencies'] = local_dependencies
            deployment_root = self.deployment_root(release)
            # Resolve public references before applying the release-specific artifact input
            resolved = options(component, environment, release["environment"], outputs)
            resolved.update(release.get("artifact_inputs", {}).get(name, {}))
            if release.get("artifact_inputs", {}).get(name, {}).get("image"):
                repository = resolved.get("image_repository", "")
                if not re.fullmatch(re.escape(repository) + r"@sha256:[0-9a-f]{64}", resolved["image"]):
                    raise Refused("Image does not match the verified infrastructure registry", 422)

        # Preview lifetime and ownership remain current checks even when inputs are frozen
        if release.get("preview"):
            from .previews import active
            active(self, release)

        # Recheck cancellation and current authority before each native command
        def heartbeat():
            current = self.ledger.get(release["id"])
            if current["status"] in {"cancel_requested", "cancelled"}:
                raise Refused("Release cancellation requested. Inspect any in-flight provider operation")
            if lease:
                self.gate.heartbeat(lease)
            elif isinstance(self.gate, LocalGate):
                self.gate.grant(target_name(self.spec.name, release["environment"], name))

        if component.adapter == "kubernetes":
            path = os.environ.get("DEPLOY_OPS_KUBECONFIG_FILE")
            if not path or not Path(path).is_file():
                raise Refused("Provide a dedicated least-privilege DEPLOY_OPS_KUBECONFIG_FILE", 422)
            credentials = {**credentials, "KUBECONFIG": str(Path(path).resolve())}
        # Keep credential caches private and attach heartbeats to every command
        process = Process(folder / "home", lambda event, detail: self.ledger.event(
            release["id"], event, {"component": name, **detail}), credentials, heartbeat,
            lease["deadline"] if lease else None)
        context = Context(component, name, release["environment"], environment.provider, source,
                          folder / "artifacts", deployment_root, resolved, process, release["id"])
        if component.adapter == 'compose':
            from .docker import binding, configure, revalidate
            selected = (revalidate(self.path, self.spec, release['environment'], name, resolved, reviewed['prepared']['docker_binding'])
                        if reviewed else binding(self.path, self.spec, release['environment'], name, resolved))
            configure(context, selected)
        return context


    # Verify the granted local identity before initializing provider CLI state
    def _login(self, context: Context, credentials: dict):
        if isinstance(self.gate, LocalGate):
            from .access import verify
            grant = self.gate.grant(target_name(self.spec.name, context.environment, context.name))
            if grant:
                verify(context, grant)
                if grant.provider == "azure":
                    return
        # CLI login caches remain in a per-stage private home, never in the human's home
        if context.component.adapter in {"container-apps", "arm", "bicep"}:
            context.command(["az", "login", "--service-principal", "--username", credentials["ARM_CLIENT_ID"],
                             "--tenant", credentials["ARM_TENANT_ID"], "--federated-token", credentials["ARM_OIDC_TOKEN"],
                             "--output", "none"])


    # Separate source preparation from credentialed deployment execution
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


    # Refuse a distinct logical writer even when its spelling resolves to the same owned path
    def _check_local_target(self, release: dict, name: str, binding: dict):
        from .adapters.local import physical_target
        # Separate ledgers cannot coordinate writers into nested deployment state
        if any(parent != self.state and (parent / 'state.db').is_file()
               for parent in Path(binding['path']).parents):
            raise Refused('A physical local target cannot cross another delivery state root', 422)
        with self.ledger.store.connect() as connection:
            records = [json.loads(row[0]) for row in connection.execute('SELECT data FROM releases')]
        for previous in records:
            for component, node in previous['nodes'].items():
                owner = [previous['project'], previous['environment'], component]
                if owner == binding['owner'] or node['adapter'] not in {'directory', 'static'}:
                    continue
                retained = node.get('physical_target')
                if retained is None and node.get('artifact_digest'):
                    try:
                        envelope = self._reviewed_envelope(previous, component)
                        path = physical_target(self.deployment_root(previous), previous['environment'], component,
                                               node['adapter'], envelope['prepared'])
                        retained = {'path': path, 'owner': owner}
                    except (OSError, ValueError, KeyError, TypeError, Refused):
                        raise Refused('Retained local target evidence is unavailable for release ' + previous['id']
                                      + '. Inspect it before assigning another local writer') from None
                if retained and retained['path'] == binding['path']:
                    raise Refused('Another component owns this physical local target: ' + '/'.join(owner), 422)


    # Reserve target ownership in existing release evidence before source builds or native preparation
    def _bind_local_target(self, release: dict, name: str, context: Context) -> dict | None:
        from .adapters.local import physical_target
        from .configuration import boundary
        if context.provider != 'local':
            return None
        path = physical_target(context.deployments, context.environment, name, context.component.adapter, context.options)
        if path is None:
            return None
        binding = {'path': path, 'owner': [release['project'], release['environment'], name]}
        with boundary(self):
            self._check_local_target(release, name, binding)
            release['nodes'][name]['physical_target'] = binding
            self.ledger.save(release, 'component.target.bound', {'component': name, 'target': binding})
        return binding


    # Freeze provider evidence before submitting the exact approval request
    def _prepare(self, release: dict, name: str):
        node = release["nodes"][name]
        component = self.spec.component(name, release["environment"])
        # Local cloud credentials may consume reviewed artifacts, never source build hooks
        if self.spec.environments[release["environment"]].provider != "local" and (
                component.build.checks or component.build.command or component.build.image):
            raise Refused("Build or synthesize in an isolated credentialless job, then provide frozen artifacts", 403)
        if isinstance(self.gate, RemoteGate):
            if component.adapter in {"opentofu", "terraform", "workflow"}:
                raise Refused("Use the separate planning/execution gate workflow for cloud IaC and custom commands")
            if not release.get("origin_release") and (component.build.checks or component.build.command or component.build.image):
                raise Refused("Build in a separate unprivileged CI job, then supply an immutable image digest")
        # Known missing local inputs stop before builds or effect-capable native preparation
        if not self.gate.supports_receipts:
            for dependency in component.needs:
                if release['nodes'].get(dependency, {}).get('status') != 'succeeded' and not self.ledger.latest_outputs(
                        release['environment'], dependency, self.spec.digest):
                    raise Refused(f"Deploy dependency {dependency} first, or include dependencies", 422)
        node["status"] = "preparing"
        release["status"] = "running"
        self.ledger.save(release, "component.preparing", {"component": name})
        if release['status'] in {'cancel_requested', 'cancelled'}:
            node['status'] = 'cancelled'
            return
        target = target_name(self.spec.name, release["environment"], name)
        authority = self.gate.context(target)
        if authority["config_digest"] != self.spec.digest:
            raise Refused("The registered workspace contract changed")
        credentials = self.gate.credentials(authority)
        context = self._context(release, name, credentials)
        try:
            physical = self._bind_local_target(release, name, context)
        except Refused:
            # This refusal occurs before any build, login or adapter preparation
            node['status'] = 'queued'
            raise
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
            from .adapters.local import static_prepared
            prepared = static_prepared(context)
        else:
            prepared = adapter.prepare(context)
        # Bind source, artifact tree, options and provider preview into one immutable envelope
        envelope = {"schema_version": 2, "adapter_contract_version": 4 if component.adapter == 'compose' else
                    3 if prepared.get('activation') == 'separate' else (2 if physical else 1),
                    "physical_target": physical,
                    "local_dependencies": node.get('local_dependencies', {}),
                    "config_digest": self.spec.digest, "adapter": context.component.adapter,
                    "source_digest": digest_tree(context.stage.parent / "source"),
                    "artifact_tree": digest_tree(context.stage), "options": context.options,
                    "prepared": prepared, "infrastructure_receipts": receipts}
        path = context.stage.parent / "prepared.json"
        private_json(path, envelope)
        proposal = Proposal(target=target, engine="delivery", artifact_digest=file_digest(path),
                            preview={"config_digest": self.spec.digest, "preview": prepared["preview"]},
                            tool_version=__version__, estimated_monthly_delta_cents=context.component.estimated_monthly_delta_cents,
                            caller=Caller(label=release.get("caller_label", release.get("agent", ""))),
                            receipts={key: {"id": value["id"], "digest": value["digest"]} for key, value in receipts.items()})
        request = self.gate.submit(proposal, release)
        node.update(status="prepared", request_id=request["id"], request_digest=request["digest"],
                    artifact_digest=proposal.artifact_digest, preview=prepared["preview"],
                    warnings=prepared.get("warnings", []))
        if component.adapter == 'kubernetes':
            node['kubernetes_binding'] = {'connection': prepared['kubernetes_request']['connection'],
                                          'uid': prepared['kubernetes_request']['baseline']['uid']}
        if component.adapter == 'compose':
            selected = prepared['docker_binding']
            node['docker_target'] = {key: selected[key] for key in ('project', 'endpoint', 'endpoint_owner_uid', 'daemon_id')}
            node['docker_target'].update({key + '_sha256': value['sha256'] for key, value in selected['tools'].items()})
        if component.adapter in {"terraform", "opentofu"}:
            node.update(infrastructure_backend=prepared["backend"], output_readiness=prepared["output_readiness"])
        self.ledger.save(release, "component.prepared", {"component": name, "request_id": request["id"]})


    # Recheck artifacts before claiming authority and performing provider writes
    def _apply(self, release: dict, name: str, request: dict):
        node = release["nodes"][name]
        folder = self.state / "releases" / release["id"] / name
        envelope = self._reviewed_envelope(release, name)
        require_execution_contract(envelope)
        if envelope["config_digest"] != self.spec.digest or digest_tree(folder / "source") != envelope["source_digest"]:
            raise Refused("Reviewed source or workspace changed")
        if digest_tree(folder / "artifacts") != envelope["artifact_tree"]:
            raise Refused("Prepared files changed after approval")
        # Claim only the exact local producer generations bound into the reviewed envelope
        if envelope['schema_version'] == 1 and self.spec.component(name, release['environment']).needs and not envelope.get('infrastructure_receipts'):
            raise Refused("Legacy local dependencies lack frozen lineage. Preserve this release and prepare fresh evidence")
        # The controller invokes this same check under its claim transaction
        def check_local_inputs():
            if self.ledger.get(release['id'])['status'] in {'cancel_requested', 'cancelled'}:
                raise Refused('Release cancellation requested before execution admission')
            if node['adapter'] == 'compose':
                from .docker import revalidate
                revalidate(self.path, self.spec, release['environment'], name, envelope['options'], envelope['prepared']['docker_binding'])
            if node['adapter'] in {'directory', 'static'} and self.spec.environments[release['environment']].provider == 'local':
                from .adapters.local import physical_target
                binding = envelope.get('physical_target')
                if envelope.get('adapter_contract_version', 1) < 2 or not binding:
                    raise Refused('Legacy local targets lack reviewed ownership. Preserve this release and prepare fresh evidence')
                actual = physical_target(self.deployment_root(release), release['environment'], name,
                                         node['adapter'], envelope['prepared'])
                if actual != binding['path'] or binding['owner'] != [release['project'], release['environment'], name]:
                    raise Refused('Reviewed physical local target changed. Prepare fresh evidence')
                self._check_local_target(release, name, binding)
            for dependency, frozen in envelope.get('local_dependencies', {}).items():
                current = self.ledger.latest_outputs(release['environment'], dependency, self.spec.digest)
                if not current or current['receipt'] != frozen['release_id'] or fingerprint(current['outputs']) != frozen['outputs_digest']:
                    raise Refused("Reviewed local dependency changed or is unresolved. Prepare fresh evidence")
            return self.ledger.worker_binding('release:' + release['id'], release['id'])

        check_local_inputs()
        # Consume approval only after the retained source and artifact checks pass
        lease = (self.gate.claim(request, release, local_input_check=check_local_inputs)
                 if isinstance(self.gate, LocalGate) else self.gate.claim(request, release))
        node["status"] = "running"
        release["status"] = "running"
        self.ledger.save(release, "component.running", {"component": name})
        outcome = "aborted"
        try:
            credentials = self.gate.credentials(lease)
            context = self._context(release, name, credentials, lease, reviewed=envelope)
            self._login(context, credentials)
            outputs = REGISTRY[context.component.adapter].apply(context, envelope["prepared"])
            self.gate.heartbeat(lease)
            outcome = "succeeded"
            node.update(status="succeeded", outputs=outputs)
        # Report uncertain outcomes even when provider execution or heartbeat checks fail
        finally:
            report = self.gate.complete(lease, outcome)
            if report["status"] != "succeeded":
                node["status"] = "uncertain"
                raise Refused("Execution needs operator reconciliation before this target can run again")
        self.ledger.save(release, "component.succeeded", {"component": name})


    # Persist each component transition and stop at review or uncertainty
    def advance(self, release_id: str) -> dict:
        from .configuration import require_saved
        scope = "release:" + release_id
        self.ledger.lock(scope, release_id)
        try:
            # Load and admit this worker's release only after exclusive ownership is established
            require_saved(self.path, self.state)
            release = self.ledger.get(release_id)
            if release["config_digest"] != self.spec.digest:
                raise Refused("Workspace changed. Create a new release instead of resuming")
            if release["status"] in ("succeeded", "failed", "cancelled", "uncertain"):
                raise Refused("Terminal or interrupted releases cannot resume")
            if release['status'] in {'running', 'cancel_requested'} or any(
                    node['status'] in {'running', 'preparing', 'uncertain'} for node in release['nodes'].values()):
                raise Refused("Interrupted work must be inspected through recovery; starting another worker cannot prove the original stopped")
            self.ledger.start_worker(scope, release_id, 'worker')
        except BaseException:
            self.ledger.unlock(scope, release_id)
            raise
        current_name = None
        try:
            for name in release["order"]:
                current_name = name
                node = release["nodes"][name]
                if node["status"] == "succeeded":
                    continue
                if self.ledger.get(release_id)["status"] in {"cancel_requested", "cancelled"}:
                    return self._settle_stop(release)
                if node["status"] in ("running", "preparing"):
                    raise Refused("Interrupted work must be reconciled before a new release")
                if node["status"] == "queued":
                    self._prepare(release, name)
                if self.ledger.get(release_id)['status'] in {'cancel_requested', 'cancelled'}:
                    return self._settle_stop(release)
                request = self.gate.status(node["request_id"])
                if request["status"] == "pending":
                    # Re-observing one exact pending request is not another decision-needed transition
                    waiting = {"request_id": node["request_id"], "request_digest": node["request_digest"]}
                    if release["status"] == "waiting" and node["status"] == "waiting" and node.get("waiting_request") == waiting:
                        release = self.ledger.get(release_id)
                        return release
                    node["status"] = "waiting"
                    node["waiting_request"] = waiting
                    release["status"] = "waiting"
                    return self.ledger.save(release, "release.waiting", {"component": name, **waiting})
                if request["status"] != "approved":
                    if request['status'] in {'running', 'uncertain'}:
                        node['status'] = 'uncertain'
                    raise Refused("Deployment request is " + request["status"])
                self._apply(release, name, request)
                if self.ledger.get(release_id)['status'] in {'cancel_requested', 'cancelled'}:
                    return self._settle_stop(release)
            release["status"] = "succeeded"
            return self.ledger.save(release, "release.succeeded")
        except Exception as error:
            node = release["nodes"].get(current_name, {})
            if self.ledger.get(release_id)['status'] in {'cancel_requested', 'cancelled'} and node.get('status') in {'queued', 'prepared', 'waiting', 'succeeded'}:
                return self._settle_stop(release)
            uncertain = node.get("status") in ("running", "uncertain", "preparing")
            node["status"] = "uncertain" if uncertain else "failed"
            release["status"] = node["status"]
            release["reason"] = str(error) if isinstance(error, Refused) else type(error).__name__ + ": inspect the local setup"
            return self.ledger.save(release, "release." + release["status"], {"component": current_name})
        finally:
            try:
                self._cleanup_caches(release, release['order'], 'advance')
            finally:
                self.ledger.unlock(scope, release_id)


    # Require the exact request currently shown for this component
    def approve(self, release_id: str, component: str, digest: str):
        node = self.ledger.get(release_id)["nodes"].get(component, {})
        if node.get("status") != "waiting" or node.get("request_digest") != digest:
            raise Refused("Review the current pending request and its exact digest")
        return self.gate.approve(node["request_id"], digest)


    # Withdraw unclaimed approval while preserving running-operation uncertainty
    def _settle_stop(self, release: dict):
        for node in release['nodes'].values():
            if node.get('request_id') and node['status'] in {'prepared', 'waiting'}:
                request = self.gate.status(node['request_id'])
                if request['status'] in {'pending', 'approved'}:
                    self.gate.approve(request['id'], request['digest'], 'reject')
                elif request['status'] in {'running', 'uncertain', 'succeeded'}:
                    raise Refused('Inspect the accepted controller operation before settling cancellation')
        release['status'] = 'cancelled'
        return self.ledger.save(release, 'release.cancelled')


    # Withdraw unclaimed approval while preserving running-operation uncertainty
    def cancel(self, release_id: str):
        release = self.ledger.get(release_id)
        if release["status"] in ("uncertain", "succeeded", "failed", "cancelled"):
            raise Refused("This release is already finished or needs reconciliation")
        if any(node["status"] in ("running", "preparing") for node in release["nodes"].values()):
            release["status"] = "cancel_requested"
        else:
            for node in release["nodes"].values():
                if node.get("request_id") and node["status"] in {"prepared", "waiting"}:
                    # A separately inspected controller failure already withdrew execution authority
                    request = self.gate.status(node["request_id"])
                    if request["status"] in {"pending", "approved"}:
                        self.gate.approve(node["request_id"], node["request_digest"], "reject")
                    elif request["status"] not in {"failed", "rejected", "expired", "cancelled"}:
                        raise Refused("Inspect and reconcile the controller execution before cancelling this release")
            release["status"] = "cancelled"
        return self.ledger.save(release, "release.cancel_requested")


    # Expose only recovery supported by this operation owner and observed local worker evidence
    def recovery_status(self, release: dict):
        result = {"release_id": release['id'], "available": False, "worker": "unavailable"}
        if release['status'] not in {'running', 'uncertain', 'cancel_requested'}:
            return {**result, "reason": "Recovery requires an interrupted release; retained inspection remains available."}
        if type(self.gate) is not LocalGate:
            return {**result, "reason": "Use the original remote or isolated operation owner to inspect and reconcile this release."}
        worker = self.ledger.worker_status('release:' + release['id'], release['id'])
        return {**result, "available": worker['state'] in {'dead', 'stopped'},
                "worker": worker['state'], "reason": worker['reason']}


    # Acquire recovery ownership before resolving interrupted release state
    def recover(self, release_id: str, note: str):
        """Release a locked target only after the operator has inspected its actual state."""
        if not isinstance(note, str) or not 10 <= len(note.strip()) <= 500:
            raise Refused("Recovery needs an inspection note of 10 to 500 characters", 422)
        release = self.ledger.get(release_id)
        if release.get("reconciliation") and release["status"] == "failed":
            return release
        available = self.recovery_status(release)
        if not available['available']:
            raise Refused(available['reason'])
        # Recovery rechecks the same evidence transactionally before replacing ownership
        scope = "release:" + release_id
        self.ledger.recovery_lock(scope, release_id)
        try:
            release = self.ledger.get(release_id)
            if release.get("reconciliation") and release["status"] == "failed":
                return release
            if release["status"] not in ("running", "uncertain", "cancel_requested"):
                raise Refused("Recovery needs an interrupted release", 422)
            outcomes = {}
            for node in release["nodes"].values():
                if node["status"] == "preparing":
                    node["status"] = "failed"
                if node["status"] in ("running", "uncertain") and node.get("request_id"):
                    request = self.gate.status(node["request_id"])
                    if request["status"] in {"running", "uncertain"}:
                        request = self.gate.recover(node["request_id"], note)
                    if request["status"] not in {"succeeded", "failed", "cancelled"}:
                        raise Refused("Controller decision is not terminal; inspect its exact request before recovery")
                    # Completion authority is retained even when adapter outputs never reached this journal
                    node["controller_outcome"] = request["status"]
                    node["status"] = "unverified" if request["status"] == "succeeded" else "failed"
                    outcomes[node["request_id"]] = request["status"]
            release.update(status="failed", reason=note, reconciliation={"controller_outcomes": outcomes,
                           "outputs_verified": False, "mutation_replayed": False})
            return self.ledger.save(release, "release.reconciled", {"note": note, "controller_outcomes": outcomes})
        finally:
            self.ledger.unlock(scope, release_id)
