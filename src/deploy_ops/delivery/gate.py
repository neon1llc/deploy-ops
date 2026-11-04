# src/deploy_ops/delivery/gate.py - Reuse one approval state machine for local sandboxes and OIDC workers.

import hashlib
import os
from pathlib import Path

import httpx

from ..client import Client, github_token
from ..clouds import environment as federate
from ..config import Policy, Settings, Target
from ..errors import Refused
from ..models import Claim, Completion, Decision, DelegationSpec, Proposal, Resolution, Source
from ..service import Controller
from ..store import Store
from .spec import Workspace
from .access import binding, check_paths, check_preview, credentials, load_access


# Keep long target labels bounded without losing their distinguishing suffix
def target_name(project: str, environment: str, component: str) -> str:
    name = f"{project}-{environment}-{component}"
    return name if len(name) <= 63 else name[:54] + "-" + hashlib.sha256(name.encode()).hexdigest()[:8]


# Keep local authority separate from cloud workflow identity
class LocalGate:
    """Local mode records operator authority. It does not impersonate GitHub OIDC."""

    supports_receipts = False

    # Register only local environments with bounded local policies
    def __init__(self, spec: Workspace, store: Store, access_file: Path | None = None,
                 source_roots: list[Path] | None = None):
        targets = {}
        self.source_roots = source_roots or []
        self.access_file = access_file
        self.access = None
        self.access_error = "No local cloud grant configured"
        if access_file:
            try:
                self.access = load_access(access_file, spec.digest)
                self.access_error = ""
            except Refused as error:
                self.access_error = str(error)
        self.grants = {}
        for environment, config in spec.environments.items():
            grant = self.access.environments.get(environment) if self.access else None
            if config.provider != "local":
                if grant is None:
                    continue
                if grant.provider != config.provider or config.production:
                    raise Refused("Local cloud grants require the exact nonproduction provider", 403)
                for component in spec.components:
                    if spec.component(component, environment).adapter not in grant.adapters:
                        continue
                    name = target_name(spec.name, environment, component)
                    self.grants[name] = grant
                    identity = binding(self.access, grant)
                    targets[name] = Target(
                        repository="local/" + spec.name, repository_id="1", repository_owner_id="1",
                        workflow_ref="neon1llc/deploy-ops/.github/workflows/local.yml@" + "0" * 40,
                        environment=environment, planner_environment=environment + "-plan", cloud=config.provider,
                        engines=["delivery"], identity=identity, planner_identity=identity, delivery_digest=spec.digest,
                        allow_local_operator=True, local_cloud=True,
                        # Exact grant resource types replace the generic denylist for this reviewed scope
                        policy=Policy(blocked_types=[], auto_actions=[action for action in ("create", "update") if action in grant.actions],
                                      allow_delegation=False, require_cost_estimate=False,
                                      allowed_actions=grant.actions, max_changes=grant.max_changes),
                    )
                continue
            for component in spec.components:
                name = target_name(spec.name, environment, component)
                targets[name] = Target(
                    repository="local/" + spec.name, repository_id="1", repository_owner_id="1",
                    workflow_ref="neon1llc/deploy-ops/.github/workflows/local.yml@" + "0" * 40,
                    environment=environment, planner_environment=environment + "-plan", cloud="local",
                    engines=["delivery"], identity={}, planner_identity={}, delivery_digest=spec.digest,
                    allow_local_operator=True, production=config.production,
                    policy=Policy(allow_delegation=not config.production and not config.preview, require_cost_estimate=False,
                                  allowed_actions=["create", "update", "read", "no-op"] + (["delete"] if config.preview else []),
                                  auto_types=["local_directory", "application_static"]),
                )
        self.controller = Controller(Settings(targets=targets, database=str(store.path)), store) if targets else None
        self.spec = spec

    # Label local execution explicitly instead of fabricating OIDC claims
    def source(self, release: dict, environment: str) -> Source:
        return Source(kind="local", repository="local/" + self.spec.name, repository_id="1", repository_owner_id="1",
                      ref="refs/heads/main", sha=release["source_digest"][:40], actor="local-operator", actor_id="local",
                      run_id=release["id"], run_attempt="1", event_name="workflow_dispatch", environment=environment,
                      job_workflow_ref="neon1llc/deploy-ops/.github/workflows/local.yml@" + "0" * 40,
                      job_workflow_sha="0" * 40, subject="local-operator")

    # Refuse cloud targets before source preparation begins
    def context(self, target: str) -> dict:
        if not self.controller or target not in self.controller.settings.targets:
            raise Refused("Cloud delivery requires a remote OIDC controller or an explicit local cloud grant. " + self.access_error, 403)
        self.grant(target)
        configured = self.controller.settings.targets[target]
        return {"cloud": configured.cloud, "identity": configured.identity, "config_digest": self.spec.digest,
                "local_target": target}

    # Revocation, expiry and edits take effect before each credentialed command
    def grant(self, target: str):
        if target not in self.grants:
            return None
        current = load_access(self.access_file, self.spec.digest)
        if current != self.access:
            raise Refused("Local cloud grant changed; reopen the worker and prepare fresh evidence", 403)
        grant = self.grants[target]
        check_paths(grant, self.source_roots)
        return grant

    # Reuse the controller's evidence and policy checks locally
    def submit(self, proposal: Proposal, release: dict):
        self.context(proposal.target)
        grant = self.grant(proposal.target)
        if grant:
            check_preview(proposal.preview["preview"], grant)
        return self.controller.submit(proposal, self.source(release, release["environment"]))

    # Read current authority rather than trusting a saved release status
    def status(self, request_id: str):
        return self.controller.get(request_id)

    # Bind the local operator's decision to the exact request digest
    def approve(self, request_id: str, digest: str, decision="approve"):
        return self.controller.decide(request_id, Decision(digest=digest, decision=decision), "local-operator")

    # Consume approval for this release and artifact exactly once
    def claim(self, request: dict, release: dict):
        self.grant(request["manifest"]["target"])
        return self.controller.claim(request["id"], Claim(digest=request["digest"],
            artifact_digest=request["manifest"]["artifact_digest"]), self.source(release, release["environment"]))

    # Recheck the lease before continued local execution
    def heartbeat(self, lease):
        self.grant(self.status(lease["request_id"])["manifest"]["target"])
        return self.controller.heartbeat(lease["request_id"], lease["token"])

    # Report the observed outcome through the shared state machine
    def complete(self, lease, outcome):
        return self.controller.complete(lease["request_id"], lease["token"], Completion(outcome=outcome))

    # Restrict local grants to declared local environments
    def delegate(self, environment: str, hours: int, uses: int):
        if environment not in self.spec.environments or self.spec.environments[environment].provider != "local":
            raise Refused("Local delegation is limited to registered local environments", 403)
        return [self.controller.delegate(DelegationSpec(
            target=target_name(self.spec.name, environment, name), hours=hours, uses=uses), "local-operator")
            for name in self.spec.components]

    # Record operator inspection without replaying the deployment
    def recover(self, request_id, note):
        return self.controller.resolve(request_id, Resolution(outcome="failed", note=note), "local-operator")

    # Keep local delivery independent of cloud credentials
    def credentials(self, context: dict) -> dict:
        target = context.get("local_target")
        if not target and context.get("request_id"):
            target = self.status(context["request_id"])["manifest"]["target"]
        grant = self.grant(target)
        return credentials(grant) if grant else {}


