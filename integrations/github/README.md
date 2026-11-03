# GitHub integration

These are integration recipes, not active or qualified deployments. Keep the reusable workflows in a
protected authority repository. Pin both the reusable workflow and toolkit to reviewed commits. A caller
must not gain cloud access merely by naming an environment or adding `id-token: write`.

## Infrastructure

Use [reusable-deploy.yml](reusable-deploy.yml) with [caller.yml](caller.yml). The planning and execution
jobs have separate GitHub environments and cloud identities. Plans can contain secrets. This artifact
transport is restricted to private repositories. Configure a remote state backend and commit the provider
lock file. Read [the authority setup](../../docs/authority-setup.md) before enabling a writer.

## Applications

Use [delivery-worker.yml](delivery-worker.yml) after a separate unprivileged build job has published an
image digest. The worker takes `environment` and `image`, snapshots the app source, prepares a runtime
change, awaits exact approval, then applies and verifies it. It does not run repository build hooks.

The [cloud workspace](../../examples/cloud-app/deploy-ops.toml) shows environment-specific runtime
settings. Its `image_repository` limits release inputs to one registry repository. The digest belongs to
the release, so a new build does not require editing the workspace's registered policy digest.

Read the `config_digest` from `deploy-ops plan <environment>` and register it in the independent
[delivery controller](../../config/delivery.example.toml). Replace the example repository IDs, workflow
pin, cloud principals, and resource identifiers. Register Azure and GCP targets independently using
[the cloud identity templates](../clouds). The target name is `<workspace>-<environment>-<component>`.

Private cross-repository access needs deliberate setup. The caller's default token does not automatically
read a different private repository. Configure authorized reusable-workflow/action access and a narrowly
scoped read mechanism for the protected toolkit checkout. Never pass an organization administrator token
to a deployment job. This recipe intentionally does not invent or provision that credential.

## Important limits

Run the authority service separately over HTTPS. The deployment job must not have operator tokens.
Cloud trust must require the reviewed workflow identity and expected repository, ref, and environment.
Use the actual OIDC subject observed for that repository. Newer and older GitHub repositories can use
different default subject formats. Refer to [the source notes](../../docs/sources.md).

Current native application preparation and execution share one trusted job. It is safe only when the
workflow is protected and no contributor code runs there. The job can request both reader and writer
identities. Cloud IAM does not independently know that the application controller granted approval.
An independent identity broker or provider-enforced approval boundary is a further hardening step.

The release ledger is local to the runner. This preview does not exchange signed infrastructure-output
receipts between independent workflow runs. Supply operator-reviewed target identifiers for native app
jobs. The local workspace demonstrates dependency-aware infrastructure and app orchestration. Durable,
verified cross-job receipts are the next implementation step, not a completed cloud feature.

Runtime health means the adapter's documented provider readiness check. Add external application probes
before production qualification. No example automatically rolls back a database or destructive IaC change.
