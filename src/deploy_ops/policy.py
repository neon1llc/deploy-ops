# src/deploy_ops/policy.py - Decide scope without consulting the agent that requested it.

from fnmatch import fnmatchcase

from .config import Target


def changed_resources(manifest: dict) -> list[dict]:
    return [item for item in manifest["preview"]["changes"] if item["action"] not in ("no-op", "read")]


def hard_denial(target: Target, manifest: dict) -> str | None:
    """Keep prohibited changes outside both human approvals and overnight grants."""
    preview = manifest["preview"]
    if preview["findings"]:
        return "; ".join(preview["findings"])
    for change in preview["changes"]:
        if change["action"] not in target.policy.allowed_actions:
            return f"Action {change['action']} is not allowed for this target"
        if change["action"] not in ("no-op", "read") and any(
            fnmatchcase(change["resource_type"], pattern) for pattern in target.policy.blocked_types
        ):
            return f"Changes to {change['resource_type']} are outside this controller's authority"
    return None


def fits_delegation(target: Target, manifest: dict, grant: dict, now: int) -> bool:
    """Count only positive cost estimates and never refund a failed execution."""
    policy = target.policy
    changes = changed_resources(manifest)
    cost = manifest["estimated_monthly_delta_cents"]
    positive_cost = max(0, cost or 0)
    return (
        not target.production
        and policy.allow_delegation
        and manifest["engine"] != "manifest"
        and not manifest["preview"]["manual_only"]
        and bool(changes)
        and grant["active"]
        and grant["starts_at"] <= now < grant["expires_at"]
        and grant["target"] == manifest["target"]
        and grant["policy_digest"] == target.digest
        and grant["uses"] < grant["max_uses"]
        and len(changes) <= policy.max_changes
        and grant["changes"] + len(changes) <= policy.max_total_changes
        and (cost is not None or not policy.require_cost_estimate)
        and positive_cost <= policy.max_monthly_delta_cents
        and grant["estimated_cents"] + positive_cost <= policy.max_total_monthly_delta_cents
        and all(
            item["resource_type"] in policy.auto_types and item["action"] in policy.auto_actions
            for item in changes
        )
    )
