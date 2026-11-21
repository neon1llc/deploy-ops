# Setup

## Install and run

Use Python 3.11 or newer on Linux or macOS, and install as in the [README](../README.md). To upgrade, install the newer tag with `pipx install --force`. The pinned requirements aren't an audited dependency lock or an SBOM.

```bash
deploy-ops init ../my-workspace
cd ../my-workspace
deploy-ops plan development
deploy-ops run development
deploy-ops status
```

`plan` inspects the dependency graph without running repository code. `run` prepares real artifacts, which can run configured checks and build commands, and stops at required approvals. Read the preview and digest before you approve.

```bash
deploy-ops approve RELEASE_ID foundation --digest EXACT_REQUEST_DIGEST
deploy-ops resume RELEASE_ID
```

Approve the next waiting component the same way. An app-only run reuses the recorded infrastructure receipt:

```bash
deploy-ops run development --component web --only
```

## Browser console

```bash
deploy-ops ui --token-file "$HOME/.config/deploy-ops/operator.token"
```

Open the loopback address the command prints, not the packaged HTML file, and paste the token into **Connect workspace**. The server creates the file with mode `0600` if absent. The browser holds the token in memory only.

Prepare a release from **Releases**, inspect its actions, approve the exact change, then choose **Execute approved release**. Approval alone doesn't start execution.

**Controls** has searchable repository, environment, component, and native infrastructure settings. Edits stay in a draft until you review and save, and advanced TOML covers options without guided controls. Saving needs all releases finished, cancelled, or reconciled, and keeps the previous file in `.deploy-ops/previous-workspace.toml`. Remote policy can't be edited here.

**Controls → Appearance** sets a preset, density, and corner radius. Only these preferences use browser storage, under `deploy-ops.appearance.v1`.

The UI and API bind to `127.0.0.1`. Don't reverse-proxy the operator interface to the Internet, and don't serve deployed app files from the console's origin. Remote workers use the separate authority service.

## Review and save configuration from the CLI

Edit a separate draft so the saved workspace stays untouched until review. If Save is interrupted, use [Save recovery](save-recovery.md) before preparing more work.

```bash
cp deploy-ops.toml draft.toml
deploy-ops config inspect
deploy-ops config validate --file draft.toml
deploy-ops config diff --file draft.toml
deploy-ops config save --file draft.toml --source-hash SAVED_SOURCE_HASH
```

Take the hash from `source_hash` in `config inspect` or `saved_source_hash` in draft validation. `config patch --file draft.toml` returns revised TOML without writing it. A save refuses a changed saved file or an outstanding release. Console and CLI must share one operator state directory, and a running console must reload after a save.

Validate reports the saved and proposed contract digests and what Save changes for promotion, rollback, and output reuse. Comments alone don't change the contract, and approvals are unaffected. Save keeps one previous copy, so preserve that TOML before another Save replaces it. A change after the origin release needs a fresh release before promotion.

## Add a local application to an existing workspace

Register repositories and environments before their components. These operations change a draft only. Repository capture defaults to committed Git source, and new environments default to local and nonproduction.

```bash
deploy-ops config add --section repositories --name docs-app --path docs-app --draft-out draft.toml
deploy-ops config add --file draft.toml --section environments --name local-test --draft-out draft.toml
deploy-ops config add --file draft.toml --section components --name docs-base \
  --repository infrastructure --kind infrastructure --adapter directory \
  --resource-name docs-site --draft-out draft.toml
deploy-ops config add --file draft.toml --section components --name docs-site \
  --repository docs-app --kind application --adapter static --need docs-base \
  --check '["python3","check.py"]' --build-command '["python3","build.py"]' \
  --artifact-directory dist --destination '${outputs.docs-base.path}' --draft-out draft.toml
deploy-ops config validate --file draft.toml
deploy-ops config diff --file draft.toml
deploy-ops config save --file draft.toml --source-hash SAVED_SOURCE_HASH
```

`--draft-out` writes to a separate file, never the saved workspace. Without it, the result is JSON with proposed `toml`, `diff`, draft `source_hash`, and effective configuration. Duplicate names are refused. `--values` takes a structured entry for advanced fields such as environment variables, build context, and environment `targets`, but not for a field also set by a flag.

The authenticated `POST /api/config/add` takes `configuration` (draft TOML), `section`, `name`, and `values`. Controls has forms for repositories, environments, directory infrastructure, and static applications. Other adapters use advanced TOML or CLI or API input.

Authoring, validation, and save never run build or check arguments. After saving, run `deploy-ops run local-test --component docs-site`, approve the requests, run `deploy-ops resume RELEASE_ID`, and check with `deploy-ops status RELEASE_ID`.

## Edit existing local build and deployment settings

In **Controls → Local build and deployment settings**, select a directory or static component. Build arguments are ordered lists, not shell commands. **Pin override** writes a value even when it equals the inherited one, and **Use inherited value** removes the override. Applying fields updates the TOML draft only.

```bash
cp deploy-ops.toml draft.toml
deploy-ops config edit --file draft.toml --draft-out draft.toml --name web --scope build --values '{"checks":[["python3","check.py"]],"command":["python3","build.py"]}'
deploy-ops config edit --file draft.toml --draft-out draft.toml --name web --scope base --values '{"directory":"dist"}'
deploy-ops config edit --file draft.toml --draft-out draft.toml --name foundation --scope environment --environment staging --values '{"name":"staging-root"}'
deploy-ops config reset --file draft.toml --draft-out draft.toml --name foundation --scope environment --environment staging --field name
deploy-ops config inspect --file draft.toml
deploy-ops config validate --file draft.toml
deploy-ops config diff --file draft.toml
```

