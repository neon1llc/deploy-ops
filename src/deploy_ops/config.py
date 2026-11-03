# src/deploy_ops/config.py - Keep deployment authority outside contributor-controlled repositories.

import hashlib
import json
import re
import tomllib
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import Field, model_validator

from .models import Cloud, Engine, Model, Slug

# Deny authority changes even when a human approves the request
BLOCKED_TYPES = [
    "aws_iam_*", "aws_organizations_*", "aws_cloudtrail*", "aws_kms_*",
    "aws_account_*", "aws_budgets_*", "aws_s3_bucket_policy", "aws_s3_account_*",
    "aws_security_group*", "aws_vpc_security_group*", "aws_network_acl*",
    "aws_guardduty*", "aws_config_*", "aws_cloudwatch_log_group",
    "aws_secretsmanager*", "aws_ssm_parameter", "aws_s3_bucket_public_access_block",
    "azuread_*", "azurerm_role_*", "azurerm_policy_*", "azurerm_*identity*",
    "azurerm_key_vault*", "azurerm_monitor_*", "azurerm_network_security*",
    "azurerm_management_*", "azurerm_subscription*", "azurerm_consumption*",
    "google_*iam*", "google_*organization*", "google_*billing*",
    "google_*service_account*", "google_*firewall*", "google_kms_*",
    "google_logging_*", "google_secret_manager*", "google_project_service*",
]
PINNED_WORKFLOW = re.compile(
    r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/\.github/workflows/"
    r"[A-Za-z0-9_.-]+\.ya?ml@[0-9a-f]{40}$"
)


