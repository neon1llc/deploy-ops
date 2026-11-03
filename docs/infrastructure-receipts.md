# Infrastructure receipts

A trusted infrastructure worker can publish typed public outputs through the existing controller. A separate
application worker supplies the resulting execution ID when preparing delivery. The controller verifies
producer identity, target, policy, expiry and revocation before accepting or executing the application change.
The application can then release again without another infrastructure apply.

This path currently supports OpenTofu 1.10.0, an S3 backend in the default workspace, and the `aws-ecs-v1`
output schema. It has local controller and provider-command fixture evidence. It has not run against AWS.
Terraform, alternate backends, cross-account delivery and other output schemas are not qualified here.

## Register the contract

Start with [the controller example](../config/receipts.example.toml) and
[the application workspace](../examples/aws-receipts/deploy-ops.toml). Replace synthetic repository IDs,
reviewed workflow commit pins, role identities, S3 bucket/key/region and the workspace digest. Keep these
contracts in the protected authority repository. A contributor's repository cannot grant itself receipt access.

The infrastructure target configures `receipt_outputs`. The application target maps workspace dependency
names to approved producer targets through `receipt_dependencies`. Environment, AWS account and region must
match. Use separate receipts and target registrations for staging or production.

The infrastructure root must expose exactly these selected public string values. Other outputs are ignored.
Sensitive values and missing or non-string fields are refused before publication.

| Output | Required value |
| --- | --- |
| `cluster` | Full AWS ECS cluster ARN |
| `service` | Full ECS service ARN belonging to that cluster |
| `container` | Named application container |
| `image_repository` | ECR repository URL in the same account and region |

OpenTofu initialization metadata must match the registered S3 backend. Planning checks this before creating
the saved plan, whose backend is bound inside the approved artifact. Execution checks again before apply
and before reading state. Custom endpoints, credential/profile overrides, role overrides, non-default
workspaces and unknown backend metadata formats are refused. The worker uses controller-issued credentials.

## Produce and consume

Use the existing [split-job workflow](../integrations/github/reusable-deploy.yml) for planning and application
of the saved infrastructure plan. A successful configured apply extracts the selected public outputs and
state lineage/serial, then records its receipt atomically with completion. The receipt ID is the original
request ID from the ticket. Repeating identical completion returns the same receipt. Different evidence
cannot replace it.

Build and push the application image in a separate job without cloud writer identity or permission to mint
cloud OIDC credentials. Pass its immutable ECR digest into the trusted application workflow. That worker
uses the approved workspace and never executes repository build hooks.

```bash
# In the authenticated application delivery worker:
deploy-ops gate receipt RECEIPT_ID --target hello-development-web

deploy-ops --project deploy-ops.toml run development --component web --only \
  --image '111111111111.dkr.ecr.us-east-1.amazonaws.com/web@sha256:IMAGE_DIGEST' \
  --receipts '{"foundation":"RECEIPT_ID"}' --wait-seconds 1800
```

Replace the receipt and digest placeholders with their actual values. An app-only release uses the same
valid receipt and a new immutable image. Promotion accepts `--receipts` for the destination environment,
keeps the original image digest and requires fresh approval. Cross-account image copying is not automatic.
The local console accepts the same JSON receipt mapping and shows the producer, source, state identity,
observation time and validity alongside the prepared change.

The protected [application workflow](../integrations/github/delivery-worker.yml) accepts a JSON `receipts`
input and selects the reviewed receipt workspace for `development`. Its other named environments keep the
cloud-app example without receipt dependencies. Review these fixed mappings and register their exact contract
before installation. Do not place producer execution tokens in app jobs or workflow outputs.

## Invalidation and recovery

The controller checks exact receipt references at submission, transactional claim, heartbeat and completion.
A new producer execution invalidates earlier receipts when it claims authority. Failure or reconciliation
does not make those receipts valid again, because a failed apply can still change infrastructure.
Publish fresh observed evidence through a new reviewed infrastructure execution before resuming delivery.

An operator can revoke a receipt through the existing admin credential:

```bash
deploy-ops gate revoke-receipt RECEIPT_ID
```

Revocation prevents new authority and interrupts subsequent heartbeats. It does not undo an operation
already accepted by AWS. Inspect and reconcile uncertain operations before retrying them.

Receipts are immutable controller records fetched through authenticated HTTPS. They are not portable
signed files. Their producer identity derives from verified GitHub OIDC and the claimed execution, not
caller-supplied identity labels. Raw state and secret outputs are not stored in the receipt or audit event.

The state digest identifies the representation returned by `tofu state pull`, not the original S3 object
bytes or version ID. A valid receipt proves a trusted worker's observation and controller-known generation.
It does not detect out-of-band infrastructure drift or prove current health. The ECS adapter still reads
the service revision before applying and checks convergence afterwards. Stronger live backend observations,
image provenance enforcement and external application probes remain unfinished.
