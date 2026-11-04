# Isolated local GCP infrastructure

DeployOps can compile one registered Cloud Run recipe and run Terraform 1.12.2 or OpenTofu 1.10.0 in a dedicated Lima VM. Planning and apply need separate exact approvals. Successful apply issues the existing typed GCP infrastructure receipt, which the application worker can reuse for app-only releases.

This route uses explicit local operator authority. Its records have no GitHub repository IDs or workflow claims. The protected GitHub OIDC runner remains available for general infrastructure source. The local recipe does not accept HCL, modules, data sources, provisioners, command hooks, provider aliases or endpoint overrides.

## Operator setup

Keep the grant, credential reference, reviewed provider lock and native tools outside all application repositories. The grant and lock must be owned by the current user with mode 0600. The controller state directory must also be private. The application worker and infrastructure module must use the same state directory.

Register an existing GCP project, one region, one Cloud Run service, an existing private GCS state bucket/prefix, an existing runtime service account and a same-project regional Artifact Registry image repository. Use immutable image digests. Google's external public sample repository is not accepted by this receipt contract; copy a reviewed image into the registered repository if needed.

The host broker uses an existing service-account credential through native gcloud. Its type, project, service-account email and fixed OAuth token endpoint must match the grant. The broker account needs permission to impersonate the exact stage accounts. The five stage/runtime accounts are distinct: infrastructure planner, infrastructure executor, application reader, application writer and runtime identity. The existing broker account is separate from all five. Give the runtime identity no project roles unless the application needs them.

Planning needs Cloud Run reads and bucket-scoped GCS object/lock permissions; GCS locking makes it more than storage read-only. Execution needs the reviewed Run create/update/read permissions and actAs on the runtime account. Application identities need no state-bucket access. Bootstrap IAM, APIs, bucket, registry and identities separately under the operator's approved scope. A short token lifetime does not narrow IAM permissions. Cloud Storage credential access boundaries do not constrain Cloud Run.

Install the package's `isolated_guest.py` as root at `/opt/deployops-tools/isolated_guest.py`. The host checks its exact package hash before sending a token. Pin native executables at `/opt/deployops-tools/terraform` and `/opt/deployops-tools/tofu`; root-owned links to root-owned version directories are accepted. Install the reviewed Google 6.50.0 package as a root-owned filesystem mirror at:

```text
/opt/deployops-tools/providers/registry.opentofu.org/hashicorp/google/6.50.0/linux_amd64/
```

The executable there is `terraform-provider-google`; retain the package's other files and license. Register its SHA256 and the chosen native executable's SHA256 in the grant. Native init checks the reviewed lock with a filesystem mirror and has no direct provider download fallback. This first route supports Linux amd64 guests.

The VM must be running with no host mounts, SSH agent or X11 forwarding, propagated proxy environment, or application port forwards. Register the fingerprint of the effective `config` object from `limactl list NAME --json`, using DeployOps' canonical `fingerprint` function. Runtime changes require a new grant. The helper independently rejects host-sharing mounts and untrusted executable ownership. Native gcloud and limactl paths must be operator/root owned, not writable by untrusted users; macOS administrative Homebrew directories remain within the trusted host boundary.

The root supervisor is trusted. It holds a kernel lock for one operation at a time, reserves a fresh unused numeric UID/GID, and runs the native tools with no supplementary groups. It creates no persistent user account. Earlier build processes cannot inspect that identity's environment or private working directory. Source, provider lock and approved apply plan are root-owned and immutable to the worker. The worker can write only its private native data/home directory. The temporary token crosses stdin and process environment, never command arguments, persistent keys or public logs. Guest root can still inspect it; this is VM isolation from contributor source, not protection against a compromised guest administrator or kernel.

## Grant structure

The shared schema is `deploy_ops.isolated_recipe.IsolatedGrant`. Unknown fields are rejected. An operator-owned JSON grant has these fields:

- `schema_version: 1`, a short `name`, `environment`, Unix `expires_at` and an explicit `authorization` note.
- `engine`: `terraform` or `opentofu`.
- `recipe`: `project`, `region`, `name`, `image_repository`, `image_digest`, `runtime_service_account`, and `backend` with `bucket` and `prefix`.
- `planner_service_account`, `executor_service_account`, `broker_service_account`, and an absolute `credentials_file` reference.
- Absolute `gcloud`, `limactl` and `lock_file` paths; `lock_digest`, `tool_digest`, `provider_digest`; `vm` and `vm_config_digest`.
- For application integration, all five fields: `workspace_digest`, `app_component`, `app_dependency`, `app_planner_service_account` and `app_executor_service_account`.

