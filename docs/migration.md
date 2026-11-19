# Migration from DeployGuard

The display name is **DeployOps**, the repository and command are `deploy-ops`, the package is `deploy_ops`, and environment variables use `DEPLOY_OPS_`. The proposed distribution is `neon1-deploy-ops`. Registry and trademark availability haven't been cleared.

The prior controller, policy, OIDC, Slack, cloud-federation, and split-job IaC behavior was kept and renamed. Its former CLI is now `deploy-ops gate`. The new default CLI owns complete release operations. The previous 66 regression tests remain in the source tree.

Start with a fresh state database and a new operator token. Live DeployGuard requests, leases, and grants don't migrate. To switch:

1. Export historical audit records.
2. Let active work finish.
3. Revoke old grants.
4. Replace cloud trust only after testing the new pinned workflow.

Don't copy an active lease into a renamed service or reuse old audience settings. The MIT license notice stays in this repository, and new features use Apache-2.0 with NOTICE.

## Native infrastructure and local cloud grants

The v0.11.0 native adapters use the existing release ledger and approval state machine, so no schema migration is needed. The optional `local_cloud` target flag defaults to false and is omitted from historical target fingerprints when unused.

Default protected resource types now include CloudFormation and ARM names alongside Terraform names. This changes policy fingerprints for targets using the default denylist. Finish or reconcile in-flight work before upgrading, then prepare fresh requests and recreate delegated windows. Don't reuse an approval under a changed policy.

## Isolated execution and Container Apps recovery

The optional `local_isolated_iac` target mode keeps local operator provenance separate from GitHub claims. It's omitted from existing target fingerprints when unused, and no database schema migration is needed. Register a new private grant for the isolated route. Existing local or federated grants don't gain this authority.

Container Apps requests prepared before exact revision journaling must be completed or reconciled under their original version. Prepare a fresh release for the new adapter, which refuses old prepared requests without a reviewed recovery identity. Existing Single-mode apps must contain exactly one named application container.

## Waiting transition identity

New waiting nodes retain the exact `waiting_request` ID/digest in the same transaction as their
first `release.waiting` event and outboxes. Existing history and delivery IDs stay unchanged. An
older waiting node without this binding records one current decision-needed transition on its next
advance, then unchanged observations stay quiet. Stop old writers before upgrading; older writers
do not enforce this deduplication. This adds no reminder scheduler or authority.

## Local target ownership

New directory/static preparations use adapter contract 2. Readers authenticate old evidence without
rewriting it. Pending contract 1 local operations require fresh preparation before execution; finish
or reconcile old work under its matching runtime before upgrading. Previously prepared ownership
is derived from authenticated artifacts when checking another writer. Missing historical evidence
requires inspection before assigning a different owner. Failed and cancelled reservations are retained.
No database migration, automatic takeover or implicit cleanup is supplied.

## Observed controller timing

Request reads add a safe `timing` projection without changing retained manifests, request digests,
approvals or database records. Older clients can ignore it. New clients report timing unavailable
when an older controller omits it; existing controller enforcement remains authoritative. The
projection contains explicitly selected timestamps and limits, without execution tokens or hashes.
