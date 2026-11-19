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
| `opentofu`, `terraform` | Exactly one of `remote_state=true` or local `local_state=true` | Actual default-workspace backend is reviewed: exact owned local path or supported s3/gcs/azurerm. Commit provider lock file |
| `cloudformation` | `account_id`, `region`, `stack_name`, `template` | Preview creates a remote change set; execute that exact reviewed identifier |
| `cdk` | `account_id`, `region`, `stack_name`, `assembly`, `stack_artifact` | Consume a frozen assembly; never synthesize application code with writer credentials |
| `arm`, `bicep` | `subscription_id`, `tenant_id`, `resource_group`, `template` | Frozen local JSON, incremental deployment only; Bicep requires compiler metadata |
| `ecs` | `region`, `cluster`, `service`, `container`, `image` | Existing ECS-controller service |
| `container-apps` | `name`, `resource_group`, `subscription_id`, `image` | Existing Single revision mode app with one named container |
| `cloud-run` | `name`, `project_id`, `region`, `image` | Existing service with one 100% traffic target |
| `kubernetes` | `context`, `namespace`, `deployment`, `container`, `image` | Dedicated kubeconfig; reviewed connection and existing Deployment UID; [native profile](kubernetes.md) |
| `workflow` | `apply` and `verify` command lists, optional `prepare` and `outputs_file` | Local trusted scripts only, manual approval, no preview equivalence guarantee |

Native app images must use `registry/repository@sha256:...`. Tags such as `latest` are rejected. The same registry artifact can be promoted, but cross-provider replication and credentials aren't automatic.

CloudFormation and CDK also accept public `parameters`, `tags`, reviewed `capabilities`, optional `role_arn`, `profile`, and `principal_arn`, and `operation = "deploy"` or `"destroy"`. CDK file assets also need `asset_bucket` and `asset_prefix`. ARM and Bicep accept public `parameters` and a `deployment_name` prefix, and only `mode = "Incremental"` and `operation = "deploy"`. See [native infrastructure](native-infrastructure.md).

## Separate secrets and state

Use `DEPLOY_OPS_REGISTRY_CONFIG_FILE` for an `auths`-only Docker config scoped to the intended repository. Buildx copies it into a private build home and removes it after the build, and credential helpers aren't imported. The same OS user can still read it, so this is credential hygiene, not a sandbox.

Use `DEPLOY_OPS_KUBECONFIG_FILE` for a dedicated scoped Kubernetes context, never a personal cluster-admin kubeconfig. Native cloud credentials come from the reviewed OIDC worker or an expiring [local AWS/GCP grant](local-cloud-access.md). Credential references stay out of the workspace and browser forms.

`.deploy-ops/` holds the database, source snapshots, prepared artifacts, and local deployments. Saved IaC plans can contain secrets, so protect state and never commit or publish it. Remote backends need their own encryption, locking, access controls, and backups.

## Environment-specific runtime and state

A component may set `adapter = "ecs"` by default and override it under an environment target with `adapter = "cloud-run"` or `adapter = "container-apps"`. Supply all required options for the chosen runtime. A static artifact can't be promoted into an OCI runtime. IaC options also accept public `var_files`, `variables`, and `backend_config`. Environments must not share a backend key.

## Release-time images

Use `deploy-ops run <environment> --component web --only --image registry/repo@sha256:<64-hex>` when the component declares an exact `image_repository`. Other repositories, mutable tags, and replacement images during promotion are refused, and the approval binds the resolved digest. Registry provenance and image signatures still need your own verification before production use. See [the multi-cloud workspace](../examples/cloud-app/deploy-ops.toml) and [the worker recipe](../integrations/github/README.md).

## Isolated infrastructure outputs

Remote AWS ECS workers resolve infrastructure dependencies through controller-issued receipts. See [infrastructure receipts](infrastructure-receipts.md).

## Guided edits and review

The console's **Workspace configuration** panel edits existing repositories, environments, and components. Choose the entry, adjust its fields, then **Apply fields to draft**. Native infrastructure forms save changes into that environment's override. These change the TOML draft, not the saved file. New entries and advanced settings use TOML. The whole workspace is validated, and changing a provider doesn't translate existing provider options.

**Validate configuration** shows the complete diff before **Save local configuration**. A save keeps the previous file in local state, refuses outstanding releases, and rejects stale file revisions. If another tab or editor changed the file, keep your draft before reloading. This detects observed conflicts and isn't a filesystem lock. Remote-controller consoles can prepare and validate drafts but can't save controller or workspace authority.

The CLI edits a draft without writing the source file:

```bash
deploy-ops --project deploy-ops.toml config inspect
deploy-ops --project deploy-ops.toml config patch --section components --name web \
  --values '{"timeout_seconds":900}'
```

