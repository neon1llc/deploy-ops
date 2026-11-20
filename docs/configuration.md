# Configuration

The operator-owned `deploy-ops.toml` is the deployment contract, so a contributor can't quietly change it to gain cloud authority. In remote mode, its canonical model digest must match `delivery_digest` in the controller configuration.

See [the local starter](../src/deploy_ops/templates/local/deploy-ops.toml) for a complete runnable example. Print the machine-readable contract with `deploy-ops schema`.

## Objects and ownership

| Object | Meaning |
|---|---|
| `repositories` | Local checkout roots and Git revisions. Commit snapshots are the default |
| `environments` | Provider, production boundary, public variables, and permitted promotion origins |
| `components` | Infrastructure, application, or trusted workflow with a named adapter |
| `needs` | Dependency edges, not an instruction to always redeploy that repository |
| `options` | Adapter-specific settings shared across environments |
| `targets.<environment>` | Environment options, with an optional `adapter` override for that runtime |
| `build.checks` and `build.command` | Argument lists run before local preparation without inherited cloud environment variables |
| `outputs` | Allowed public scalar outputs from infrastructure or trusted workflows |
| `estimated_monthly_delta_cents` | Operator-supplied estimate, not a hard cloud spend cap |

Names use lowercase letters, digits, and hyphens. Cycles, unknown dependencies, unknown fields, escaping paths, and option-like Git revisions are rejected. Repository roots must stay under the workspace folder. Submodules, Git LFS hydration, symlinks, and source trees over 100 MB or 10,000 files aren't supported.

Only complete `${env.name}` and `${outputs.component.key}` values are expanded, with no expression language or shell expansion. Outputs must name a declared dependency. Environment variables and output records are public, so keep secrets in the provider's secret store.

## Adapter options

| Adapter | Required settings | Important boundary |
|---|---|---|
| `directory` | `name` | Writes only under local deployment state |
| `static` | `directory`, optional `destination` | Versioned files and atomic active pointer, no public hosting service created |
| `compose` | `file`, optional `project` | Existing digest-pinned images only, no build, privileged mode, or host bind mounts |
| `opentofu`, `terraform` | Exactly one of `remote_state=true` or local `local_state=true` | Actual default-workspace backend is reviewed: exact owned local path or supported s3, gcs, or azurerm. Commit provider lock file |
| `cloudformation` | `account_id`, `region`, `stack_name`, `template` | Preview creates a remote change set. Execute that exact reviewed identifier |
| `cdk` | `account_id`, `region`, `stack_name`, `assembly`, `stack_artifact` | Consume a frozen assembly. Never synthesize application code with writer credentials |
| `arm`, `bicep` | `subscription_id`, `tenant_id`, `resource_group`, `template` | Frozen local JSON, incremental deployment only. Bicep requires compiler metadata |
| `ecs` | `region`, `cluster`, `service`, `container`, `image` | Existing ECS-controller service |
| `container-apps` | `name`, `resource_group`, `subscription_id`, `image` | Existing Single revision mode app with one named container |
| `cloud-run` | `name`, `project_id`, `region`, `image` | Existing service with one 100% traffic target |
| `kubernetes` | `context`, `namespace`, `deployment`, `container`, `image` | Dedicated kubeconfig. Reviewed connection and existing Deployment UID. See [native profile](kubernetes.md) |
| `workflow` | `apply` and `verify` command lists, optional `prepare` and `outputs_file` | Local trusted scripts only, manual approval, no preview equivalence guarantee |

Native app images must use `registry/repository@sha256:...`. Tags such as `latest` are rejected. The same registry artifact can be promoted, but cross-provider replication and credentials aren't automatic.

CloudFormation and CDK also accept public `parameters`, `tags`, reviewed `capabilities`, optional `role_arn`, `profile`, and `principal_arn`, and `operation = "deploy"` or `"destroy"`. CDK file assets also need `asset_bucket` and `asset_prefix`. ARM and Bicep accept public `parameters` and a `deployment_name` prefix, and only `mode = "Incremental"` and `operation = "deploy"`. See [native infrastructure](native-infrastructure.md).

A component may set `adapter = "ecs"` by default and override it under an environment target with `adapter = "cloud-run"` or `adapter = "container-apps"`. Supply all required options for the chosen runtime. A static artifact can't be promoted into an OCI runtime. IaC options also accept public `var_files`, `variables`, and `backend_config`. Environments must not share a backend key.

## Secrets and state

- `DEPLOY_OPS_REGISTRY_CONFIG_FILE` points to an `auths`-only Docker config scoped to the intended repository. Buildx copies it into a private build home and removes it afterward. The same OS user can still read it, so this is hygiene, not a sandbox.
- `DEPLOY_OPS_KUBECONFIG_FILE` points to a dedicated scoped Kubernetes context, never a personal cluster-admin kubeconfig.
- Native cloud credentials come from the reviewed OIDC worker or an expiring [local AWS/GCP grant](local-cloud-access.md), and stay out of the workspace and browser forms.
- `.deploy-ops/` holds the database, source snapshots, prepared artifacts, and local deployments. Saved IaC plans can contain secrets, so protect state and never commit it. Remote backends need their own encryption, locking, access controls, and backups.