def canonical(value: object) -> str:
    """Use one JSON representation for fingerprints and audit links."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def fingerprint(value: object) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class Policy(Model):
    blocked_types: list[str] = Field(default_factory=lambda: BLOCKED_TYPES.copy())
    allowed_actions: list[str] = Field(default_factory=lambda: ["create", "update", "read", "no-op"])
    auto_types: list[str] = Field(default_factory=list)
    auto_actions: list[Literal["create", "update"]] = Field(default_factory=lambda: ["create", "update"])
    max_changes: int = Field(default=5, ge=1, le=100)
    max_uses: int = Field(default=4, ge=1, le=100)
    max_total_changes: int = Field(default=12, ge=1, le=1000)
    max_monthly_delta_cents: int = Field(default=1000, ge=0, le=1000000)
    max_total_monthly_delta_cents: int = Field(default=2000, ge=0, le=1000000)
    require_cost_estimate: bool = True
    allow_delegation: bool = False

    @model_validator(mode="after")
    def exact_auto_types(self):
        if any(re.search(r"[*?\[\]]", name) for name in self.auto_types):
            raise ValueError("Automatic resource types must be exact names, not patterns")
        if not set(self.auto_actions) <= set(self.allowed_actions):
            raise ValueError("Automatic actions must be allowed by the target policy")
        return self


class StateBackend(Model):
    bucket: str = Field(pattern=r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
    key: str = Field(min_length=1, max_length=512, pattern=r"^[a-zA-Z0-9/_.-]+$")
    region: str = Field(pattern=r"^[a-z]{2}-[a-z]+-[0-9]$")


class ReceiptPolicy(Model):
    format: Literal["aws-ecs-v1"] = "aws-ecs-v1"
    backend: StateBackend
    valid_seconds: int = Field(default=3600, ge=60, le=86400)


class Target(Model):
    repository: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    repository_id: str = Field(pattern=r"^[1-9][0-9]*$")
    repository_owner_id: str = Field(pattern=r"^[1-9][0-9]*$")
    ref: str = "refs/heads/main"
    workflow_ref: str
    environment: str = Field(min_length=1, max_length=100)
    planner_environment: str = Field(min_length=1, max_length=100)
    cloud: Cloud
    engines: list[Engine] = Field(default_factory=lambda: ["opentofu", "terraform"])
    workdir: str = "."
    delivery_digest: str | None = None
    allow_local_operator: bool = False
    production: bool = False
    events: list[str] = Field(default_factory=lambda: ["workflow_dispatch", "push"])
    identity: dict[str, str]
    planner_identity: dict[str, str]
    policy: Policy = Field(default_factory=Policy)
    receipt_outputs: ReceiptPolicy | None = None
    receipt_dependencies: dict[str, Slug] = Field(default_factory=dict)

    @model_validator(mode="after")
    def check_boundary(self):
        if self.receipt_outputs or self.receipt_dependencies:
            if self.cloud != "aws" or not re.fullmatch(r"arn:aws:iam::[0-9]{12}:role/.+", self.identity.get("role_arn", "")):
                raise ValueError("Infrastructure receipts currently require a registered AWS role")
            if self.receipt_outputs and self.engines != ["opentofu"]:
                raise ValueError("Receipt producers currently require the OpenTofu saved-plan path")
            if self.receipt_dependencies and self.engines != ["delivery"]:
                raise ValueError("Receipt consumers must use the delivery engine")
            if any(not re.fullmatch(r"[a-z][a-z0-9-]{0,47}", key) for key in self.receipt_dependencies):
                raise ValueError("Receipt dependencies must name workspace components")
        if not PINNED_WORKFLOW.fullmatch(self.workflow_ref):
            raise ValueError("Pin the reusable workflow to a full 40-character commit SHA")
        path = PurePosixPath(self.workdir)
        if path.is_absolute() or ".." in path.parts or "\\" in self.workdir:
            raise ValueError("workdir must stay inside the checked-out repository")
        if self.planner_environment == self.environment:
            raise ValueError("Use separate planning and execution environments")
        required = {
            "local": set(),
            "aws": {"role_arn", "region"},
            "azure": {"client_id", "tenant_id", "subscription_id"},
            "gcp": {"workload_identity_provider", "service_account", "project_id"},
        }[self.cloud]
        for identity in (self.identity, self.planner_identity):
            if set(identity) != required or any(not value for value in identity.values()):
                raise ValueError(f"{self.cloud} identities need exactly {sorted(required)}")
            if any("\n" in value or "\r" in value for value in identity.values()):
                raise ValueError("Cloud identity values cannot contain line breaks")
        if "delivery" in self.engines and (not self.delivery_digest or not re.fullmatch(r"[0-9a-f]{64}", self.delivery_digest)):
            raise ValueError("Delivery targets need an administrator-approved workspace digest")
        if self.allow_local_operator and self.cloud != "local":
            raise ValueError("Local operator mode cannot authorize cloud access")
        if self.cloud == "local":
            if self.production and self.policy.allow_delegation:
                raise ValueError("Production cannot enable delegation")
            return self
        principal = {"aws": "role_arn", "azure": "client_id", "gcp": "service_account"}[self.cloud]
        if self.identity[principal] == self.planner_identity[principal]:
            raise ValueError("Planning and execution must use different cloud identities")
        if self.production and self.policy.allow_delegation:
            raise ValueError("Production targets cannot enable unattended delegation in this release")
        return self

    @property
    def digest(self) -> str:
        value = self.model_dump()
        # Optional receipts must not invalidate existing nonreceipt approvals and grants
        if not self.receipt_outputs and not self.receipt_dependencies:
            value.pop("receipt_outputs")
            value.pop("receipt_dependencies")
        return fingerprint(value)


class SlackConfig(Model):
    team_id: str = ""
    app_id: str = ""
    channel_id: str = ""
    # Map Slack members to GitHub numeric IDs to prevent self-approval
    approvers: dict[str, str] = Field(default_factory=dict)


class Settings(Model):
    audience: str = "deploy-ops"
    database: str = ".state/deploy_ops.db"
    allowed_hosts: list[str] = Field(default_factory=lambda: ["127.0.0.1", "localhost", "testserver"])
    request_seconds: int = Field(default=3600, ge=60, le=86400)
    approval_seconds: int = Field(default=1800, ge=60, le=7200)
    execution_seconds: int = Field(default=900, ge=30, le=900)
    delegation_hours: int = Field(default=8, ge=1, le=12)
    max_body_bytes: int = Field(default=2000000, ge=1024, le=10000000)
    targets: dict[str, Target]
    slack: SlackConfig = Field(default_factory=SlackConfig)

    @model_validator(mode="after")
    def validate_targets(self):
        if not self.targets or any(not re.fullmatch(r"[a-z][a-z0-9-]{0,62}", key) for key in self.targets):
            raise ValueError("Configure at least one target with a lowercase slug")
        for name, target in self.targets.items():
            for producer in target.receipt_dependencies.values():
                if producer == name or producer not in self.targets or not self.targets[producer].receipt_outputs:
                    raise ValueError("Receipt dependencies must name a registered infrastructure producer")
        return self


def load(path: Path) -> Settings:
    """Resolve state storage relative to the controller configuration, not its caller."""
    settings = Settings.model_validate(tomllib.loads(path.read_text()))
    database = Path(settings.database)
    if not database.is_absolute():
        settings.database = str((path.resolve().parent / database).resolve())
    return settings