`local_edit_forms` in inspection reports effective values and their source. Use whole `${env.name}` and `${outputs.component.key}` references only. Validation doesn't check that a future output exists or run build code. These operations cover local build checks, the build command, DirectoryOptions, and StaticOptions, not environment-specific builds or provider options.

Native infrastructure forms work the same way, with the native form name such as `foundation/staging`:

```bash
deploy-ops config edit --file draft.toml --draft-out draft.toml --name foundation/staging --scope environment --values '{"region":"us-east-1"}'
deploy-ops config reset --file draft.toml --draft-out draft.toml --name foundation/staging --scope environment --field region
```

## Serve a selected static revision

A successful static component offers **Start serving** in its release detail. The console starts an owned loopback listener on its own OS-assigned port, and **Stop serving** closes it. Neither action builds, deploys, or changes pointers, approvals, previews, or files. From the CLI:

```bash
deploy-ops preview RELEASE_ID --component web --port 8000
```

Use `--port 0` for an OS-assigned port. A console process owns at most eight sessions and can't stop a CLI session. Keep secrets out of static artifacts, since other local processes can read loopback content, and never serve it at the console origin. See [local serving](local-serving.md).

## Cloud setup

Read [security](../SECURITY.md) first. [Authority setup](authority-setup.md) and the [cloud trust templates](../integrations/clouds) cover separate planner and executor identities. Replace every placeholder and validate the provider configuration.

`deploy-ops gate` separates IaC planning and execution jobs. The delivery worker runs native app rollouts from a built immutable image and refuses build hooks, generic commands, and IaC in a cloud-authorized job. `integrations/github/delivery-worker.yml` is a reviewed-runner recipe, not a ready workflow. You supply cloud CLIs, identities, backend state, registry credentials, and app resources.

## Docker and other systems

The [Dockerfile](../Dockerfile) and [Compose file](../compose.yaml) package the authority service only. They install no cloud CLIs and don't mount the Docker socket. Set the controller database to `/state/deploy-ops.db`. The service runs as UID 10001, so make mounted configuration readable by that user and keep the token secret.

The local console runs from the Python package. Windows is unqualified. See the [support matrix](support-matrix.md).

For app delivery on a disposable engine or cluster, copy the [Compose](../examples/local-compose/README.md) or [Kubernetes](../examples/local-kubernetes/README.md) example. Both cover first deployment, app-only reuse, approvals, and cleanup. Kubernetes also covers promotion.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Success. Read-only status commands return 0 when the record is found. |
| 1 | Failed or uncertain release. |
| 2 | Invalid or refused input. |
| 3 | The run waits for approval. CI must not treat this as a successful rollout. |

## Stop and recover local controller authority

Open **Controls → Controller stop and recovery** and refresh the controller. Inspect a request's target, changes, and exact digest before approving or rejecting it. Approval records permission only. Execution is a separate release action.

**Pause authority** revokes bounded delegations, cancels approved requests, and marks running requests uncertain. It blocks new approvals and claims and can't undo operations a provider already accepted. Inspect the actual outcomes, then record a reconciliation reason and outcome. That records your observation without replaying deployment. **Resume authority** allows fresh decisions without restoring revoked grants.

```bash
deploy-ops operator status
deploy-ops operator requests
deploy-ops operator request REQUEST_ID
deploy-ops operator pause
deploy-ops operator resolve REQUEST_ID --values '{"outcome":"failed","note":"Inspected target and confirmed the failed outcome"}'
deploy-ops operator delegations
deploy-ops operator revoke DELEGATION_ID
deploy-ops operator audit
deploy-ops operator resume
```

`operator decision REQUEST_ID --values` takes `digest`, `decision` (`approve` or `reject`), and an optional `note`. Reconciliation needs an inspection reason of 10 to 500 characters. Keep credentials out of notes. The panel and CLI cover the local controller only. Remote and isolated modes keep their own interfaces. After reconciling, use the release's recovery action if it was also interrupted.

Controller review and release detail show request and approval expiries, the budget before claim, and afterward the admitted deadline. These are what the server observed, not proof that a provider stopped. A known budget below the server's minimum hides Execute. A longer component command timeout can't extend authority, and no countdown renews approval.

## Inspect interrupted local recovery

```bash
deploy-ops recovery RELEASE_ID
deploy-ops recover RELEASE_ID --note "What was inspected after stopping the original worker"
```

Retained `status` and `events` work without an initialized workspace. `recovery` needs the current local workspace and owner.

Release detail shows the recovery reason for interrupted `running`, `cancel_requested`, and `uncertain` records and offers reconciliation only when current worker evidence supports it. A dead supervisor isn't enough while a tracked command group may still run, and unknown ownership, legacy locks, and copied or foreign-host evidence stay unavailable. Target inspection and the note are still required, because a process exit doesn't prove a provider operation stopped. Remote and isolated releases use their original owner, so another worker can't recover them. Reconciliation records an outcome without replaying deployment or manufacturing outputs.

Preview cleanup has its own check: `deploy-ops previews recovery RELEASE_ID`, or the release inspector.
