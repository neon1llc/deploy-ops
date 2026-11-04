# src/deploy_ops/receipts.py - Bind typed public outputs to authenticated infrastructure execution.


import hashlib
import json
from contextlib import contextmanager
from pathlib import Path

from .config import Target, fingerprint
from .errors import Refused
from .models import CloudRunOutputs, EcsOutputs, InfrastructureEvidence


# Qualify receipt extraction against the exact tools installed by the protected workflows
RECEIPT_TOOLS = {
    ("aws-ecs-v1", "opentofu"): "1.10.0",
    ("gcp-cloud-run-v1", "opentofu"): "1.10.0",
    ("gcp-cloud-run-v1", "terraform"): "1.12.2",
}


# Keep runtime versions inside the reviewed state-format contract
def verify_tool(format: str, engine: str, version: str) -> None:
    if RECEIPT_TOOLS.get((format, engine)) != version:
        raise Refused("Infrastructure receipt engine or version is outside the supported contract", 422)


# Derive receipt scope from the registered execution identity
def aws_scope(target: Target) -> tuple[str, str]:
    return target.identity["role_arn"].split(":")[4], target.identity["region"]


# Compare the cloud and destination scope before sharing a producer's outputs
def scope(target: Target) -> tuple[str, str, str]:
    if target.cloud == "aws":
        return ("aws", *aws_scope(target))
    if target.cloud == "gcp" and target.receipt_region:
        return "gcp", target.identity["project_id"], target.receipt_region
    raise Refused("Receipt target has no supported registered scope", 422)


# Replace the entire native GCS backend before any credentialed initialization
@contextmanager
def controlled_backend(directory: Path, policy: dict | None):
    if not policy or policy["format"] != "gcp-cloud-run-v1":
        yield []
        return
    # A second override could silently replace this operator-owned destination
    if any(path.name in {"override.tf", "override.tf.json"} or path.name.endswith(("_override.tf", "_override.tf.json"))
           for path in directory.iterdir()):
        raise Refused("GCP receipt source must not contain native override files", 422)
    workspace = directory / ".terraform" / "environment"
    if workspace.exists() and workspace.read_text().strip() != "default":
        raise Refused("GCP receipt source must use the default workspace before initialization", 422)
    path = directory / "deploy_ops_backend_override.tf.json"
    created = False
    try:
        with path.open("x") as stream:
            created = True
            path.chmod(0o600)
            json.dump({"terraform": {"backend": {"gcs": policy["backend"]}}}, stream)
            stream.write("\n")
        # Reconfigure avoids copying state from a source-selected cached backend
        yield ["-reconfigure"]
    finally:
        if created:
            path.unlink(missing_ok=True)


# Validate resource relationships before issuing public output evidence
def issue(request: dict, target: Target, evidence: InfrastructureEvidence, now: int, generation: int) -> dict:
    cloud, account, region = scope(target)
    outputs = evidence.outputs.model_dump()
    policy = target.receipt_outputs
    if cloud == "aws":
        if not isinstance(evidence.outputs, EcsOutputs):
            raise Refused("Receipt outputs differ from the registered ECS contract", 422)
        prefix = f"arn:aws:ecs:{region}:{account}:"
        if not outputs["cluster"].startswith(prefix + "cluster/") or not outputs["service"].startswith(prefix + "service/"):
            raise Refused("Receipt resources do not match the registered account and region", 422)
        cluster = outputs["cluster"].rsplit("/", 1)[1]
        if outputs["service"].split("/")[1] != cluster:
            raise Refused("Receipt service does not belong to the declared cluster", 422)
        if not outputs["image_repository"].startswith(f"{account}.dkr.ecr.{region}.amazonaws.com/"):
            raise Refused("Receipt registry does not match the registered account and region", 422)
    else:
        # A public string is useful evidence only within the operator's registered project and region
        if not isinstance(evidence.outputs, CloudRunOutputs):
            raise Refused("Receipt outputs differ from the registered Cloud Run contract", 422)
        if outputs["project"] != account or outputs["region"] != region:
            raise Refused("Receipt resources do not match the registered project and region", 422)
        if not outputs["image_repository"].startswith(f"{region}-docker.pkg.dev/{account}/"):
            raise Refused("Receipt registry does not match the registered project and region", 422)
    # Include producer identity and state freshness without copying raw state
    body = {
        "id": request["id"], "format": policy.format, "generation": generation,
        "producer_target": request["manifest"]["target"],
        "producer": request["execution"]["source"], "request_digest": request["digest"],
        "artifact_digest": request["manifest"]["artifact_digest"], "policy_digest": target.digest,
        "environment": target.environment, "account" if cloud == "aws" else "project": account, "region": region,
        "backend": policy.backend.model_dump(), "observed_at": now, "expires_at": now + policy.valid_seconds,
        **evidence.model_dump(),
    }
    return {**body, "digest": fingerprint(body)}


