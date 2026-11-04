# Migration from DeployGuard

The display name is **DeployOps**, repository and command are `deploy-ops`, package is `deploy_ops`,
and environment variables use `DEPLOY_OPS_`. The proposed package distribution is `neon1-deploy-ops`.
Registry and trademark availability have not been cleared.

The prior controller, policy, OIDC, Slack, cloud-federation, and split-job IaC behavior was retained and
renamed. Its former CLI is now `deploy-ops gate`. The new default CLI owns complete release operations.
The previous 66 regression tests remain in the source tree.

Start with a fresh state database and new operator token. No automatic migration of live DeployGuard
requests, leases, or grants is supplied. Export historical audit records first, let active work finish,
revoke old grants, and replace cloud trust only after testing the new pinned workflow. Do not copy an
active lease into a differently named service or silently reuse old audience settings.

The full prior ZIP remains a separate artifact. The retained MIT license notice remains in this repository.
New features and the combined distribution use Apache-2.0 with NOTICE.

## Native infrastructure and local cloud grants

The v0.11.0 native adapters use the existing release ledger and approval state machine. No database
schema migration is required. The optional `local_cloud` target flag defaults to false and is omitted
from historical target fingerprints when unused. Existing federation remains separate.

Default protected resource types now include CloudFormation and ARM names alongside Terraform names.
This intentionally changes policy fingerprints for targets using the default denylist. Finish or reconcile
in-flight work before upgrading, then prepare fresh requests and recreate any affected delegated windows.
Do not reuse a previous approval under a changed policy. An explicit local cloud grant uses its exact
resource-type and action allowlist instead, requires manual approval, and never enables production or
delegation. Missing, expired or mismatched grants leave configuration editing available but cloud
execution disabled.

## Isolated execution and Container Apps recovery

The optional `local_isolated_iac` target mode keeps local operator provenance separate from GitHub
claims. It is omitted from existing target fingerprints when unused. No database schema migration is
required. Register a new private grant for the isolated route; existing local or federated grants do
not gain this authority automatically.

Container Apps requests prepared before exact revision journaling must be completed or reconciled
under their original version. Prepare a fresh release for the new adapter; it refuses old prepared
requests without a reviewed recovery identity. Existing Single-mode apps must contain exactly one
named application container.
