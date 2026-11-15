# Isolated local GCP infrastructure

DeployOps can compile one registered Cloud Run recipe and run Terraform 1.12.2 or OpenTofu 1.10.0 in a dedicated Lima VM. Planning and apply need separate exact approvals. A successful apply issues the typed GCP infrastructure receipt, which the application worker can reuse for app-only releases.

This route uses local operator authority, so its records have no GitHub repository IDs or workflow claims. It accepts no HCL, modules, data sources, provisioners, command hooks, provider aliases, or endpoint overrides.

## Operator setup

Keep the grant, credential reference, provider lock, and native tools outside all application repositories. The grant and lock must be owned by the current user with mode 0600. The controller state directory must be private and shared by the application worker and infrastructure module.

Register an existing GCP project, one region, one Cloud Run service, a private GCS state bucket and prefix, a runtime service account, and a same-project regional Artifact Registry repository. Use immutable image digests. Google's public sample repository isn't accepted, so copy a reviewed image into your repository.

The host broker uses an existing service-account credential through native gcloud. Its type, project, service-account email, and fixed OAuth token endpoint must match the grant, and it needs permission to impersonate the stage accounts. Five accounts are distinct from each other and the broker: infrastructure planner, infrastructure executor, application reader, application writer, and runtime identity. Give the runtime identity no project roles unless the application needs them.

Planning needs Cloud Run reads and bucket-scoped GCS object and lock permissions. Execution needs the reviewed Run create, update, and read permissions and actAs on the runtime account. Application identities need no state-bucket access. Bootstrap IAM, APIs, bucket, registry, and identities separately.

Install the package's `isolated_guest.py` as root at `/opt/deployops-tools/isolated_guest.py`. The host checks its package hash before sending a token. Pin native executables at `/opt/deployops-tools/terraform` and `/opt/deployops-tools/tofu`. Install the reviewed Google 6.50.0 package as a root-owned filesystem mirror at:

```text
/opt/deployops-tools/providers/registry.opentofu.org/hashicorp/google/6.50.0/linux_amd64/
```

The executable there is `terraform-provider-google`. Keep the package's other files and license. Register its SHA256 and the native executable's SHA256 in the grant. Native init has no provider download fallback. Only Linux amd64 guests are supported.

The VM must be running with no host mounts, SSH agent or X11 forwarding, proxy environment, or application port forwards. Register the fingerprint of the effective `config` object from `limactl list NAME --json`, using DeployOps' `fingerprint` function. Runtime changes need a new grant. Native gcloud and limactl paths must be operator or root owned.

The root supervisor is trusted. It holds a kernel lock for one operation at a time, runs native tools under a fresh numeric UID and GID with no supplementary groups, and keeps source, provider lock, and approved plan root-owned and immutable to the worker. The temporary token crosses stdin and process environment, never arguments, persistent keys, or logs. Guest root can still read it, so this isolates the VM from contributor source and doesn't protect against a compromised guest administrator or kernel.

## Grant structure

The schema is `deploy_ops.isolated_recipe.IsolatedGrant`, and unknown fields are rejected. An operator-owned JSON grant has:

- `schema_version: 1`, a short `name`, `environment`, Unix `expires_at`, and an `authorization` note.
- `engine`: `terraform` or `opentofu`.
- `recipe`: `project`, `region`, `name`, `image_repository`, `image_digest`, `runtime_service_account`, and `backend` with `bucket` and `prefix`.
- `planner_service_account`, `executor_service_account`, `broker_service_account`, and an absolute `credentials_file`.
- Absolute `gcloud`, `limactl`, and `lock_file` paths, plus `lock_digest`, `tool_digest`, `provider_digest`, `vm`, and `vm_config_digest`.
- For application integration, all of `workspace_digest`, `app_component`, `app_dependency`, `app_planner_service_account`, and `app_executor_service_account`.

The fixed recipe sets ingress ALL for authenticated host probes, CPU 1, memory 256 MiB, request-based CPU allocation, minimum instances 0, revision maximum 1, concurrency 1, and a 30-second timeout. It creates no IAM policy or unauthenticated binding. Revision maximum 1 isn't a billing cap, and the pinned provider rejects `max_instance_count`. Deletion protection is off for this disposable recipe, and destruction needs separate authorization.

## Execute the two approved stages

The CLI, authenticated loopback console, and standalone module share one core. Replace the paths and IDs with reviewed values:

```sh
deploy-ops --state /private/operator/state isolated --grant /private/operator/grant.json prepare
deploy-ops --state /private/operator/state isolated --grant /private/operator/grant.json approve PLAN_REQUEST --digest EXACT_PLAN_REQUEST_DIGEST
deploy-ops --state /private/operator/state isolated --grant /private/operator/grant.json execute PLAN_REQUEST
deploy-ops --state /private/operator/state isolated --grant /private/operator/grant.json approve APPLY_REQUEST --digest EXACT_APPLY_REQUEST_DIGEST
deploy-ops --state /private/operator/state isolated --grant /private/operator/grant.json execute APPLY_REQUEST
```

The first execution returns a pending request for the saved native plan. The second verifies the retained plan bytes before claiming writer authority. The broker then mints a separate impersonated token limited to 900 seconds. Grant expiry or a shorter controller deadline wins.

An expired registration still allows status reads and recording an inspected failure. It can't prepare, approve, mint tokens, or execute. To close a running or uncertain record after inspection, run `deploy-ops --state /private/operator/state isolated --grant /private/operator/grant.json reconcile REQUEST --digest EXACT_DIGEST --note "Specific inspection findings"` or use the console. Reconciliation records failure. It doesn't stop a remote operation, remove resources, retry, or revive a receipt.

The host renews controller authority while the guest works. Guest EOF, heartbeat silence beyond 12 seconds, or the absolute deadline ends the native process group and its descendants. Accepted cloud operations can outlive that process. An interrupted apply becomes uncertain, invalidates earlier receipts, and needs readback and reconciliation.

## Reuse the receipt for an application

Use the Cloud Run application component with one registered infrastructure dependency. Its `project_id`, `region`, `name`, and `image_repository` options must refer to that dependency's receipt outputs. Source build hooks are refused.

```sh
export DEPLOY_OPS_ISOLATED_GRANT_FILE=/private/operator/grant.json
deploy-ops --project /work/application/deploy-ops.toml --state /private/operator/state run development --component web --only --image REGION-docker.pkg.dev/PROJECT/REPOSITORY/IMAGE@sha256:DIGEST --receipts '{"foundation":"RECEIPT_ID"}'
```

Approve and resume the application through the normal CLI or console. A later app-only release can reuse the same current receipt. Receipts remain subject to scope, expiry, supersession, revocation, and unresolved infrastructure checks.

## Verification limits

Current native references: [gcloud impersonated token lifetime](https://docs.cloud.google.com/sdk/gcloud/reference/auth/print-access-token), [Cloud Storage credential boundaries](https://docs.cloud.google.com/iam/docs/create-downscoped-short-lived-credentials), [Cloud Run scaling limits](https://docs.cloud.google.com/run/docs/configuring/max-instances-limits), and [Google provider 6.50.0 schema](https://raw.githubusercontent.com/hashicorp/terraform-provider-google/v6.50.0/website/docs/r/cloud_run_v2_service.html.markdown).
