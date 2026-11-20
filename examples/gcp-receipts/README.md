# GCP infrastructure receipts

This example connects a protected Terraform/OpenTofu worker to a separate Cloud Run application worker. Infrastructure runs once, and later app releases reuse its authenticated receipt with a new immutable image digest. The controller invalidates the old receipt as soon as another infrastructure execution claims authority, even if that execution later fails or needs reconciliation.

Use [the controller registration](../../config/gcp-receipts.example.toml), this workspace, and the [split-job infrastructure workflow](../../integrations/github/reusable-deploy.yml). The [application workflow](../../integrations/github/delivery-worker.yml) selects this workspace for `gcp-development`. Replace repository IDs, workflow commit pins, project, region, backend, and workspace digest in operator-controlled configuration. Terraform 1.12.2 and OpenTofu 1.10.0 use the same typed `gcp-cloud-run-v1` output contract.

The [infrastructure root](infrastructure/main.tf) creates one Cloud Run service. It assumes the GCS state bucket, regional Artifact Registry image, APIs, billing, and runtime service account already exist. It creates no IAM bindings, service accounts, API enablement, or public ingress. Zero minimum and one maximum instance bound capacity, and deletion protection stays on. Choose ingress, runtime permissions, cleanup, and cost limits before live use. The example requires manual approval and omits a cost estimate rather than inventing one.

## Set up the producer

1. Copy the infrastructure root into the producer repository's `infrastructure/` directory.
2. Edit its backend to match the registered bucket and prefix.
3. Copy `inputs.auto.tfvars.json.example` to `inputs.auto.tfvars.json`, fill in public values, and commit it with the reviewed provider lock.

Keep credentials out of variables, source, and backend files. The protected runner gets separate planning and execution credentials through GCP workload federation.

The provider source is `registry.opentofu.org/hashicorp/google` at 6.50.0, which keeps both engines on the same registry and lock entries, since OpenTofu otherwise rewrites Terraform's default-registry address. Reproduce dependency initialization with your chosen engine before changing the pin. Resource lifecycle settings exclude application image and traffic changes from infrastructure ownership. Other service settings stay under IaC review.

The runner writes an operator-derived backend override before acquiring credentials. It replaces the source backend block with the registered GCS destination, refuses existing override files, disables cached backend migration with `-reconfigure`, and removes the generated file afterward. Native metadata is checked before planning, applying, and state extraction. Custom endpoints, explicit credentials, impersonation, encryption overrides, and unknown backend settings are refused. This controls backend selection only. Provider binaries and source still need review and isolated runners with scoped identities.

## Run it

Run the protected infrastructure workflow with target `gcp-infrastructure`, environment `gcp-development`, planning environment `gcp-development-plan`, and engine `opentofu` or `terraform`. After the reviewed saved plan succeeds, its request ID is the receipt ID. Then run the separate application workflow with the same environment, receipt ID, and immutable image:

```sh
deploy-ops --project deploy-ops.toml run gcp-development --component web --only \
  --image 'us-central1-docker.pkg.dev/example-project/apps/web@sha256:IMAGE_DIGEST' \
  --receipts '{"foundation":"RECEIPT_ID"}' --wait-seconds 1800
```

For an app-only release, use the still-valid receipt and another digest from the same repository. Build and publish that image in a separate job with only its registry permission. The application worker runs no repository build hooks. It verifies the service baseline, creates a no-traffic revision, checks readiness, and moves traffic only after the revision is ready.

The receipt exports `project`, `region`, `name`, and `image_repository`. The controller checks the producer and consumer project, region, and environment, and the regional registry must match that scope. Outputs come from the producer's state observation, not proof of current resource health or registry ownership. The Cloud Run adapter reads back the service before changing it.

## Evidence

Controller, API, and provider-command fixtures cover both engine handoffs, app-only reuse, and invalidation. Native initialization and provider validation ran in a credentialless VM, and native GCS backend metadata and override checks ran against an offline synthetic storage API. The complete workflow hasn't been qualified against a live GCP project or installed GitHub workflow.
