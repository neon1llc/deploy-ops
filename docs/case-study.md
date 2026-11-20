# Case study: infrastructure and application delivery

## Intended outcome

A developer or authenticated automation deploys infrastructure and an application from separate repositories, then releases the application again without redeploying its infrastructure. Environments may use different cloud runtimes, with reviewed artifacts, bounded authority, and traceable outcomes.

## Demonstrated local journey

The bundled example uses separate infrastructure and application source roots. The infrastructure adapter creates an isolated local deployment directory and records its public path. The app runs checks and a build, requests approval of its frozen artifact, and deploys versioned static content. An app-only release reuses the infrastructure receipt. Staging promotion preserves the artifact digest, and a loopback HTTP request verifies the promoted content.

Tests also change the local app and restore a prior successful artifact through a reviewed rollback. Changed source, changed artifacts, exhausted delegation, stale prior deployment state, and inappropriate cloud authority are refused. These are local results, not cloud ones.

## Existing cloud building blocks

The authority service verifies GitHub OIDC identity and pinned workflow context. The split-job Terraform/OpenTofu worker uses separate planning and execution identities. Native app adapters apply exact image changes to existing ECS, Container Apps, Cloud Run, and Kubernetes targets.

Federation protocols and GCP recipes have fixture tests only. AWS and Azure adapters are partly live-checked ([support matrix](support-matrix.md)). Cross-workflow receipt exchange, image provenance enforcement, and a fully isolated infrastructure-to-app pipeline still need live qualification.

## First cloud case study

Not yet recorded. It needs disposable targets and explicit spending authority. Deploy infrastructure, publish a receipt, deploy the app from it, release the app again without an infrastructure apply, and promote the same image to staging. Include a manual approval, a bounded grant, a denied authority change, an expired lease, an interrupted operation blocked until reconciliation, and a rejected role assumption from an unregistered workflow. Publish no account identifiers, raw plans, tokens, or invented numbers. Repeat on Azure and GCP before claiming multi-cloud parity.