# Use the same authority protocol from isolated workflow workers
class RemoteGate:
    supports_receipts = True

    # Keep controller transport settings in one client
    def __init__(self, url: str):
        self.client = Client(url)

    # Fetch the registered delivery contract before preparing work
    def context(self, target: str) -> dict:
        return self.client.call("GET", f"/v1/targets/{target}/delivery-context")

    # Obtain verified public outputs for the named consumer target
    def receipt(self, target: str, receipt_id: str) -> dict:
        return self.client.call("GET", f"/v1/targets/{target}/receipts/{receipt_id}")

    # Submit evidence without embedding execution credentials
    def submit(self, proposal: Proposal, release: dict):
        return self.client.call("POST", "/v1/requests", proposal.model_dump())

    # Read the controller's current request state
    def status(self, request_id: str):
        return self.client.call("GET", f"/v1/requests/{request_id}")

    # Use operator authentication only for the review decision
    def approve(self, request_id: str, digest: str, decision="approve"):
        return self.client.call("POST", f"/v1/admin/requests/{request_id}/decision",
                                {"digest": digest, "decision": decision}, operator=True)

    # Exchange matching request and artifact digests for an execution lease
    def claim(self, request: dict, release: dict):
        return self.client.call("POST", f"/v1/requests/{request['id']}/claim", {
            "digest": request["digest"], "artifact_digest": request["manifest"]["artifact_digest"]})

    # Revalidate the specific lease during execution
    def heartbeat(self, lease):
        return self.client.call("POST", f"/v1/executions/{lease['request_id']}/heartbeat", execution_token=lease["token"])

    # Report completion with the execution credential
    def complete(self, lease, outcome):
        return self.client.call("POST", f"/v1/executions/{lease['request_id']}/complete", {"outcome": outcome},
                                execution_token=lease["token"])

    # Require operator authentication for reconciliation
    def recover(self, request_id, note):
        return self.client.call("POST", f"/v1/admin/requests/{request_id}/resolve", {"outcome": "failed", "note": note},
                                operator=True)

    # Exchange workflow identity only in the credential-handling worker
    def credentials(self, context: dict) -> dict:
        with httpx.Client(timeout=10, follow_redirects=False) as client:
            values = federate(context["cloud"], context["identity"], github_token, context.get("request_id", os.environ.get("GITHUB_RUN_ID", "planning")), client)
        if "GOOGLE_OAUTH_ACCESS_TOKEN" in values:
            values["CLOUDSDK_AUTH_ACCESS_TOKEN"] = values["GOOGLE_OAUTH_ACCESS_TOKEN"]
        return values