The JSON result has the updated `toml`, a unified `diff`, validated fields, and the contract digest. Save the draft through the console or your editor. Check configuration output for repository paths and options before publishing it.

For directory/static/Compose components, **Local build and deployment settings** distinguishes component options from
environment overrides. **Use adapter default** removes one optional component option; required settings
such as the directory resource name cannot be removed. **Use inherited** removes one environment override.
An explicit override remains pinned even when it equals the inherited value, and survives later component
changes. Activation choices use a native dropdown. Both reset actions update only the draft, preserving
other fields and comments until you explicitly save.

The CLI uses these same scopes and returns a separate reviewable draft:

```bash
deploy-ops --project deploy-ops.toml config reset --name web --scope base \
  --field activation --draft-out defaults-draft.toml
deploy-ops --project deploy-ops.toml config reset --name web --scope environment \
  --environment staging --field activation --draft-out inherited-draft.toml
```

Compose file and project settings use the same creation, component-default and environment-override
controls. The file is required and must stay inside the component source. Omitting the project derives
`environment-component`; an explicit project follows Docker's lowercase/digit/dash/underscore naming
rules. An equal environment pin remains explicit when component defaults change. These pure edits do
not read Compose files, start Docker, render configuration or pull images.

```bash
deploy-ops --project deploy-ops.toml config add --section components --name container \
  --repository application --kind application --adapter compose --compose-file compose.yaml \
  --draft-out compose-draft.toml
```

Release preparation still delegates rendering to native `docker compose config` and checks the rendered
services for the existing image and host-access restrictions. Container execution requires a separately
reviewed release and an [explicit local Docker profile](local-docker.md) selecting suitable native tools,
an owned Unix endpoint and its daemon identity. See Docker's [project-name rules](https://docs.docker.com/compose/how-tos/project-name/)
and [native configuration rendering](https://docs.docker.com/reference/cli/docker/compose/config/).

Paging uses a separate versioned configuration, so edits don't invalidate deployment approvals. Use `deploy-ops paging` or the console. See [paging](paging.md).

## Retained history and reviewed inputs

`deploy-ops --state PATH status` and `events RELEASE` inspect an existing private store without loading
current workspace or cloud grants. A missing path reports uninitialized state and creates nothing.
Unsupported database formats and incomplete WAL copies are refused. Reading history does not restore
execution authority or repair a store.

Each release captures a repository once, then gives its components copies of that same snapshot.
Committed capture uses the selected Git revision. Current-files capture ignores that revision and refuses
if included files change while copying. It never commits or stashes the original workspace.

New prepared format 2 binds local producer release IDs and output digests. A later producer claim makes
older outputs ineligible until fresh successful evidence exists. Consumer preparation and claim recheck
the exact frozen inputs. Legacy format 1 remains readable, but local dependencies without frozen lineage
require fresh preparation. Unknown formats stop before claim. Historical approval bytes remain unchanged.

Configuration Save refuses pending, running or uncertain preview cleanup, including cleanup on a
successful deployment. Inspect and reconcile that retirement obligation first. Save never executes it.

Local directory/static adapter contract 2 binds the canonical physical mutation path and logical owner
(project, environment and component). Different spellings and directory symlinks cannot assign a second
component to the same directory marker or static activation pointer. These two markers remain separate,
so an application can deploy into its foundation's tree. Preparation reserves ownership before builds;
history retains that reservation even if the release later fails or is cancelled. There is no implicit
ownership transfer. Nested independent state roots cannot share a target. Inspect retained ownership
before renaming or replacing a component. This boundary coordinates cooperating local DeployOps writers;
it does not protect against arbitrary filesystem changes by the same operating-system user.

The bound path is rechecked before claim, including symlink changes after approval. Older prepared local
artifacts remain readable, but require fresh preparation before execution under this runtime. Their
approved bytes are not rewritten. Unknown adapter contracts are refused. Use the matching runtime for
historical evidence; do not downgrade a writer over newer pending work.

Generic IaC preparation uses `init -reconfigure`, which reinitializes without migrating a cached backend.
Local state fixes its owned destination and refuses source override files or `backend_config` selectors.
Remote state must actually initialize a supported remote backend; a declaration alone is insufficient.
The prepared review binds native metadata, public destination fields, default workspace and a digest of
the complete effective configuration. Apply repeats initialization and checks the same binding before
native apply. This is metadata binding, not proof of cloud ownership, backend access isolation or
restorable remote state. Alternate native workspaces/backends require a separately supported route.

Selected generic IaC outputs are checked in the saved plan. Known missing, sensitive, non-scalar, null,
fractional-number or oversized values refuse before approval/apply. Unknown values stay explicitly
`unknown_until_apply` and are checked afterward using the same small public scalar rules. Preparation
does not claim those outputs already exist. The release detail exposes effective backend and output
readiness evidence behind native disclosures. See [migration](migration.md) for older prepared work.
