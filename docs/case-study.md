# Case study: infrastructure and application delivery

## Intended outcome

A developer or identified agent can deploy infrastructure and an application from separate repositories.
The application can be released again without redeploying its infrastructure. Environments may use
different cloud runtimes. Reviewed artifacts, bounded authority, and traceable outcomes remain consistent.

This is the DeployOps product boundary. Approval is one subsystem. Builds, dependency outputs, delivery,
verification, promotion, recovery, and operational evidence are also part of the system.

## Demonstrated local journey

The bundled example uses separate infrastructure and application source roots. The infrastructure adapter
creates an isolated local deployment directory and records its public path. The app runs checks and a build,
requests approval of its frozen artifact, and deploys versioned static content. An app-only release reuses
the infrastructure receipt. Staging promotion preserves the artifact digest. A loopback HTTP request verifies
that the promoted content is served.

The tests also change the local app and restore a prior successful artifact through a new reviewed rollback.
Changed source, changed artifacts, exhausted delegation, stale prior deployment state, and inappropriate
cloud authority are refused. These are local execution results, not cloud success stories.

## Existing cloud building blocks

The independent authority service verifies GitHub OIDC identity and pinned workflow context. The split-job
Terraform/OpenTofu worker uses separate planning and execution identities. Native app adapters prepare and
apply exact image changes to existing ECS, Container Apps, Cloud Run, and Kubernetes targets.

Provider command contracts and federation protocols have local mocked tests. The recipes have not run
against real accounts. Cross-workflow cloud receipt exchange, image provenance enforcement, and a fully
isolated infrastructure-to-app pipeline still need implementation and live qualification.

## Record the first cloud case study

Use disposable targets and explicit spending authority. Deploy a small infrastructure repository through
its own pipeline, publish a verifiable output receipt, and deploy an app repository using that receipt.
Release the app again without an infrastructure apply. Promote the same immutable image to staging.

Show an exact manual approval, an eligible bounded grant, a denied authority change, an expired lease,
and an interrupted operation that remains blocked until reconciliation. Attempt cloud role assumption from
an unregistered workflow and verify rejection. Capture the source and workflow revisions, artifact and
request digests, cloud audit correlation, provider result, and external application probe.

Measure deployment time, approval latency, recovery steps, and actual cost only after the scenario runs.
Record failures alongside successes. Do not publish account identifiers, raw plans, tokens, or fabricated
performance results. Repeat the complete path on Azure and GCP before claiming live multi-cloud parity.

## What makes the project useful

The same release and authority model serves a local CLI, a local browser console, and trusted CI workers.
Provider-specific adapters retain meaningful differences instead of promising that every workload moves
between clouds by changing one word. A future Deep UI can call these operations without owning a second
copy of the deployment logic.

See [verification](verification.md), [capabilities](capabilities.md), and [the roadmap](roadmap.md) for the
current evidence and next complete user paths.
