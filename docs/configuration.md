# Configuration

The operator-owned `deploy-ops.toml` is the deployment contract. It is not a file that a contributor may
silently change to obtain additional cloud authority. In remote mode, its canonical model digest must
match `delivery_digest` in the separate controller configuration.

The complete runnable example lives in [the local starter](../src/deploy_ops/templates/local/deploy-ops.toml).
Print the machine-readable contract with `deploy-ops schema`.

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

Names use lowercase letters, digits, and hyphens. Cycles, unknown dependencies, unknown fields, escaping
component paths, and option-like Git revisions are rejected. Repository roots must stay under the workspace
folder. Check out separate Git repositories into that folder, or use the explicit working-tree mode locally.
Submodules, Git LFS hydration, symlinks, and source trees over 100 MB or 10,000 files are not supported.

Only complete `${env.name}` and `${outputs.component.key}` values are expanded. There is no expression
language, shell expansion, or arbitrary template evaluation. Outputs must name a declared dependency.
Environment variables and output records are public configuration. Keep secrets in the provider's secret
store and pass references, not plaintext values.

## Adapter options

| Adapter | Required settings | Important boundary |
|---|---|---|
| `directory` | `name` | Writes only under local deployment state |
| `static` | `directory`, optional `destination` | Versioned files and atomic active pointer, no public hosting service created |
| `compose` | `file`, optional `project` | Existing digest-pinned images only, no build, privileged mode, or host bind mounts |
| `opentofu`, `terraform` | `remote_state=true`, or local `local_state=true` | Repo must define the remote backend. Commit provider lock file |
| `cloudformation` | `account_id`, `region`, `stack_name`, `template` | Preview creates a remote change set; execute that exact reviewed identifier |
| `cdk` | `account_id`, `region`, `stack_name`, `assembly`, `stack_artifact` | Consume a frozen assembly; never synthesize application code with writer credentials |
| `arm`, `bicep` | `subscription_id`, `tenant_id`, `resource_group`, `template` | Frozen local JSON, incremental deployment only; Bicep requires compiler metadata |
| `ecs` | `region`, `cluster`, `service`, `container`, `image` | Existing ECS-controller service |
| `container-apps` | `name`, `resource_group`, `subscription_id`, `image` | Existing Single revision mode app |
| `cloud-run` | `name`, `project_id`, `region`, `image` | Existing service with one 100% traffic target |
| `kubernetes` | `context`, `namespace`, `deployment`, `container`, `image` | Dedicated kubeconfig and existing Deployment |
| `workflow` | `apply` and `verify` command lists, optional `prepare` and `outputs_file` | Local trusted scripts only, manual approval, no preview equivalence guarantee |

Native app images must use `registry/repository@sha256:...`. A tag such as `latest` is rejected.
The same registry artifact can be promoted, but cross-provider registry replication and credentials are
not automatic. Each environment must be able to pull that artifact.

Native IaC settings have one typed owner shared by files, CLI and guided forms. CloudFormation and CDK
also accept public `parameters`, `tags`, explicitly reviewed `capabilities`, an optional `role_arn`,
`profile` and `principal_arn`, and `operation = "deploy"` or `"destroy"`. CDK file assets additionally
require an explicit `asset_bucket` and slash-terminated `asset_prefix`. These settings do not create a
bootstrap stack or grant permission to publish assets.

ARM and Bicep accept public `parameters` and a `deployment_name` prefix. The adapter appends a release-specific
suffix. Only `mode = "Incremental"` and `operation = "deploy"` are accepted. See
[native infrastructure](native-infrastructure.md) for resource, asset and cleanup restrictions.

## Separate secrets and state

Use `DEPLOY_OPS_REGISTRY_CONFIG_FILE` for an `auths`-only Docker config scoped to the intended repository.
Buildx copies it into a private build home and removes the copy after the build. Credential helpers and the
human's Docker configuration are not imported. The same OS user can still inspect these files. This is
credential hygiene, not a sandbox.