The fixed recipe enforces IAM checks, ingress ALL for authenticated host probes, CPU 1, memory 256 MiB, request-based CPU allocation, no startup CPU boost, minimum zero, revision maximum one, concurrency one and a 30-second request timeout. It creates no IAM policy or unauthenticated binding. Revision maximum one is not a billing cap. The pinned Google provider does not accept service-level `max_instance_count`. Deletion protection is false for this explicitly disposable recipe; accepted destruction and cleanup remain separately authorized.

Review the full generated public module and tool/lock digests printed by the first planning request. That packet is part of the exact approval digest, not an unbound explanatory label. Every subsequent stage checks the current grant, lock and packet identity.

## Execute the two approved stages

The main CLI, authenticated loopback console and standalone module use the same core methods. Replace the paths and IDs with reviewed operator values:

```sh
deploy-ops --state /private/operator/state isolated --grant /private/operator/grant.json prepare
deploy-ops --state /private/operator/state isolated --grant /private/operator/grant.json approve PLAN_REQUEST --digest EXACT_PLAN_REQUEST_DIGEST
deploy-ops --state /private/operator/state isolated --grant /private/operator/grant.json execute PLAN_REQUEST
deploy-ops --state /private/operator/state isolated --grant /private/operator/grant.json approve APPLY_REQUEST --digest EXACT_APPLY_REQUEST_DIGEST
deploy-ops --state /private/operator/state isolated --grant /private/operator/grant.json execute APPLY_REQUEST
```

The first execution returns a new pending request for the actual saved native plan. Its request references the completed planning lease. The second execution verifies the retained saved-plan bytes before claiming writer authority. The broker then mints a separate impersonated token, limited to 900 seconds. Grant expiry or a shorter controller deadline wins.

An expired but unchanged operator registration still supports status reads and recording an inspected failure. It cannot prepare, approve, mint tokens or execute new work. To close a running/uncertain record after actual inspection, use `deploy-ops --state /private/operator/state isolated --grant /private/operator/grant.json reconcile REQUEST --digest EXACT_DIGEST --note "Specific inspection findings"`, or the matching explicit console action. Reconciliation records failure; it does not stop a remote operation, remove resources, retry execution or revive a receipt.

The host renews controller authority while the guest works. Guest EOF, heartbeat silence beyond 12 seconds or the absolute deadline terminates the native process group, including surviving provider descendants. Accepted cloud operations can outlive that process. An interrupted apply becomes uncertain, invalidates earlier receipts and requires readback/reconciliation. Retrying is not a cleanup strategy.

## Reuse the receipt for an application

Use the standard Cloud Run application component with one registered infrastructure dependency. Its `project_id`, `region`, `name` and `image_repository` options must refer to that dependency's receipt outputs. Source build hooks are refused.

```sh
export DEPLOY_OPS_ISOLATED_GRANT_FILE=/private/operator/grant.json
deploy-ops --project /work/application/deploy-ops.toml --state /private/operator/state run development --component web --only --image REGION-docker.pkg.dev/PROJECT/REPOSITORY/IMAGE@sha256:DIGEST --receipts '{"foundation":"RECEIPT_ID"}'
```

Approve and resume the application through the existing CLI/console lifecycle. A later app-only release can use the same still-current receipt; infrastructure need not run again. Receipts remain subject to scope, expiry, supersession, revocation and unresolved infrastructure checks.

The shared CLI and authenticated console API expose public recipe/status and separate prepare, exact approve and asynchronous execute operations. The console never accepts browser-supplied credential references or arbitrary recipes. Application releases use the same Engine and receipt gate. There is no native destroy/empty-state completion workflow yet. Use separately authorized, exact operator cleanup and report it as such.

## Verification limits

Credentialless tests exercise both native-engine contracts, separate approvals, narrow identity selection, changed grants/locks/plans, receipt invalidation and two application-only deliveries with synthetic provider results. Native offline VM checks validate the generated modules with the pinned provider mirror, worker write restrictions, host transport readiness, supervisor exclusion, EOF/deadline/heartbeat shutdown and descendant termination. These checks do not establish cloud IAM correctness, live plan/apply success, application health or actual cost.

Current native references: [gcloud impersonated token lifetime](https://docs.cloud.google.com/sdk/gcloud/reference/auth/print-access-token), [Cloud Storage credential boundaries](https://docs.cloud.google.com/iam/docs/create-downscoped-short-lived-credentials), [Cloud Run scaling limits](https://docs.cloud.google.com/run/docs/configuring/max-instances-limits), and [Google provider 6.50.0 schema](https://raw.githubusercontent.com/hashicorp/terraform-provider-google/v6.50.0/website/docs/r/cloud_run_v2_service.html.markdown).
