# Infrastructure receipts

A trusted infrastructure worker publishes typed public outputs through the controller. A separate application worker supplies the resulting execution ID when preparing delivery. The controller verifies producer identity, target, policy, expiry, and revocation before accepting or executing the application change. The application can then release again without another infrastructure apply.

| Contract | Tools | Backend | Outputs |
|---|---|---|---|
| AWS | OpenTofu 1.10.0 | S3, default workspace | `aws-ecs-v1` |
| [GCP](../examples/gcp-receipts/README.md) | OpenTofu 1.10.0 or Terraform 1.12.2 | GCS, default workspace | `gcp-cloud-run-v1` |

Both have fixture evidence only. The full handoff hasn't run against live AWS or GCP. Other backends, cross-account delivery, and other output schemas aren't qualified.

## Register the contract

Start with [the controller example](../config/receipts.example.toml) and [the application workspace](../examples/aws-receipts/deploy-ops.toml). Replace the synthetic repository IDs, workflow commit pins, role identities, S3 bucket, key, region, and workspace digest. Keep these contracts in the protected authority repository. The infrastructure target sets `receipt_outputs` and the application target maps dependency names to producer targets through `receipt_dependencies`. Environment, AWS account, and region must match, and staging and production need separate receipts.

The infrastructure root must expose exactly these public string values. Other outputs are ignored. Sensitive, missing, or non-string fields are refused before publication.

| Output | Required value |
| --- | --- |
| `cluster` | Full AWS ECS cluster ARN |
| `service` | Full ECS service ARN belonging to that cluster |
| `container` | Named application container |
| `image_repository` | ECR repository URL in the same account and region |

OpenTofu init metadata must match the registered S3 backend, checked before planning and again before apply and state reads. Custom endpoints, credential, profile, or role overrides, non-default workspaces, and unknown metadata formats are refused. The worker uses controller-issued credentials.

## Produce and consume

Use the [split-job workflow](../integrations/github/reusable-deploy.yml) to plan and apply the saved infrastructure plan. A successful apply extracts the selected public outputs and state lineage and serial, then records its receipt atomically with completion. The receipt ID is the original request ID, and repeating identical completion returns the same receipt.

Build and push the application image in a separate job with only registry permissions. Pass its immutable ECR digest into the trusted application workflow, which never runs repository build hooks.

```bash
# In the authenticated application delivery worker:
deploy-ops gate receipt RECEIPT_ID --target hello-development-web

deploy-ops --project deploy-ops.toml run development --component web --only \
  --image '111111111111.dkr.ecr.us-east-1.amazonaws.com/web@sha256:IMAGE_DIGEST' \
  --receipts '{"foundation":"RECEIPT_ID"}' --wait-seconds 1800
```

An app-only release uses the same valid receipt and a new immutable image. Promotion accepts `--receipts` for the destination environment, keeps the original image digest, and needs fresh approval. Cross-account image copying isn't automatic. The console accepts the same JSON receipt mapping. The [application workflow](../integrations/github/delivery-worker.yml) accepts a JSON `receipts` input. The workflow selects the receipt workspace for `development`, and `gcp-development` selects the GCP one. Never put producer execution tokens in app jobs or workflow outputs.

## Invalidation and recovery

The controller checks exact receipt references at submission, claim, heartbeat, and completion. A new producer execution invalidates earlier receipts when it claims authority. Failure or reconciliation doesn't make them valid again, because a failed apply can still change infrastructure. Publish fresh evidence through a new reviewed infrastructure execution before resuming delivery.

An operator can revoke a receipt through the existing admin credential:

```bash
deploy-ops gate revoke-receipt RECEIPT_ID
```

Revocation prevents new authority and interrupts later heartbeats. It doesn't undo an operation AWS already accepted, so reconcile uncertain operations before retrying.

The state digest identifies the `tofu state pull` representation, not the S3 object bytes or version ID. A valid receipt proves a trusted worker's observation. It doesn't detect out-of-band drift or prove current health. The ECS adapter reads the service revision before applying and checks convergence afterwards. Live backend observations, image provenance enforcement, and external application probes are unfinished.

## GCP contract

Use [the GCP controller example](../config/gcp-receipts.example.toml) and [the GCP workspace](../examples/gcp-receipts/README.md). Producer and consumer both register an explicit `receipt_region`, matching project identities, and environment. The typed outputs are `project`, `region`, `name`, and `image_repository`, which must be a regional Artifact Registry path in that project.

The operator registers a GCS bucket and canonical prefix. The runner writes a temporary native backend override before obtaining credentials, refuses competing overrides, and uses `init -reconfigure`. Backend metadata is checked before planning, applying, and state extraction. Explicit credentials, custom endpoints, impersonation, encryption overrides, and unknown metadata fields are refused.

This uses the isolated OIDC plan and apply workflow with distinct planning and execution identities. It doesn't enable Terraform or OpenTofu in local cloud grants. The state digest describes the `state pull` representation, not a GCS object generation. Follow a receipt with live service readback. Scope backend access separately from application delivery.
