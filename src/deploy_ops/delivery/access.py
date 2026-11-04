# delivery/access.py - Bind trusted local cloud credentials to an expiring operator grant.


import configparser
import json
import os
import re
import time
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..config import fingerprint
from ..errors import Refused
from .files import private_directory


# Keep access references outside workspace-controlled source and browser forms
class Grant(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    provider: Literal["aws", "gcp", "azure"]
    identity: dict[str, str]
    adapters: list[Literal["cloudformation", "cdk", "ecs", "cloud-run", "arm", "bicep", "container-apps"]] = Field(min_length=1)
    resource_types: list[str] = Field(min_length=1)
    actions: list[Literal["create", "update", "delete", "replace", "read", "no-op"]]
    max_changes: int = Field(ge=1, le=100)


    # Native adapter grants cannot authorize another provider or source execution
    @model_validator(mode="after")
    def provider_adapters(self):
        allowed = {"aws": {"cloudformation", "cdk", "ecs"}, "gcp": {"cloud-run"},
                   "azure": {"arm", "bicep", "container-apps"}}
        if not set(self.adapters) <= allowed[self.provider]:
            raise ValueError("Local adapters must match the granted cloud provider")
        if self.provider == "azure":
            azure_identity(self.identity)
        return self


# A grant approves only this workspace revision and a finite lifetime
class Access(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal[1] = 1
    workspace_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    expires_at: int
    authorization: str = Field(min_length=10, max_length=1000)
    environments: dict[str, Grant]


# Refuse portable world-readable secrets and indirect credential providers
def private_file(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_file() or (os.name == "posix" and (path.stat().st_mode & 0o077 or path.stat().st_uid != os.getuid())):
        raise Refused("Credential and access files must be owned by this user and readable only by the owner", 422)
    return path


# Loading a reference grants no authority beyond its exact saved workspace
def load_access(path: Path, digest: str) -> Access:
    try:
        access = Access.model_validate(tomllib.loads(private_file(str(path)).read_text()))
    except Refused:
        raise
    except Exception:
        raise Refused("Invalid local cloud access file; inspect it privately", 422) from None
    if access.workspace_digest != digest:
        raise Refused("Local cloud grant does not match this workspace. Review a new grant", 403)
    if time.time() >= access.expires_at:
        raise Refused("Local cloud grant expired; no new cloud commands are allowed", 403)
    return access


# Validate the public Azure scope without opening the private key
def azure_identity(identity: dict[str, str]) -> None:
    required = {"tenant_id", "client_id", "subscription_id", "resource_group", "cloud", "certificate_file"}
    uuid = r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}"
    if (set(identity) != required or identity.get("cloud") != "AzureCloud"
            or any(not re.fullmatch(uuid, identity.get(key, "")) for key in ("tenant_id", "client_id", "subscription_id"))
            or not re.fullmatch(r"[A-Za-z0-9_().-]{1,90}", identity.get("resource_group", ""))
            or identity["resource_group"].endswith(".")
            or not Path(identity.get("certificate_file", "")).is_absolute()):
        raise Refused("Azure local access needs public-cloud tenant, client, subscription, resource group and absolute certificate path", 422)


# Resolve credential aliases on every authority check so source cannot supply the key
def check_paths(grant: Grant, source_roots: list[Path]) -> None:
    if grant.provider == "azure":
        azure_identity(grant.identity)
        certificate = private_file(grant.identity["certificate_file"])
        if any(certificate.is_relative_to(root.resolve()) for root in source_roots):
            raise Refused("Keep Azure certificate credentials outside workspace and source repositories", 422)


# Native tools receive only the chosen credential reference and fixed target
def credentials(grant: Grant) -> dict[str, str]:
    identity = grant.identity
    if grant.provider == "azure":
        azure_identity(identity)
        certificate = private_file(identity["certificate_file"])
        return {"ARM_CLIENT_ID": identity["client_id"], "ARM_TENANT_ID": identity["tenant_id"],
                "ARM_SUBSCRIPTION_ID": identity["subscription_id"], "ARM_CLIENT_CERTIFICATE_PATH": str(certificate)}
    if grant.provider == "aws":
        required = {"account_id", "principal_arn", "region", "profile", "credentials_file", "config_file"}
        if set(identity) != required or not re.fullmatch(r"[0-9]{12}", identity.get("account_id", "")):
            raise Refused("AWS local access needs account, principal, region, profile and explicit credential/config files", 422)
        if not identity["principal_arn"].startswith("arn:aws:iam::" + identity["account_id"] + ":user/"):
            raise Refused("This local AWS route requires an explicitly bound IAM user", 422)
        keyfile = private_file(identity["credentials_file"])
        configfile = Path(identity["config_file"]).expanduser().resolve()
        config = configparser.RawConfigParser()
        config.read(configfile)
        section = "profile " + identity["profile"]
        if config.defaults() or (config.has_section("default") and set(config["default"]) - {"region", "output"}):
            raise Refused("AWS default configuration must not supply helpers or endpoint overrides", 422)
        if config.has_section(section) and set(config[section]) - {"region", "output"}:
            raise Refused("Use an AWS profile without helpers, role chaining or endpoint overrides", 422)
        keys = configparser.RawConfigParser()
        keys.read(keyfile)
        profile = identity["profile"]
        if not keys.has_section(profile) or set(keys[profile]) != {"aws_access_key_id", "aws_secret_access_key"}:
            raise Refused("The selected AWS profile must contain a persistent key pair only", 422)
        return {"AWS_PROFILE": profile, "AWS_DEFAULT_PROFILE": profile,
                "AWS_SHARED_CREDENTIALS_FILE": str(keyfile), "AWS_CONFIG_FILE": str(configfile),
                "AWS_DEFAULT_REGION": identity["region"], "AWS_REGION": identity["region"],
                "AWS_EC2_METADATA_DISABLED": "true", "AWS_IGNORE_CONFIGURED_ENDPOINT_URLS": "true"}
    required = {"project_id", "service_account", "region", "credentials_file"}
    if set(identity) != required:
        raise Refused("GCP local access needs project, service account, region and credential file", 422)
    keyfile = private_file(identity["credentials_file"])
    try:
        data = json.loads(keyfile.read_text())
        valid = (data.get("type") == "service_account"
                 and data.get("token_uri") == "https://oauth2.googleapis.com/token"
                 and data.get("client_email") == identity["service_account"]
                 and data.get("project_id") == identity["project_id"])
    except Exception:
        valid = False
    if not valid:
        raise Refused("GCP credential metadata does not match the approved service account and project", 403)
    return {"GOOGLE_APPLICATION_CREDENTIALS": str(keyfile), "CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE": str(keyfile),
            "CLOUDSDK_CORE_PROJECT": identity["project_id"], "GOOGLE_CLOUD_PROJECT": identity["project_id"]}


# Authenticate only the granted service principal in a private public-cloud CLI cache
def verify_azure(context, grant: Grant) -> dict:
    identity = grant.identity
    azure_identity(identity)
    required = {"subscription_id", "resource_group"}
    if context.component.adapter in {"arm", "bicep"}:
        required.add("tenant_id")
    for key in required | ({"tenant_id", "client_id"} & context.options.keys()):
        if str(context.options.get(key, "")).lower() != identity[key].lower():
            raise Refused("Azure adapter target differs from its local cloud grant", 403)
    if context.options.get("cloud", "AzureCloud") != "AzureCloud":
        raise Refused("Azure local access supports the public cloud only", 403)
    if context.component.adapter not in grant.adapters:
        raise Refused("Azure adapter is outside its local cloud grant", 403)

    # Native login consumes a file reference; private-key bytes never enter argv or events
    certificate = private_file(identity["certificate_file"])
    if certificate.is_relative_to(context.source.resolve()) or certificate.is_relative_to(context.stage.parent.resolve()):
        raise Refused("Keep Azure certificate credentials outside source and release artifacts", 422)
    home = context.stage.parent / "home"
    private_directory(home)
    cache = home / ".azure"
    private_directory(cache)
    context.process.env.update(HOME=str(home), AZURE_CONFIG_DIR=str(cache),
        AZURE_CORE_COLLECT_TELEMETRY="no", AZURE_EXTENSION_USE_DYNAMIC_INSTALL="no")
    context.command(["az", "cloud", "set", "--name", "AzureCloud", "--only-show-errors"], capture=True)
    context.command(["az", "login", "--service-principal", "--username", identity["client_id"],
        "--tenant", identity["tenant_id"], "--certificate", str(certificate), "--output", "none",
        "--only-show-errors"], capture=True)

    # Subscription selection and readback reject cached human or different-tenant identities
    context.command(["az", "account", "set", "--subscription", identity["subscription_id"], "--only-show-errors"], capture=True)
    observed = json.loads(context.command(["az", "account", "show", "--subscription", identity["subscription_id"],
        "--output", "json", "--only-show-errors"], capture=True))
    user = observed.get("user", {})
    if (str(observed.get("id", "")).lower() != identity["subscription_id"].lower()
            or str(observed.get("tenantId", "")).lower() != identity["tenant_id"].lower()
            or observed.get("environmentName") != "AzureCloud" or observed.get("state") != "Enabled"
            or str(user.get("name", "")).lower() != identity["client_id"].lower()
            or user.get("type") != "servicePrincipal"):
        raise Refused("Azure authenticated identity differs from its local cloud grant", 403)
    group = json.loads(context.command(["az", "group", "show", "--name", identity["resource_group"],
        "--subscription", identity["subscription_id"], "--output", "json", "--only-show-errors"], capture=True))
    expected = f"/subscriptions/{identity['subscription_id']}/resourceGroups/{identity['resource_group']}"
    if str(group.get("id", "")).lower() != expected.lower():
        raise Refused("Azure resource group differs from its local cloud grant", 403)
    return {"provider": "azure", "subscription_id": identity["subscription_id"],
            "tenant_id": identity["tenant_id"], "principal": identity["client_id"],
            "resource_group": identity["resource_group"]}


# Read identity through the same process environment used by native adapters
def verify(context, grant: Grant) -> dict:
    if grant.provider == "azure":
        return verify_azure(context, grant)
    identity = grant.identity
    for key in ("account_id", "region", "project_id"):
        if key in context.options and key in identity and context.options[key] != identity[key]:
            raise Refused("Adapter target differs from its local cloud grant", 403)
    if context.options.get("profile") and context.options["profile"] != identity.get("profile"):
        raise Refused("Adapter profile differs from its local cloud grant", 403)
    if grant.provider == "aws":
        observed = json.loads(context.command(["aws", "--profile", identity["profile"], "--region", identity["region"],
            "--no-cli-pager", "sts", "get-caller-identity", "--output", "json"], capture=True))
        if observed.get("Account") != identity["account_id"] or observed.get("Arn") != identity["principal_arn"]:
            raise Refused("AWS authenticated identity differs from its local cloud grant", 403)
        return {"provider": "aws", "account_id": observed["Account"], "principal": observed["Arn"]}
    observed = json.loads(context.command(["gcloud", "projects", "describe", identity["project_id"],
        "--project", identity["project_id"], "--quiet", "--format=json"], capture=True))
    if observed.get("projectId") != identity["project_id"] or observed.get("lifecycleState") != "ACTIVE":
        raise Refused("GCP did not verify the intended active project", 403)
    return {"provider": "gcp", "project_id": identity["project_id"], "principal": identity["service_account"]}


# Reject new resource kinds and oversized changes even after a manual approval
def check_preview(preview: dict, grant: Grant):
    changes = preview.get("changes", [])
    if len(changes) > grant.max_changes:
        raise Refused("Preview exceeds the local grant's change limit", 403)
    for change in changes:
        if change["resource_type"] not in grant.resource_types or change["action"] not in grant.actions:
            raise Refused("Preview includes a resource type or action outside the local grant", 403)


# Bind access changes without publishing credential paths in release evidence
def binding(access: Access, grant: Grant) -> dict[str, str]:
    return {"grant_digest": fingerprint(access.model_dump()), "provider": grant.provider}
