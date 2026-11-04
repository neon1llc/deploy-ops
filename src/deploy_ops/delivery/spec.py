# src/deploy_ops/delivery/spec.py - Describe repositories, environments, and dependency-aware releases.


import re
import tomllib
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..config import fingerprint
from ..errors import Refused

# Keep stable component names separate from the explicit adapter vocabulary
NAME = re.compile(r"^[a-z][a-z0-9-]{0,47}$")
ADAPTERS = {
    "directory", "static", "compose", "opentofu", "terraform", "ecs",
    "container-apps", "cloud-run", "kubernetes", "workflow",
    "cloudformation", "cdk", "arm", "bicep",
}


# Reject unknown contract fields instead of silently discarding operator intent
class Record(BaseModel):
    model_config = ConfigDict(extra="forbid")


# Select a source revision and make working-tree use explicit
class Repository(Record):
    path: str
    revision: str = "HEAD"
    allow_uncommitted: bool = False


    # Prevent a Git revision from becoming a command option
    @model_validator(mode="after")
    def safe_revision(self):
        if not self.revision or self.revision.startswith("-") or "\x00" in self.revision:
            raise ValueError("Git revisions must not be empty or command options")
        return self


# Keep provider, production and promotion choices separate from component settings
class Environment(Record):
    provider: Literal["local", "aws", "azure", "gcp"]
    production: bool = False
    preview: bool = False
    variables: dict[str, str] = Field(default_factory=dict)
    promote_from: list[str] = Field(default_factory=list)


# Represent trusted build inputs as argument lists and explicit artifact settings
class Build(Record):
    checks: list[list[str]] = Field(default_factory=list)
    command: list[str] = Field(default_factory=list)
    context: str = "."
    dockerfile: str = "Dockerfile"
    image: str = ""
    push: bool = False


# Bind one repository capability to dependencies and environment overrides
class Component(Record):
    # Source ownership and dependency edges do not imply deployment authority
    repository: str
    kind: Literal["infrastructure", "application", "workflow"]
    adapter: str
    path: str = "."
    needs: list[str] = Field(default_factory=list)
    options: dict[str, Any] = Field(default_factory=dict)
    targets: dict[str, dict[str, Any]] = Field(default_factory=dict)

    # Build inputs, public outputs and bounds stay part of the approved contract
    build: Build = Field(default_factory=Build)
    outputs: list[str] = Field(default_factory=list)
    timeout_seconds: int = Field(default=600, ge=10, le=3600)
    estimated_monthly_delta_cents: int | None = Field(default=None, ge=0)


    # Reject unknown adapters and source paths that escape their repository
    @model_validator(mode="after")
    def check_adapter(self):
        if self.adapter not in ADAPTERS:
            raise ValueError(f"Unknown adapter: {self.adapter}")
        for value in (self.path, self.build.context, self.build.dockerfile):
            path = Path(value)
            if path.is_absolute() or ".." in path.parts or "\\" in value:
                raise ValueError("Component paths must stay inside their repository")
        for command in [*self.build.checks, self.build.command]:
            if command and any(not isinstance(part, str) or "\x00" in part for part in command):
                raise ValueError("Commands must be argument lists, not shell fragments")
        return self


