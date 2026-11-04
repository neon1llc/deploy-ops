# delivery/options.py - Describe reviewed adapter settings once for files, CLI and forms.


from typing import Literal
from pathlib import Path
import re

from pydantic import BaseModel, ConfigDict, Field, model_validator


# Preserve only public deployment inputs in workspace documents
class PublicOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")


# Bind raw templates to their native account and stack lifecycle
class CloudFormationOptions(PublicOptions):
    # Stack identity and template selection are required before preview
    account_id: str = Field(min_length=1, description="Expected AWS account ID")
    region: str = Field(min_length=1)
    stack_name: str = Field(min_length=1)
    template: str = Field(min_length=1, description="Repository-relative JSON or YAML template")
    operation: Literal["deploy", "destroy"] = "deploy"

    # Access-sensitive provider choices are explicit public references
    profile: str = ""
    principal_arn: str = ""
    role_arn: str = ""
    parameters: dict[str, str | int | bool] = Field(default_factory=dict)
    tags: dict[str, str] = Field(default_factory=dict)
    capabilities: list[Literal["CAPABILITY_IAM", "CAPABILITY_NAMED_IAM"]] = Field(default_factory=list)


# Consume a frozen assembly without running CDK application source
class CdkOptions(PublicOptions):
    # A selected assembly stack must match the configured deployment target
    account_id: str = Field(min_length=1)
    region: str = Field(min_length=1)
    stack_name: str = Field(min_length=1)
    assembly: str = Field(min_length=1, description="Repository-relative frozen cloud assembly directory")
    stack_artifact: str = Field(min_length=1, description="Stack artifact ID from manifest.json")

    # Publication destinations do not grant bootstrap or role creation
    asset_bucket: str = ""
    asset_prefix: str = ""
    operation: Literal["deploy", "destroy"] = "deploy"
    profile: str = ""
    principal_arn: str = ""
    role_arn: str = ""
    parameters: dict[str, str | int | bool] = Field(default_factory=dict)
    tags: dict[str, str] = Field(default_factory=dict)
    capabilities: list[Literal["CAPABILITY_IAM", "CAPABILITY_NAMED_IAM"]] = Field(default_factory=list)


# ARM what-if is a prediction and only incremental resource-group deployment is supported
class ArmOptions(PublicOptions):
    # An existing resource group is the only supported deployment scope
    subscription_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    resource_group: str = Field(min_length=1)
    template: str = Field(min_length=1, description="Repository-relative frozen JSON template")

    # Public parameters and deliberate incremental mode remain reviewable
    parameters: dict[str, str | int | bool] = Field(default_factory=dict)
    deployment_name: str = "deploy-ops"
    mode: Literal["Incremental"] = "Incremental"
    operation: Literal["deploy"] = "deploy"


# Adapters share a form only when their native inputs have the same meaning
MODELS = {"cloudformation": CloudFormationOptions, "cdk": CdkOptions, "arm": ArmOptions, "bicep": ArmOptions}


# Describe guided local inputs separately from native cloud provider assumptions
class DirectoryOptions(PublicOptions):
    name: str = Field(pattern=r"^(?:[a-z][a-z0-9-]{0,47}|\$\{(?:env\.[^{}]+|outputs\.[^{}.]+\.[^{}.]+)\})$", title="Resource name")


# Keep artifact and target paths reviewable without resolving deployment outputs
class StaticOptions(PublicOptions):
    directory: str = Field(default=".", title="Artifact directory", description="Path inside the component source")
    destination: str = Field(default="", title="Destination", description="Deployment-relative path or whole dependency output reference; omit for the component default")


    # References remain complete values and are checked against the workspace by the caller
    @model_validator(mode="after")
    def paths(self):
        for key, value in (("directory", self.directory), ("destination", self.destination)):
            if re.fullmatch(r"\$\{(?:env\.[^{}]+|outputs\.[^{}.]+\.[^{}.]+)\}", value):
                continue
            path = Path(value)
            if path.is_absolute() or ".." in path.parts or "\\" in value or "\x00" in value or "${" in value:
                raise ValueError("Local paths must stay inside their source or deployment root")
        return self


# These forms do not join MODELS, whose consumers enforce native cloud constraints
LOCAL_MODELS = {"directory": DirectoryOptions, "static": StaticOptions}


# Supply defaults to forms while keeping omitted values out of saved TOML
def form(adapter: str, values: dict) -> dict:
    model = MODELS.get(adapter)
    if model is None:
        return {}
    schema = model.model_json_schema()
    return {"fields": schema["properties"], "required": schema.get("required", []),
            "values": model.model_validate(values).model_dump()}