# Reject alternate state locations or hidden credential overrides
def verify_backend(directory: Path, expected: dict) -> None:
    """Require the registered native backend and default workspace before planning or applying."""
    kind = "gcs" if "prefix" in expected else "s3"
    try:
        workspace = directory / ".terraform" / "environment"
        if workspace.exists() and workspace.read_text().strip() != "default":
            raise ValueError("Unsupported workspace")
        metadata = json.loads((directory / ".terraform" / "terraform.tfstate").read_text())
        if metadata.get("version") != 3:
            raise ValueError("Unsupported backend metadata version")
        backend = metadata["backend"]
        if backend["type"] != kind or any(backend["config"].get(key) != value for key, value in expected.items()):
            raise ValueError("Different backend")
        # Custom endpoints could route the same bucket/key to a different service
        config = backend["config"]
        if kind == "gcs":
            # Native 1.10.0 and 1.12.2 metadata leaves environment credentials out of this map
            forbidden = {"credentials", "access_token", "impersonate_service_account",
                         "impersonate_service_account_delegates", "storage_custom_endpoint",
                         "encryption_key", "kms_encryption_key"}
            if set(config) - {"bucket", "prefix"} - forbidden or any(config.get(key) for key in forbidden):
                raise ValueError("Unsupported GCS backend override")
            return
        forbidden = ("endpoint", "endpoints", "iam_endpoint", "sts_endpoint", "access_key", "secret_key",
                     "token", "profile", "shared_credentials_file", "shared_credentials_files", "shared_config_files",
                     "assume_role", "assume_role_with_web_identity", "role_arn", "external_id")
        if any(config.get(key) for key in forbidden):
            raise ValueError("Custom backend endpoint")
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        raise Refused(f"Receipt producer needs the registered {kind.upper()} backend in the default workspace", 422) from error


# Export only the named public outputs belonging to the registered schema
def extract_state(raw: bytes, format: str = "aws-ecs-v1") -> InfrastructureEvidence:
    """Discard raw state and sensitive values before constructing the public schema."""
    if len(raw) > 8_000_000:
        raise Refused("State exceeds the receipt extraction limit", 422)
    try:
        model = {"aws-ecs-v1": EcsOutputs, "gcp-cloud-run-v1": CloudRunOutputs}[format]
        state = json.loads(raw)
        outputs = {}
        for name in model.model_fields:
            output = state["outputs"][name]
            if output.get("sensitive") is not False or output.get("type") != "string":
                raise ValueError("Not an explicitly public string")
            outputs[name] = output["value"]
        return InfrastructureEvidence(state_lineage=state["lineage"], state_serial=state["serial"],
                                      state_digest=hashlib.sha256(raw).hexdigest(), outputs=model.model_validate(outputs))
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        raise Refused("State must contain the registered typed, non-sensitive receipt outputs", 422) from error
