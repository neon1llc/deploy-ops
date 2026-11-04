# src/deploy_ops/isolated_recipe.py - Compile one bounded Cloud Run recipe without executing contributor code.


import re
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .config import GcsBackend, canonical, fingerprint
from .models import CloudRunOutputs, Digest, Model, Slug

# Pin the native formats exercised by the receipt implementation
VERSIONS = {"opentofu": "1.10.0", "terraform": "1.12.2"}
PROVIDER = "registry.opentofu.org/hashicorp/google"
PROVIDER_VERSION = "6.50.0"
HELPER = "/opt/deployops-tools/isolated_guest.py"


# Accept public destination data rather than executable infrastructure source
class Recipe(CloudRunOutputs):
    runtime_service_account: str
    image_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    backend: GcsBackend

    # Keep runtime identity and registry within the approved project
    @model_validator(mode="after")
    def scope(self):
        if not re.fullmatch(r"[a-z][a-z0-9-]{4,28}[a-z0-9]@" + re.escape(self.project)
                            + r"\.iam\.gserviceaccount\.com", self.runtime_service_account):
            raise ValueError("Runtime identity must belong to the recipe project")
        if not self.image_repository.startswith(f"{self.region}-docker.pkg.dev/{self.project}/"):
            raise ValueError("Recipe registry must belong to the same project and region")
        if any(part in {"", ".", ".."} for part in self.image_repository.split("/")):
            raise ValueError("Recipe image paths must use canonical nonempty segments")
        return self

    # Generate the entire module so source cannot introduce hooks or alternative endpoints
    def module(self, engine: str) -> dict:
        service = {"name": self.name, "location": self.region, "deletion_protection": False,
                   "ingress": "INGRESS_TRAFFIC_ALL", "invoker_iam_disabled": False,
                   "labels": {"managed_by": "deploy-ops"},
                   "template": {"timeout": "30s", "max_instance_request_concurrency": 1,
                       "service_account": self.runtime_service_account,
                       "scaling": {"min_instance_count": 0, "max_instance_count": 1},
                       "containers": [{"image": self.image_repository + "@" + self.image_digest,
                           "resources": {"cpu_idle": True, "startup_cpu_boost": False,
                                         "limits": {"cpu": "1", "memory": "256Mi"}}}]},
                   "lifecycle": {"ignore_changes": ["template[0].containers[0].image", "traffic", "client", "client_version"]}}
        return {"terraform": {"required_version": "= " + VERSIONS[engine],
                    "required_providers": {"google": {"source": PROVIDER, "version": PROVIDER_VERSION}},
                    "backend": {"gcs": self.backend.model_dump()}},
                "provider": {"google": {"project": self.project, "region": self.region}},
                "resource": {"google_cloud_run_v2_service": {"web": service}},
                "output": {key: {"value": value} for key, value in {
                    "project": "${google_cloud_run_v2_service.web.project}",
                    "region": "${google_cloud_run_v2_service.web.location}",
                    "name": "${google_cloud_run_v2_service.web.name}",
                    "image_repository": self.image_repository}.items()}}


# Keep reviewed runtime and narrow identities outside contributor source
class IsolatedGrant(Model):
    schema_version: Literal[1] = 1
    name: Slug = Field(max_length=55)
    environment: Slug
    expires_at: int
    authorization: str = Field(min_length=10, max_length=1000)
    engine: Literal["terraform", "opentofu"]
    recipe: Recipe
    planner_service_account: str
    executor_service_account: str
    credentials_file: str
    broker_service_account: str
    gcloud: str
    limactl: str
    vm: str = Field(pattern=r"^[a-z][a-z0-9-]{0,62}$")
    vm_config_digest: Digest
    tool_digest: Digest
    provider_digest: Digest
    lock_file: str
    lock_digest: Digest
    workspace_digest: Digest | None = None
    app_component: Slug | None = None
    app_dependency: Slug | None = None
    app_planner_service_account: str | None = None
    app_executor_service_account: str | None = None

    # Refuse ambiguous identity reuse and partial application registration
    @model_validator(mode="after")
    def identities(self):
        accounts = [self.planner_service_account, self.executor_service_account]
        app = [self.workspace_digest, self.app_component, self.app_dependency,
               self.app_planner_service_account, self.app_executor_service_account]
        if any(app) and not all(app):
            raise ValueError("Application receipt registration needs all application fields")
        accounts += [value for value in app[-2:] if value]
        accounts.extend([self.recipe.runtime_service_account, self.broker_service_account])
        if len(accounts) != len(set(accounts)):
            raise ValueError("Planning, execution, application and runtime identities must be distinct")
        for account in accounts:
            if not re.fullmatch(r"[a-z][a-z0-9-]{4,28}[a-z0-9]@" + re.escape(self.recipe.project)
                                + r"\.iam\.gserviceaccount\.com", account):
                raise ValueError("Every isolated service account must belong to the registered project")
        for path in (self.credentials_file, self.gcloud, self.limactl, self.lock_file):
            if not Path(path).is_absolute():
                raise ValueError("Operator files and native tools need absolute paths")
        return self

    # Include every authority change in the controller target identity
    @property
    def digest(self):
        return fingerprint(self.model_dump())

    # Bind the exact generated module and provider lock before planning approval
    @property
    def packet_digest(self):
        return fingerprint({"module": self.recipe.module(self.engine), "lock_digest": self.lock_digest,
                            "tool_digest": self.tool_digest, "provider_digest": self.provider_digest})

    # Keep generated source serialization deterministic across the host and guest
    def module_bytes(self):
        return canonical(self.recipe.module(self.engine)).encode()
