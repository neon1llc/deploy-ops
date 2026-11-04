# src/deploy_ops/plans.py - Reduce tool previews to changes without retaining secret values.


import re
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from typing import Any

from .errors import Refused
from .models import Change, Cloud, Engine, Preview

# Map only recognized saved-plan operations to the policy vocabulary
ACTIONS = {
    ("create",): "create", ("update",): "update", ("delete",): "delete",
    ("read",): "read", ("no-op",): "no-op", ("delete", "create"): "replace",
    ("create", "delete"): "replace",
}
# Keep provider resource namespaces explicit during normalization
PREFIXES = {"local": ("terraform_", "local_", "random_"), "aws": ("aws_",), "azure": ("azurerm_", "azuread_"), "gcp": ("google_", "google-beta_")}


# Find executable provisioners anywhere in nested configuration
def _contains_provisioner(value: Any) -> bool:
    if isinstance(value, dict):
        return bool(value.get("provisioners")) or any(_contains_provisioner(v) for v in value.values())
    if isinstance(value, list):
        return any(_contains_provisioner(v) for v in value)
    return False


# Propagate unknown planned values into manual-review requirements
def _has_unknown(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_has_unknown(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_unknown(item) for item in value)
    return value is True


# Reduce native plan details to the policy-relevant public change set
def normalize(engine: Engine, cloud: Cloud, raw: dict) -> Preview:
    """Fail closed on unrecognized plans and reserve generic evidence for human review."""
    if engine == "delivery":
        return Preview.model_validate(raw["preview"])
    if engine == "manifest":
        preview = Preview.model_validate(raw)
        return preview.model_copy(update={"manual_only": True})
    if not re.fullmatch(r"1\.[0-9]+", str(raw.get("format_version", ""))) or not isinstance(
        raw.get("planned_values"), dict
    ):
        raise Refused("Expected terraform/tofu show -json output with format_version 1.x", 422)
    if raw.get("errored") or raw.get("complete") is False or raw.get("deferred_changes"):
        raise Refused("Incomplete, deferred, or errored plans cannot be approved", 422)
    resources = raw.get("resource_changes", [])
    if not isinstance(resources, list) or len(resources) > 2000:
        raise Refused("Invalid resource_changes list", 422)
    changes = []
    findings = []
    unknown = False
    for resource in resources:
        try:
            detail = resource["change"]
            unknown = unknown or _has_unknown(detail.get("after_unknown", {}))
            action = ACTIONS.get(tuple(detail["actions"]))
            if action is None:
                raise ValueError("Unknown change action")
            if detail.get("importing"):
                action = "import"
            elif resource.get("previous_address"):
                action = "move"
            kind = resource["type"]
            if not kind.startswith(PREFIXES[cloud]):
                findings.append("Plan includes a resource outside the selected cloud")
            changes.append(Change(address=resource["address"], resource_type=kind, action=action))
        except (AttributeError, KeyError, TypeError, ValueError) as error:
            raise Refused("Resource changes contain an unsupported shape or action", 422) from error
    if _contains_provisioner(raw.get("configuration", {})):
        findings.append("Provisioners are not allowed in the guarded execution path")
    if raw.get("resource_drift"):
        findings.append("Existing infrastructure drift needs a fresh reviewed baseline")
    # Output values stay out of the ledger, but changing them still requires review
    output_changes = raw.get("output_changes", {})
    if not isinstance(output_changes, dict) or any(
        not isinstance(value, dict) or not isinstance(value.get("actions"), list)
        for value in output_changes.values()
    ):
        raise Refused("Invalid output_changes", 422)
    output_changed = any(value.get("actions") != ["no-op"] for value in output_changes.values())
    return Preview(changes=changes, findings=sorted(set(findings)), manual_only=output_changed or unknown)


# Reject invalid currencies and non-finite estimates before rounding
def cost_delta(raw: dict) -> int:
    """Round an Infracost monthly USD estimate upward to whole cents."""
    value = raw.get("diffTotalMonthlyCost")
    if value is None or raw.get("currency", "USD") != "USD":
        raise Refused("Cost evidence needs diffTotalMonthlyCost in USD", 422)
    try:
        amount = Decimal(str(value))
        if not amount.is_finite():
            raise InvalidOperation
        return int((amount * 100).to_integral_value(rounding=ROUND_CEILING))
    except (InvalidOperation, ValueError, OverflowError) as error:
        raise Refused("Invalid monthly cost estimate", 422) from error
