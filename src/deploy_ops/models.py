# src/deploy_ops/models.py - Validate requests before they enter the approval ledger.

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
Commit = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]
Slug = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9-]{0,62}$")]
Engine = Literal["terraform", "opentofu", "manifest", "delivery"]
Cloud = Literal["aws", "azure", "gcp", "local"]
Action = Literal["create", "update", "delete", "replace", "read", "no-op", "import", "move"]


class Model(BaseModel):
    """Reject unknown fields instead of silently weakening a request."""

    model_config = ConfigDict(extra="forbid", strict=True)


class Source(Model):
    """Keep issuer-verified identity separate from the agent's own labels."""

    kind: Literal["github", "local"] = "github"
    repository: str
    repository_id: str
    repository_owner_id: str
    ref: str
    sha: Commit
    actor: str
    actor_id: str
    run_id: str
    run_attempt: str
    event_name: str
    environment: str
    job_workflow_ref: str
    job_workflow_sha: Commit
    subject: str


class Agent(Model):
    """Describe the caller without treating its self-report as authentication."""

    label: str = Field(default="", max_length=120)
    model: str = Field(default="", max_length=120)
    run: str = Field(default="", max_length=160)


class Change(Model):
    address: str = Field(min_length=1, max_length=512)
    resource_type: str = Field(min_length=1, max_length=256)
    action: Action


class Preview(Model):
    changes: list[Change] = Field(default_factory=list, max_length=2000)
    findings: list[str] = Field(default_factory=list, max_length=100)
    manual_only: bool = False


class Proposal(Model):
    target: Slug
    engine: Engine
    artifact_digest: Digest
    preview: dict[str, Any]
    tool_version: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9._+-]+$")
    lock_digest: Digest | None = None
    estimated_monthly_delta_cents: int | None = Field(default=None, ge=-100000000, le=100000000)
    agent: Agent = Field(default_factory=Agent)

    @model_validator(mode="after")
    def require_provider_lock(self):
        if self.engine in ("terraform", "opentofu") and not self.lock_digest:
            raise ValueError("Terraform and OpenTofu requests need a provider lock digest")
        return self


class Decision(Model):
    digest: Digest
    decision: Literal["approve", "reject"]
    note: str = Field(default="", max_length=500)


class Claim(Model):
    digest: Digest
    artifact_digest: Digest


class DelegationSpec(Model):
    target: Slug
    hours: int = Field(ge=1, le=12)
    uses: int = Field(ge=1, le=100)


class Completion(Model):
    outcome: Literal["succeeded", "failed", "aborted"]
    exit_code: int | None = Field(default=None, ge=-255, le=255)


class Resolution(Model):
    outcome: Literal["succeeded", "failed", "cancelled"]
    note: str = Field(min_length=10, max_length=500)
