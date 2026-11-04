# src/deploy_ops/models.py - Validate requests before they enter the approval ledger.


from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

# Share exact identifier formats across incoming authority messages
Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
Commit = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]
Slug = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9-]{0,62}$")]
Engine = Literal["terraform", "opentofu", "manifest", "delivery"]
Cloud = Literal["aws", "azure", "gcp", "local"]
Action = Literal["create", "update", "delete", "replace", "read", "no-op", "import", "move"]


# Apply strict field validation to every authority message
class Model(BaseModel):
    """Reject unknown fields instead of silently weakening a request."""

    model_config = ConfigDict(extra="forbid", strict=True)


# Retain the identity claims required to bind source and execution attempts
class Source(Model):
    """Keep issuer-verified identity separate from the agent's own labels."""

    kind: Literal["github", "local"] = "github"
    repository: str
    repository_id: str | None = None
    repository_owner_id: str | None = None
    ref: str
    sha: Commit
    actor: str
    actor_id: str
    run_id: str
    run_attempt: str
    event_name: str
    environment: str
    job_workflow_ref: str | None = None
    job_workflow_sha: Commit | None = None
    subject: str

    # Federated source must retain real issuer claims while local provenance stays explicitly local
    @model_validator(mode="after")
    def verified_fields(self):
        if self.kind == "github" and not all((self.repository_id, self.repository_owner_id,
                                               self.job_workflow_ref, self.job_workflow_sha)):
            raise ValueError("GitHub source requires verified repository and workflow claims")
        return self


# Carry optional caller labels separately from authenticated claims
class Agent(Model):
    """Describe the caller without treating its self-report as authentication."""

    label: str = Field(default="", max_length=120)
    model: str = Field(default="", max_length=120)
    run: str = Field(default="", max_length=160)


# Represent a resource action without retaining its secret values
class Change(Model):
    address: str = Field(min_length=1, max_length=512)
    resource_type: str = Field(min_length=1, max_length=256)
    action: Action


# Keep ambiguous findings and manual-review requirements visible
class Preview(Model):
    changes: list[Change] = Field(default_factory=list, max_length=2000)
    findings: list[str] = Field(default_factory=list, max_length=100)
    manual_only: bool = False


# Bind a dependency receipt by both identifier and content digest
class ReceiptReference(Model):
    id: str = Field(pattern=r"^[0-9a-f]{32}$")
    digest: Digest


# Limit shared infrastructure outputs to the supported ECS contract
class EcsOutputs(Model):
    cluster: str = Field(pattern=r"^arn:aws:ecs:[a-z0-9-]+:[0-9]{12}:cluster/[A-Za-z0-9_-]+$")
    service: str = Field(pattern=r"^arn:aws:ecs:[a-z0-9-]+:[0-9]{12}:service/[A-Za-z0-9_-]+/[A-Za-z0-9_-]+$")
    container: str = Field(pattern=r"^[A-Za-z0-9_-]{1,255}$")
    image_repository: str = Field(pattern=r"^[0-9]{12}\.dkr\.ecr\.[a-z0-9-]+\.amazonaws\.com/[a-z0-9][a-z0-9._/-]+$")


# Keep Cloud Run destinations explicit and independent from runtime credentials
class CloudRunOutputs(Model):
    project: str = Field(pattern=r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
    region: str = Field(pattern=r"^[a-z]+-[a-z]+[0-9]+$")
    name: str = Field(pattern=r"^[a-z](?:[a-z0-9-]{0,47}[a-z0-9])?$")
    image_repository: str = Field(pattern=(
        r"^[a-z]+-[a-z]+[0-9]+-docker\.pkg\.dev/[a-z][a-z0-9-]{4,28}[a-z0-9]/"
        r"[a-z][a-z0-9_-]*/[a-z0-9][a-z0-9._/-]*$"))


# Bind public outputs to one observed state version
class InfrastructureEvidence(Model):
    state_lineage: str = Field(pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
    state_serial: int = Field(ge=0)
    state_digest: Digest
    outputs: EcsOutputs | CloudRunOutputs


# Make the generated public source packet reviewable without exposing broker references
class IsolatedPacket(Model):
    module: dict[str, Any]
    lock_digest: Digest
    tool_digest: Digest
    provider_digest: Digest


# Bind the reviewed plan to artifact, tool and optional dependency evidence
class Proposal(Model):
    target: Slug
    engine: Engine
    artifact_digest: Digest
    preview: dict[str, Any]
    tool_version: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9._+-]+$")
    lock_digest: Digest | None = None
    estimated_monthly_delta_cents: int | None = Field(default=None, ge=-100000000, le=100000000)
    agent: Agent = Field(default_factory=Agent)
    receipts: dict[str, ReceiptReference] = Field(default_factory=dict)
    planning_request: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    isolated_packet: IsolatedPacket | None = None


    # Require dependency identity for saved-plan engines
    @model_validator(mode="after")
    def require_provider_lock(self):
        if self.engine in ("terraform", "opentofu") and not self.lock_digest:
            raise ValueError("Terraform and OpenTofu requests need a provider lock digest")
        return self


# Require an exact digest for each human approval or rejection
class Decision(Model):
    digest: Digest
    decision: Literal["approve", "reject"]
    note: str = Field(default="", max_length=500)


# Bind execution to both the request and retained artifact
class Claim(Model):
    digest: Digest
    artifact_digest: Digest


# Bound a requested unattended window before policy evaluation
class DelegationSpec(Model):
    target: Slug
    hours: int = Field(ge=1, le=12)
    uses: int = Field(ge=1, le=100)


# Keep the runner's outcome distinct from the controller's final state
class Completion(Model):
    outcome: Literal["succeeded", "failed", "aborted"]
    exit_code: int | None = Field(default=None, ge=-255, le=255)
    infrastructure: InfrastructureEvidence | None = None


# Require an inspection note when resolving uncertain execution
class Resolution(Model):
    outcome: Literal["succeeded", "failed", "cancelled"]
    note: str = Field(min_length=10, max_length=500)