# Own the shared repository graph and environment contract
class Workspace(Record):
    schema_version: Literal[1] = 1
    name: str
    repositories: dict[str, Repository]
    environments: dict[str, Environment]
    components: dict[str, Component]


    # Validate references and effective provider options before execution
    @model_validator(mode="after")
    def relationships(self):
        for key in [self.name, *self.repositories, *self.environments, *self.components]:
            if not NAME.fullmatch(key):
                raise ValueError("Use lowercase names with optional hyphens, at most 48 characters")
        for name, component in self.components.items():
            if component.repository not in self.repositories:
                raise ValueError(f"Unknown repository for {name}")
            if set(component.needs) - self.components.keys() or name in component.needs:
                raise ValueError(f"Invalid dependencies for {name}")
            if set(component.targets) - self.environments.keys():
                raise ValueError(f"Unknown environment override for {name}")
            for override in component.targets.values():
                if "adapter" in override and override["adapter"] not in ADAPTERS:
                    raise ValueError("Unknown environment adapter")
        for name, environment in self.environments.items():
            if environment.preview and (environment.production or environment.provider != "local"):
                raise ValueError("Preview environments must be local and nonproduction")
            if set(environment.promote_from) - self.environments.keys() or name in environment.promote_from:
                raise ValueError(f"Invalid promotion path for {name}")
            # Validate effective native options after environment inheritance
            from .options import MODELS
            for component_name in self.components:
                effective = self.component(component_name, name)
                if effective.adapter in MODELS:
                    expected = "aws" if effective.adapter in {"cloudformation", "cdk"} else "azure"
                    if environment.provider != expected or effective.kind != "infrastructure":
                        raise ValueError("Native infrastructure adapter needs its matching cloud and infrastructure kind")
                    MODELS[effective.adapter].model_validate(effective.options)
        self.order()
        return self


    # Traverse each dependency once and reject cycles before preparing work
    def order(self, selected: str | None = None, with_dependencies: bool = True) -> list[str]:
        """Keep shared dependencies once and reject cycles before starting work."""
        if selected and selected not in self.components:
            raise Refused("Unknown component", 422)
        result, visiting = [], set()


        # Detect recursion before admitting a node into dependency order
        def visit(name):
            if name in visiting:
                raise ValueError("Component dependency graph contains a cycle")
            if name in result:
                return
            visiting.add(name)
            if with_dependencies:
                for dependency in self.components[name].needs:
                    visit(dependency)
            visiting.remove(name)
            result.append(name)

        for name in ([selected] if selected else self.components):
            visit(name)
        return result


    # Merge the selected environment without mutating shared component defaults
    def component(self, name: str, environment: str) -> Component:
        """Resolve a target adapter without pretending provider settings are interchangeable."""
        component = self.components[name]
        override = dict(component.targets.get(environment, {}))
        adapter = override.pop("adapter", component.adapter)
        return component.model_copy(update={"adapter": adapter, "options": {**component.options, **override}, "targets": {}})


    # Preserve established approvals when optional preview support is disabled
    @property
    def digest(self) -> str:
        data = self.model_dump()
        # Disabled preview support must not invalidate existing workspace approvals
        for environment in data["environments"].values():
            if not environment["preview"]:
                environment.pop("preview")
        return fingerprint(data)


# Parse the operator workspace without executing repository code
def load(path: Path) -> Workspace:
    """Read the operator-owned workspace. Repository content cannot rewrite its authority."""
    return Workspace.model_validate(tomllib.loads(path.read_text()))


# Resolve only complete environment and declared dependency references
def options(component: Component, environment: Environment, name: str, outputs: dict) -> dict:
    """Resolve complete values, without evaluating expressions or expanding a shell."""
    values = {**component.options, **component.targets.get(name, {})}


    # Expand only the two supported public-reference namespaces
    def resolve(value):
        if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
            key = value[2:-1]
            if key.startswith("env."):
                if key[4:] not in environment.variables:
                    raise Refused(f"Missing environment variable reference: {key}", 422)
                return environment.variables[key[4:]]
            if key.startswith("outputs."):
                parts = key.split(".")
                if len(parts) != 3 or parts[1] not in component.needs:
                    raise Refused("Output references must name a declared dependency", 422)
                try:
                    return outputs[parts[1]][parts[2]]
                except KeyError as error:
                    raise Refused(f"Missing deployment output: {key}", 422) from error
            raise Refused("Only environment and dependency output references are supported", 422)
        if isinstance(value, dict):
            return {key: resolve(item) for key, item in value.items()}
        if isinstance(value, list):
            return [resolve(item) for item in value]
        return value

    return resolve(values)