Use `DEPLOY_OPS_KUBECONFIG_FILE` for a dedicated scoped Kubernetes context. Do not pass a personal
cluster-admin kubeconfig. Native cloud credentials are obtained by the reviewed OIDC worker or an
explicit, expiring [local AWS/GCP grant](local-cloud-access.md). Credential references remain outside
the workspace and browser forms. Neither route grants authority to run repository code with writer credentials.

`.deploy-ops/` contains the database, private source snapshots, prepared artifacts, temporary homes, and
local deployments. Saved IaC plans can contain secrets. Back up and protect state. Do not commit or publish it.
A local IaC backend is persisted under the environment's deployment directory. Remote backends must provide
their own encryption, locking, access controls, and backup policy.

## Environment-specific runtime and state

A component may set `adapter = "ecs"` by default and override it under an environment target with
`adapter = "cloud-run"` or `adapter = "container-apps"`. Supply all required options for the chosen runtime.
The shared artifact remains an OCI image. A static artifact cannot be promoted into an OCI runtime.

IaC options also accept `var_files`, `variables`, and `backend_config`. These are public configuration.
Paths stay within the repository. Backend selection is reused during planning and apply. Distinct environments
must not accidentally point at the same backend key. Native engine isolation and remote state permissions
remain operator responsibilities.

## Release-time images

Use `deploy-ops run <environment> --component web --only --image registry/repo@sha256:<64-hex>`
when the component declares an exact `image_repository`. This is a release artifact, not an arbitrary
configuration override. Other repositories, mutable tags, and replacement images during promotion are
refused. The prepared envelope and approval bind to the resolved digest. Registry provenance and image
signatures still need an independent verification policy before production use.

See [the multi-cloud application workspace](../examples/cloud-app/deploy-ops.toml) and
[the trusted worker recipe](../integrations/github/README.md).

## Isolated infrastructure outputs

Remote AWS ECS workers can resolve registered infrastructure dependencies through controller-issued receipts.
The producer registers a typed S3 backend and public output schema; the consumer maps dependency names to
approved producer targets. See [infrastructure receipts](infrastructure-receipts.md) for the configuration
examples, workflow inputs, validity checks and recovery behavior. Existing local output bindings are unchanged.

## Guided edits and review

The console's **Workspace configuration** panel edits existing repositories, environments and components.
Choose the entry, adjust its fields, then **Apply fields to draft**. This preserves other form drafts,
comments, build settings, provider options and environment overrides. Native infrastructure option forms
show the effective component/environment settings and save deliberate changes into that environment's
override. They change the visible TOML draft, not the saved file. Advanced settings and new entries remain
available through TOML.

Changing a provider or tool does not translate existing provider options. Review those options and any
per-environment adapter overrides before deployment. The whole workspace is validated, including references,
dependency cycles and path restrictions. Form choices and defaults come from the shared workspace schema.

Use **Validate configuration** to inspect the complete diff before **Save local configuration**. A save
retains the previous file in local state, refuses outstanding releases and rejects stale file revisions.
If another tab or editor changed the saved file, preserve your draft before reloading and reconciling it.
This detects observed conflicts; it is not a filesystem lock against unrelated applications.
Remote-controller consoles can prepare and validate drafts but cannot save controller or workspace authority.

The CLI uses the same draft editor, without writing the source file:

```bash
deploy-ops --project deploy-ops.toml config inspect
deploy-ops --project deploy-ops.toml config patch --section components --name web \
  --values '{"timeout_seconds":900}'
```

The JSON result contains the updated `toml`, a unified `diff`, validated fields and the contract digest.
Review and save the desired draft through the console or your normal editor. Configuration output can
contain repository paths and options; do not publish it without checking its contents.

Paging destinations and routing use a separate versioned configuration so edits do not invalidate
deployment approvals. Use the guided console or `deploy-ops paging` commands; see [paging](paging.md).
