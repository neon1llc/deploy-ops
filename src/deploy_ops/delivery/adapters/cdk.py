# src/deploy_ops/delivery/adapters/cdk.py - Deploy frozen CDK assemblies through reviewed CloudFormation change sets.


import base64
import json
import shutil
import zipfile
from dataclasses import replace
from pathlib import Path

from ...client import file_digest
from ...errors import Refused
from ..files import digest_tree, inside
from . import cloudformation as cfn
from .base import Context, need


# Reject new assembly instructions until their semantics have been reviewed
MAX_SCHEMA_MAJOR = 55
STACK_PROPERTIES = {"templateFile", "parameters", "tags", "stackName", "validateOnSynth",
                    "cloudFormationExecutionRoleArn", "requiresBootstrapStackVersion",
                    "bootstrapStackVersionSsmParameter", "terminationProtection"}


# Parse bounded assembly metadata without importing or evaluating CDK application code
def manifest(path: Path) -> dict:
    if not path.is_file() or path.stat().st_size > 5_000_000:
        raise Refused("CDK manifest is missing or too large", 422)
    value = json.loads(path.read_text())
    try:
        major = int(value["version"].split(".")[0])
    except (KeyError, ValueError, TypeError, AttributeError) as error:
        raise Refused("CDK manifest needs a versioned schema", 422) from error
    if not 1 <= major <= MAX_SCHEMA_MAJOR:
        raise Refused("CDK assembly schema is newer than this adapter supports", 422)
    return value


# Package only existing bytes and preserve a reproducible archive identity
def freeze_asset(source: Path, target: Path, packaging: str) -> None:
    if source.is_symlink():
        raise Refused("CDK asset sources cannot be symbolic links", 422)
    if packaging == "file" and source.is_file():
        shutil.copyfile(source, target)
        target.chmod(0o600)
        return
    if packaging != "zip" or not source.is_dir():
        raise Refused("CDK supports prebuilt files or zip-packaged directories", 422)
    digest_tree(source)
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(source.rglob("*")):
            if path.is_file():
                info = zipfile.ZipInfo(path.relative_to(source).as_posix(), (1980, 1, 1, 0, 0, 0))
                info.external_attr = (0o755 if path.stat().st_mode & 0o111 else 0o644) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, path.read_bytes())
    target.chmod(0o600)


# Freeze explicit file destinations without adding bootstrap or role permissions
def assets(context: Context, assembly: Path, artifacts: dict, dependencies: list[str]) -> list[dict]:
    result = []
    destination = context.stage / "cdk-assets"
    destination.mkdir(mode=0o700)
    for identifier in dependencies:
        artifact = artifacts.get(identifier, {})
        if artifact.get("type") != "cdk:asset-manifest":
            raise Refused("CDK stack dependencies must be deployed separately; only asset dependencies are supported", 422)
        path = inside(assembly, need(artifact.get("properties", {}), "file"))
        value = manifest(path)
        if set(value) - {"version", "files", "dockerImages"} or value.get("dockerImages"):
            raise Refused("Docker assets must be built and published in an isolated job before synthesis", 422)
        for asset_id, asset in value.get("files", {}).items():
            source = asset.get("source", {})
            if set(source) - {"path", "packaging"} or not source.get("path"):
                raise Refused("Executable CDK assets are not allowed in a credentialed worker", 422)
            destinations = asset.get("destinations", {})
            if len(destinations) != 1:
                raise Refused("Select one explicit CDK file destination per asset", 422)
            remote = next(iter(destinations.values()))
            if set(remote) - {"bucketName", "objectKey", "region"}:
                raise Refused("CDK asset role assumptions need a separately configured publication worker", 422)
            if remote.get("region", context.options["region"]) != context.options["region"]:
                raise Refused("CDK file asset region differs from the selected stack", 422)
            bucket = need(context.options, "asset_bucket")
            prefix = need(context.options, "asset_prefix")
            if not prefix.endswith("/") or remote.get("bucketName") != bucket or not remote.get("objectKey", "").startswith(prefix):
                raise Refused("CDK file destination is outside the explicitly configured publication scope", 422)
            path_out = destination / (str(len(result)) + ".asset")
            source_path = inside(path.parent, need(source, "path"))
            if not source_path.is_relative_to(assembly):
                raise Refused("CDK asset escapes the frozen assembly", 422)
            freeze_asset(source_path, path_out, source.get("packaging", "file"))
            result.append({"id": asset_id, "path": path_out.relative_to(context.stage).as_posix(),
                           "digest": file_digest(path_out), "bucket": need(remote, "bucketName"),
                           "key": need(remote, "objectKey")})
    return result


# Verify bootstrap metadata without creating resources or broadening trust
def bootstrap(context: Context, properties: dict) -> None:
    version = properties.get("requiresBootstrapStackVersion", 0)
    if not version:
        return
    parameter = properties.get("bootstrapStackVersionSsmParameter")
    if not isinstance(parameter, str) or not parameter.startswith("/cdk-bootstrap/"):
        raise Refused("CDK bootstrap needs an explicit existing version parameter", 422)
    value = json.loads(context.command(cfn.aws(context, "ssm", "get-parameter", "--name", parameter), capture=True))
    try:
        ready = int(value["Parameter"]["Value"]) >= int(version)
    except (KeyError, TypeError, ValueError) as error:
        raise Refused("CDK bootstrap version could not be verified") from error
    if not ready:
        raise Refused("Existing CDK bootstrap is too old; bootstrap changes require separate authority")


