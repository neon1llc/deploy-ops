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


def target_name(project: str, environment: str, component: str) -> str:
    name = f"{project}-{environment}-{component}"
    return name if len(name) <= 63 else name[:54] + "-" + hashlib.sha256(name.encode()).hexdigest()[:8]


class LocalGate:
    """Local mode records operator authority. It does not impersonate GitHub OIDC."""

    def __init__(self, spec: Workspace, store: Store):
        targets = {}
        for environment, config in spec.environments.items():
            if config.provider != "local":
                continue
            for component in spec.components:
                name = target_name(spec.name, environment, component)
                targets[name] = Target(
                    repository="local/" + spec.name, repository_id="1", repository_owner_id="1",
                    workflow_ref="neon1llc/deploy-ops/.github/workflows/local.yml@" + "0" * 40,
                    environment=environment, planner_environment=environment + "-plan", cloud="local",
                    engines=["delivery"], identity={}, planner_identity={}, delivery_digest=spec.digest,
                    allow_local_operator=True, production=config.production,
                    policy=Policy(allow_delegation=not config.production, require_cost_estimate=False,
                                  auto_types=["local_directory", "application_static"]),
                )
        self.controller = Controller(Settings(targets=targets, database=str(store.path)), store) if targets else None
        self.spec = spec

    def source(self, release: dict, environment: str) -> Source:
        return Source(kind="local", repository="local/" + self.spec.name, repository_id="1", repository_owner_id="1",
                      ref="refs/heads/main", sha=release["source_digest"][:40], actor="local-operator", actor_id="local",
                      run_id=release["id"], run_attempt="1", event_name="workflow_dispatch", environment=environment,
                      job_workflow_ref="neon1llc/deploy-ops/.github/workflows/local.yml@" + "0" * 40,
                      job_workflow_sha="0" * 40, subject="local-operator")

    def context(self, target: str) -> dict:
        if not self.controller or target not in self.controller.settings.targets:
            raise Refused("Cloud delivery requires the remote OIDC controller and a trusted workflow", 403)
        return {"cloud": "local", "identity": {}, "config_digest": self.spec.digest}

    def submit(self, proposal: Proposal, release: dict):
        self.context(proposal.target)
        return self.controller.submit(proposal, self.source(release, release["environment"]))

    def status(self, request_id: str):
        return self.controller.get(request_id)

    def approve(self, request_id: str, digest: str, decision="approve"):
        return self.controller.decide(request_id, Decision(digest=digest, decision=decision), "local-operator")

    def claim(self, request: dict, release: dict):
        return self.controller.claim(request["id"], Claim(digest=request["digest"],
            artifact_digest=request["manifest"]["artifact_digest"]), self.source(release, release["environment"]))

    def heartbeat(self, lease):
        return self.controller.heartbeat(lease["request_id"], lease["token"])

    def complete(self, lease, outcome):
        return self.controller.complete(lease["request_id"], lease["token"], Completion(outcome=outcome))

    def delegate(self, environment: str, hours: int, uses: int):
        if environment not in self.spec.environments or self.spec.environments[environment].provider != "local":
            raise Refused("Local delegation is limited to registered local environments", 403)
        return [self.controller.delegate(DelegationSpec(
            target=target_name(self.spec.name, environment, name), hours=hours, uses=uses), "local-operator")
            for name in self.spec.components]

    def recover(self, request_id, note):
        return self.controller.resolve(request_id, Resolution(outcome="failed", note=note), "local-operator")

    def credentials(self, context: dict) -> dict:
        return {}


class RemoteGate:
    def __init__(self, url: str):
        self.client = Client(url)

    def context(self, target: str) -> dict:
        return self.client.call("GET", f"/v1/targets/{target}/delivery-context")

    def receipt(self, target: str, receipt_id: str) -> dict:
        return self.client.call("GET", f"/v1/targets/{target}/receipts/{receipt_id}")

    def submit(self, proposal: Proposal, release: dict):
        return self.client.call("POST", "/v1/requests", proposal.model_dump())

    def status(self, request_id: str):
        return self.client.call("GET", f"/v1/requests/{request_id}")

    def approve(self, request_id: str, digest: str, decision="approve"):
        return self.client.call("POST", f"/v1/admin/requests/{request_id}/decision",
                                {"digest": digest, "decision": decision}, operator=True)

    def claim(self, request: dict, release: dict):
        return self.client.call("POST", f"/v1/requests/{request['id']}/claim", {
            "digest": request["digest"], "artifact_digest": request["manifest"]["artifact_digest"]})

    def heartbeat(self, lease):
        return self.client.call("POST", f"/v1/executions/{lease['request_id']}/heartbeat", execution_token=lease["token"])

    def complete(self, lease, outcome):
        return self.client.call("POST", f"/v1/executions/{lease['request_id']}/complete", {"outcome": outcome},
                                execution_token=lease["token"])

    def recover(self, request_id, note):
        return self.client.call("POST", f"/v1/admin/requests/{request_id}/resolve", {"outcome": "failed", "note": note},
                                operator=True)

    def credentials(self, context: dict) -> dict:
        with httpx.Client(timeout=10, follow_redirects=False) as client:
            values = federate(context["cloud"], context["identity"], github_token, context.get("request_id", os.environ.get("GITHUB_RUN_ID", "planning")), client)
        if "GOOGLE_OAUTH_ACCESS_TOKEN" in values:
            values["CLOUDSDK_AUTH_ACCESS_TOKEN"] = values["GOOGLE_OAUTH_ACCESS_TOKEN"]
        return values