## Release-time images

Use `deploy-ops run <environment> --component web --only --image registry/repo@sha256:<64-hex>` when the component declares an exact `image_repository`. Other repositories, mutable tags, and replacement images during promotion are refused, and the approval binds the resolved digest. Registry provenance and image signatures still need your own verification before production use. See [the multi-cloud workspace](../examples/cloud-app/deploy-ops.toml) and [the worker recipe](../integrations/github/README.md).

Remote AWS ECS workers resolve infrastructure dependencies through controller-issued receipts. See [infrastructure receipts](infrastructure-receipts.md).

## Guided edits and review

The console's **Workspace configuration** panel edits existing entries: choose one, adjust its fields, then **Apply fields to draft**. Native adapter forms save into that environment's override, and [Cloud Run components](cloud-run.md) share these controls. Credential files and live grants stay outside the forms. Edits change the TOML draft, not the saved file, and the whole workspace is validated. Changing a provider doesn't translate existing provider options.

**Validate configuration** shows the full diff before **Save local configuration**. A save keeps the previous file, refuses outstanding releases, and rejects stale file revisions. If another editor changed the file, keep your draft before reloading. This detects observed conflicts and isn't a filesystem lock. Remote-controller consoles can validate drafts but can't save. Save locks the console, and tokens clear on reload.

The CLI edits a draft without writing the source file:

```bash
deploy-ops --project deploy-ops.toml config inspect
deploy-ops --project deploy-ops.toml config patch --section components --name web \
  --values '{"timeout_seconds":900}'
```

The JSON result has the updated `toml`, a unified `diff`, validated fields, and the contract digest. [Setup](setup.md#edit-existing-local-build-and-deployment-settings) has the pin, inherit, and reset commands. **Use adapter default** removes one optional component option, but required settings such as the directory resource name can't be removed.

A Compose component needs a `file` inside its source. Omitting `project` derives `environment-component`, and an explicit project follows Docker's [project-name rules](https://docs.docker.com/compose/how-tos/project-name/). These edits don't read Compose files, start Docker, or pull images.

```bash
deploy-ops --project deploy-ops.toml config add --section components --name container \
  --repository application --kind application --adapter compose --compose-file compose.yaml \
  --draft-out compose-draft.toml
```

Preparation renders the file with [`docker compose config`](https://docs.docker.com/reference/cli/docker/compose/config/) and checks the services against the image and host-access restrictions. Execution needs a separately reviewed release and an [explicit local Docker profile](local-docker.md). Paging has its own versioned configuration, so edits don't invalidate deployment approvals ([paging](paging.md)).

## Retained history and reviewed inputs

`deploy-ops --state PATH status` and `events RELEASE` inspect an existing private store without loading the current workspace or cloud grants. A missing path reports uninitialized state and creates nothing. Unsupported database formats and incomplete WAL copies are refused. Reading history restores no execution authority and repairs nothing.

- **Source capture.** Each release captures a repository once and gives its components copies of that snapshot. Current-files capture ignores the Git revision, refuses if files change while copying, and never commits or stashes.
- **Prepared format 2** binds local producer release IDs and output digests. A later producer claim makes older outputs ineligible until fresh successful evidence exists. Format 1 stays readable, but local dependencies without frozen lineage need fresh preparation. Unknown formats stop before claim.
- **Save and cleanup.** Save refuses pending, running, or uncertain preview cleanup. Reconcile it first. Save never executes it.
- **Local target ownership.** Directory and static adapter contract 2 binds the physical path and logical owner (project, environment, component). Different spellings and symlinks can't assign a second component to the same marker or pointer, but an application can deploy into its foundation's tree. Preparation reserves ownership before builds, history keeps it even if the release fails or is cancelled, and it never transfers implicitly. Inspect retained ownership before renaming or replacing a component. This coordinates cooperating DeployOps writers, not other filesystem changes by the same OS user.
- **Recheck and downgrade.** The path is rechecked before claim, including symlink changes after approval. Older prepared local artifacts need fresh preparation, and unknown contracts are refused. Don't downgrade a writer over newer pending work.

Generic IaC preparation uses `init -reconfigure`, which reinitializes without migrating a cached backend.

- Local state fixes its owned destination and refuses source override files and `backend_config` selectors.
- Remote state must initialize a supported remote backend. A declaration alone is insufficient.
- The review binds native metadata, public destination fields, the default workspace, and the effective configuration digest, and apply repeats the check. This isn't proof of cloud ownership, access isolation, or restorable remote state. Alternate native workspaces and backends need a separate route.
- Selected outputs are checked in the saved plan. Known missing, sensitive, non-scalar, null, fractional, or oversized values refuse before approval. Unknown values stay `unknown_until_apply` and are checked afterward by the same rules. See [migration](migration.md) for older prepared work.