# Bind templates, assets, parameters, and synthesis metadata before provider preview
def prepare(context: Context) -> dict:
    if context.options.get("operation", "deploy") == "destroy":
        return cfn.prepare(context)
    source = inside(context.source, need(context.options, "assembly"))
    if not source.is_dir():
        raise Refused("Provide a frozen CDK cloud assembly from an isolated synthesis job", 422)
    digest_tree(source)
    assembly = context.stage / "cdk-assembly"
    shutil.copytree(source, assembly)
    value = manifest(assembly / "manifest.json")
    if value.get("missing"):
        raise Refused("CDK assembly has unresolved context lookups; synthesize it completely before preview", 422)
    if set(value) - {"version", "minimumCliVersion", "artifacts", "missing", "runtime"}:
        raise Refused("CDK assembly contains unsupported instructions", 422)
    artifacts = value.get("artifacts", {})
    selected = artifacts.get(need(context.options, "stack_artifact"), {})
    if selected.get("type") != "aws:cloudformation:stack":
        raise Refused("stack_artifact must name a CloudFormation stack in the assembly", 422)
    expected_environment = "aws://" + need(context.options, "account_id") + "/" + need(context.options, "region")
    if selected.get("environment") != expected_environment:
        raise Refused("CDK stack environment differs from the configured account and region", 422)
    properties = selected.get("properties", {})
    if set(properties) - STACK_PROPERTIES or properties.get("terminationProtection"):
        raise Refused("CDK role, notification, or protection instructions need a separately supported execution path", 422)
    if properties.get("stackName", context.options["stack_artifact"]) != context.options.get("stack_name"):
        raise Refused("CDK stack name differs from the configured stack", 422)
    role = properties.get("cloudFormationExecutionRoleArn")
    if role and role != context.options.get("role_arn"):
        raise Refused("Explicitly configure the assembly's reviewed CloudFormation execution role", 422)

    # Merge only public stack inputs and require the assembly's existing bootstrap
    options = {**context.options, "parameters": {**properties.get("parameters", {}), **cfn.public_map(context.options, "parameters")},
               "tags": {**properties.get("tags", {}), **cfn.public_map(context.options, "tags")}}
    derived = replace(context, options=options)
    files = assets(derived, assembly, artifacts, selected.get("dependencies", []))
    cfn.identity(derived)
    bootstrap(derived, properties)
    result = cfn.prepare(derived, inside(assembly, need(properties, "templateFile")))
    result["cdk"] = {"assembly_digest": digest_tree(assembly), "schema_version": value["version"],
                     "stack_artifact": context.options["stack_artifact"], "assets": files,
                     "bootstrap": properties, "options": options}
    result["preview"]["changes"].extend({"address": item["bucket"] + "/" + item["key"],
                                          "resource_type": "cdk_file_asset", "action": "create"} for item in files)
    result["warnings"].extend(["File assets are published after approval and retained in the existing bootstrap bucket"] if files else [])
    return result


# Use native S3 conditional writes to avoid replacing an unrelated existing object
def publish(context: Context, files: list[dict]) -> None:
    for item in files:
        path = inside(context.stage, item["path"])
        if file_digest(path) != item["digest"]:
            raise Refused("CDK asset bytes changed after approval")
        checksum = base64.b64encode(bytes.fromhex(item["digest"])).decode()
        common = ["--bucket", item["bucket"], "--expected-bucket-owner", context.options["account_id"]]
        listing = json.loads(context.command(cfn.aws(context, "s3api", "list-objects-v2", *common,
                                                     "--prefix", item["key"]), capture=True))
        exists = any(entry.get("Key") == item["key"] for entry in listing.get("Contents", []))
        if not exists:
            cfn.record(context, publishing_asset={"bucket": item["bucket"], "key": item["key"], "digest": item["digest"]})
            context.command(cfn.aws(context, "s3api", "put-object", *common, "--key", item["key"],
                                    "--body", str(path), "--if-none-match", "*", "--checksum-algorithm", "SHA256",
                                    "--checksum-sha256", checksum), capture=True)
        observed = json.loads(context.command(cfn.aws(context, "s3api", "head-object", *common,
                                                      "--key", item["key"], "--checksum-mode", "ENABLED"), capture=True))
        if observed.get("ChecksumSHA256") != checksum:
            raise Refused("Existing CDK file asset does not match the approved checksum")


# Publish frozen assets and execute the same reviewed CloudFormation object
def apply(context: Context, prepared: dict) -> dict:
    if prepared["operation"] == "destroy":
        return cfn.apply(context, prepared)
    metadata = prepared["cdk"]
    if digest_tree(context.stage / "cdk-assembly") != metadata["assembly_digest"]:
        raise Refused("CDK assembly changed after approval")
    derived = replace(context, options=metadata["options"])
    if cfn.identity(derived) != prepared["scope"]:
        raise Refused("CDK target changed after approval")
    cfn.validate_approved(derived, prepared)
    bootstrap(derived, metadata["bootstrap"])
    publish(derived, metadata["assets"])
    return cfn.apply(derived, prepared)


# Reuse provider readback without resynthesizing or replaying asset writes
def inspect(context: Context, prepared: dict) -> dict:
    return cfn.inspect(context, prepared)


# Preview cleanup never removes a shared CDK bootstrap or published asset
def discard(context: Context, prepared: dict) -> dict:
    return cfn.discard(context, prepared)


# Read a failed preview without evaluating the source application
def inspect_pending(context: Context) -> dict:
    return cfn.inspect_pending(context)
