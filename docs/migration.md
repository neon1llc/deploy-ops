# Migration from DeployGuard

The display name is **DeployOps**, repository and command are `deploy-ops`, package is `deploy_ops`,
and environment variables use `DEPLOY_OPS_`. The proposed package distribution is `neon1-deploy-ops`.
Registry and trademark availability have not been cleared.

The prior controller, policy, OIDC, Slack, cloud-federation, and split-job IaC behavior was kept and renamed. Its former CLI is now `deploy-ops gate`. The new default CLI owns complete release operations. The previous 66 regression tests remain in the source tree.

Start with a fresh state database and a new operator token. Live DeployGuard requests, leases, and grants don't migrate. To switch:

1. Export historical audit records.
2. Let active work finish.
3. Revoke old grants.
4. Replace cloud trust only after testing the new pinned workflow.

Don't copy an active lease into a differently named service or reuse old audience settings.

The full prior ZIP is a separate artifact. The MIT license notice stays in this repository. New features and the combined distribution use Apache-2.0 with NOTICE.

## Native infrastructure and local cloud grants

The v0.11.0 native adapters use the existing release ledger and approval state machine, so no database schema migration is needed. The optional `local_cloud` target flag defaults to false and is omitted from historical target fingerprints when unused. Existing federation stays separate.

Default protected resource types now include CloudFormation and ARM names alongside Terraform names. This changes policy fingerprints for targets using the default denylist. Finish or reconcile in-flight work before upgrading, then prepare fresh requests and recreate affected delegated windows. Don't reuse an approval under a changed policy.

An explicit local cloud grant uses its own resource-type and action allowlist, needs manual approval, and never enables production or delegation. Missing, expired, or mismatched grants leave configuration editing available but disable cloud execution.

## Isolated execution and Container Apps recovery

The optional `local_isolated_iac` target mode keeps local operator provenance separate from GitHub claims. It's omitted from existing target fingerprints when unused, and no database schema migration is needed. Register a new private grant for the isolated route. Existing local or federated grants don't gain this authority.

Container Apps requests prepared before exact revision journaling must be completed or reconciled under their original version. Prepare a fresh release for the new adapter, which refuses old prepared requests without a reviewed recovery identity. Existing Single-mode apps must contain exactly one named application container.
