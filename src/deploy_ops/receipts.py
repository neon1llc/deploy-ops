# src/deploy_ops/receipts.py - Bind typed public outputs to authenticated infrastructure execution.


import hashlib
import json
from pathlib import Path

from .config import Target, fingerprint
from .errors import Refused
from .models import InfrastructureEvidence


# Derive receipt scope from the registered execution identity
def aws_scope(target: Target) -> tuple[str, str]:
    return target.identity["role_arn"].split(":")[4], target.identity["region"]


# Validate resource relationships before issuing public output evidence
def issue(request: dict, target: Target, evidence: InfrastructureEvidence, now: int, generation: int) -> dict:
    account, region = aws_scope(target)
    outputs = evidence.outputs.model_dump()
    prefix = f"arn:aws:ecs:{region}:{account}:"
    if not outputs["cluster"].startswith(prefix + "cluster/") or not outputs["service"].startswith(prefix + "service/"):
        raise Refused("Receipt resources do not match the registered account and region", 422)
    cluster = outputs["cluster"].rsplit("/", 1)[1]
    if outputs["service"].split("/")[1] != cluster:
        raise Refused("Receipt service does not belong to the declared cluster", 422)
    if not outputs["image_repository"].startswith(f"{account}.dkr.ecr.{region}.amazonaws.com/"):
        raise Refused("Receipt registry does not match the registered account and region", 422)
    policy = target.receipt_outputs
    # Include producer identity and state freshness without copying raw state
    body = {
        "id": request["id"], "format": policy.format, "generation": generation,
        "producer_target": request["manifest"]["target"],
        "producer": request["execution"]["source"], "request_digest": request["digest"],
        "artifact_digest": request["manifest"]["artifact_digest"], "policy_digest": target.digest,
        "environment": target.environment, "account": account, "region": region,
        "backend": policy.backend.model_dump(), "observed_at": now, "expires_at": now + policy.valid_seconds,
        **evidence.model_dump(),
    }
    return {**body, "digest": fingerprint(body)}


# Reject alternate state locations or hidden credential overrides
def verify_backend(directory: Path, expected: dict) -> None:
    """Require the initialized S3 backend and default workspace before creating or applying a plan."""
    try:
        workspace = directory / ".terraform" / "environment"
        if workspace.exists() and workspace.read_text().strip() != "default":
            raise ValueError("Unsupported workspace")
        metadata = json.loads((directory / ".terraform" / "terraform.tfstate").read_text())
        if metadata.get("version") != 3:
            raise ValueError("Unsupported backend metadata version")
        backend = metadata["backend"]
        if backend["type"] != "s3" or any(backend["config"].get(key) != value for key, value in expected.items()):
            raise ValueError("Different backend")
        # Custom endpoints could route the same bucket/key to a different service
        config = backend["config"]
        forbidden = ("endpoint", "endpoints", "iam_endpoint", "sts_endpoint", "access_key", "secret_key",
                     "token", "profile", "shared_credentials_file", "shared_credentials_files", "shared_config_files",
                     "assume_role", "assume_role_with_web_identity", "role_arn", "external_id")
        if any(config.get(key) for key in forbidden):
            raise ValueError("Custom backend endpoint")
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        raise Refused("Receipt producer needs the registered S3 backend in the default workspace", 422) from error


# Export only the four explicitly public typed ECS outputs
def extract_state(raw: bytes) -> InfrastructureEvidence:
    """Discard raw state and sensitive values before constructing the public schema."""
    if len(raw) > 8_000_000:
        raise Refused("State exceeds the receipt extraction limit", 422)
    try:
        state = json.loads(raw)
        outputs = {}
        for name in ("cluster", "service", "container", "image_repository"):
            output = state["outputs"][name]
            if output.get("sensitive") is not False or output.get("type") != "string":
                raise ValueError("Not an explicitly public string")
            outputs[name] = output["value"]
        return InfrastructureEvidence(state_lineage=state["lineage"], state_serial=state["serial"],
                                      state_digest=hashlib.sha256(raw).hexdigest(), outputs=outputs)
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        raise Refused("State must contain the four typed, non-sensitive ECS receipt outputs", 422) from error
