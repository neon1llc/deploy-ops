# GCP infrastructure receipts

This example connects a protected Terraform/OpenTofu worker to a separate Cloud Run application worker. Infrastructure runs once; later app releases reuse its authenticated receipt and supply a new immutable image digest. The controller invalidates the old receipt as soon as another infrastructure execution claims authority, including when that execution later fails or needs reconciliation.

Use [the controller registration](../../config/gcp-receipts.example.toml), this workspace and the existing [split-job infrastructure workflow](../../integrations/github/reusable-deploy.yml). The [application workflow](../../integrations/github/delivery-worker.yml) selects this workspace for `gcp-development`. Replace repository IDs, workflow commit pins, project, region, backend and workspace digest in operator-controlled configuration. Both Terraform 1.12.2 and OpenTofu 1.10.0 use the same typed `gcp-cloud-run-v1` output contract.

The [infrastructure root](infrastructure/main.tf) creates one Cloud Run service. It assumes the GCS state bucket, regional Artifact Registry image, APIs, billing and runtime service account already exist. It creates no IAM bindings, service accounts, API enablement or public ingress. Its zero minimum and one maximum instance bound capacity; deletion protection remains enabled. Choose ingress, runtime permissions, cleanup and cost limits deliberately before live use. The example requires manual approval and explicitly omits a cost estimate; it does not manufacture one.

Copy this infrastructure root into the producer repository's `infrastructure/` directory. Edit its backend to match the registered bucket/prefix. Copy `inputs.auto.tfvars.json.example` to `inputs.auto.tfvars.json`, supply public values and commit them with the reviewed provider lock. Keep credentials out of variables, source and backend files. The protected runner obtains distinct planning and execution credentials through the existing GCP workload-federation path.

The provider source is explicitly `registry.opentofu.org/hashicorp/google` at 6.50.0. This keeps both engines on the same registry and lock entries; OpenTofu otherwise rewrites Terraform's default-registry address. Reproduce dependency initialization with the chosen native engine before changing the pin. Application image and traffic changes are excluded from infrastructure ownership through the resource lifecycle settings; other service settings stay under IaC review.

For GCP receipt work, the runner temporarily writes an operator-derived backend override before acquiring credentials. Native override semantics replace the entire source backend block with the registered GCS destination. Existing override files are refused, cached backend migration is disabled with `-reconfigure`, and the generated file is removed afterwards. Native metadata is checked before planning, applying and state extraction. Custom endpoints, explicit credentials, impersonation, encryption overrides and unknown backend settings are refused. This controls backend selection; provider binaries and source still require review and isolated runners with scoped identities.

Run the protected infrastructure workflow with target `gcp-infrastructure`, environment `gcp-development`, planning environment `gcp-development-plan`, and engine `opentofu` or `terraform`. After the reviewed saved plan succeeds, its request ID identifies the receipt. Then run the separate application workflow with the same environment, receipt ID and immutable image:

```sh
deploy-ops --project deploy-ops.toml run gcp-development --component web --only \
  --image 'us-central1-docker.pkg.dev/example-project/apps/web@sha256:IMAGE_DIGEST' \
  --receipts '{"foundation":"RECEIPT_ID"}' --wait-seconds 1800
```

For an app-only release, use the still-valid receipt and another image digest from the same repository. Build and publish that image in a separate job with only its required registry permission. The application worker executes no repository build hooks. It verifies the existing service baseline, creates a no-traffic revision, checks readiness and moves traffic only after the revision is ready.

The receipt exports `project`, `region`, `name` and `image_repository`. The controller checks the producer and consumer project, region and environment; the regional registry must match that scope. Outputs come from the successful worker's state observation and are not proof of current resource health or an independent registry-ownership check. The Cloud Run adapter performs its own readback before changing the service.

Current evidence includes controller/API and provider-command fixtures for both engine handoffs, app-only reuse and invalidation; native tool initialization and provider validation in a credentialless VM; and native GCS backend metadata/override checks against an offline synthetic storage API. This complete workflow has not been qualified against a live GCP project or installed GitHub workflow.
