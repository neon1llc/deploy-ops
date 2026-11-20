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

The release ledger is local to each runner. The controller can issue typed AWS ECS and GCP Cloud Run
public-output receipts for separately authenticated application workers. Consumers recheck exact references
online. These are controller records, not portable offline signatures. See
[receipt configuration](../../docs/infrastructure-receipts.md). Cross-worker cloud behavior has fixture
coverage and native offline backend checks. A live protected-workflow journey remains unqualified.

Runtime health means the adapter's documented provider readiness check. Add external application probes
before production qualification. No example automatically rolls back a database or destructive IaC change.

## Portable inspection artifact

The application recipe preserves the deployment command's exit code and captures its returned release
separately. Its new `deployment-inspection-RUN-ATTEMPT` artifact includes only `inspection.json`, a
[versioned allowlisted summary](../../docs/inspection.md). It does not add raw state or source to that
artifact. The existing private `deployment-evidence` administrative artifact contract is retained
separately. Its raw database and prepared files have different privacy and restore requirements.

Capture failure, pre-execution absence and a missing upload file fail their own steps. They cannot
manufacture a successful release. The transport summary records deployment/capture/upload step
outcomes and the upload action's returned artifact ID, archive digest and URL. The archive digest is
distinct from the inspection summary's content digest. `upload: not_recorded` inside the file is
intentional: the capture cannot attest to an upload that has not happened yet.

The same private repository access boundary, reviewed action/toolkit pins and one-day retention remain.
This recipe does not grant additional recipients access or promise indefinite availability. An expired
or failed upload leaves the artifact unavailable. Retain the job outcome and transport record. A hard
runner loss or cancellation can prevent capture and `always()` steps from running at all. Do not infer
that a missing artifact means no provider write occurred.

Download the artifact and run `deploy-ops evidence inspect inspection.json --html inspection.html`
from any directory with the matching runtime installed. No source or cloud login is needed. Current
local interruption/inspection checks do not qualify GitHub upload, expiry, OIDC or a protected live
workflow. Those must be exercised separately before relying on this transport operationally.
