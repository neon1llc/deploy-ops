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
