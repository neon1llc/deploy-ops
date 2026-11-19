# Setup

## Install and run

Use Python 3.11 or newer. Verification used Python 3.12.10 on macOS and 3.14.4 on Linux. Start with the commands in the [README](../README.md). The pinned requirements aren't an audited dependency lock or an SBOM.

```bash
deploy-ops init ../my-workspace
cd ../my-workspace
deploy-ops plan development
deploy-ops run development
deploy-ops status
```

`plan` inspects the dependency graph without running repository code. `run` prepares real artifacts, which can run configured checks and build commands, and stops at required approvals. Read the preview and digest before approving.

```bash
deploy-ops approve RELEASE_ID foundation --digest EXACT_REQUEST_DIGEST
deploy-ops resume RELEASE_ID
```

Approve the next waiting component the same way. An application-only run reuses the recorded infrastructure output receipt:

```bash
deploy-ops run development --component web --only
```

## Browser console

```bash
deploy-ops ui --token-file "$HOME/.config/deploy-ops/operator.token"
```

Open the loopback address the command prints, not the packaged HTML file. Paste the token from that file into **Connect workspace**. The server creates the file with mode `0600` if absent, and the browser holds the token in memory only.

Prepare a release from **Releases**, inspect its actions, approve the exact change, then choose **Execute approved release**. Approval alone doesn't start execution.

**Controls** has searchable repository, environment, component, and native infrastructure settings. Deployment edits stay in a draft until reviewed and saved. Advanced TOML covers options without guided controls. The forms don't cover every workspace setting.

Saving configuration requires all releases to be finished, cancelled, or reconciled. The previous file is kept in `.deploy-ops/previous-workspace.toml`. Remote policy can't be edited here.

**Controls → Appearance** sets a preset, density, and corner radius. Only these preferences use browser storage, under `deploy-ops.appearance.v1`.

The UI and API bind to `127.0.0.1`. Don't reverse-proxy this operator interface to the Internet. Use the separate authority service for authenticated remote workers. Don't serve deployed app files from the console's origin.

## Review and save configuration from the CLI

Use a separate draft file so your saved workspace stays untouched until review.
If Save is interrupted, use [Save recovery](save-recovery.md) before preparing more work.

```bash
cp deploy-ops.toml draft.toml
deploy-ops config inspect
deploy-ops config validate --file draft.toml
deploy-ops config diff --file draft.toml
deploy-ops config save --file draft.toml --source-hash SAVED_SOURCE_HASH
```

Use the `source_hash` from `config inspect`, or `saved_source_hash` from draft validation. `config patch --file draft.toml` returns revised TOML without writing it. A save refuses a changed saved file or an outstanding release. Console and CLI must share one operator state directory. After a save, reload any running console.

## Add a local application to an existing workspace

Register sources and environments before their components. These operations change a draft only. Repository capture defaults to committed Git source, and new environments default to local and nonproduction.

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

`--draft-out` writes successful add or patch results to a separate file, never the saved workspace. Without it, the result is JSON with proposed `toml`, `diff`, draft `source_hash`, and effective configuration. Duplicate names are refused. `--values` accepts a structured entry for advanced fields such as environment variables, build context, and environment `targets`. Don't set one field through both a flag and `--values`.

The authenticated `POST /api/config/add` takes `configuration` (draft TOML), `section`, `name`, and `values`. Controls has compact forms for repositories, environments, directory infrastructure, and static applications. Other adapters use advanced TOML or structured CLI/API input.

Authoring, validation, and save never run build or check arguments. After saving, run `deploy-ops run local-test --component docs-site`, approve the requests, then `deploy-ops resume RELEASE_ID`. Check with `deploy-ops status RELEASE_ID`.

## Cloud setup

Read [security](../SECURITY.md) before setting up cloud access. The [authority setup](authority-setup.md) and [cloud trust templates](../integrations/clouds) cover separate planner and executor identities. Replace every placeholder and validate the provider configuration.

The `deploy-ops gate` workflow separates IaC planning and execution jobs. The delivery worker supports native app rollouts from a built immutable image and refuses build hooks, generic commands, and IaC in a cloud-authorized job.

`integrations/github/delivery-worker.yml` is a reviewed-runner recipe, not a ready-to-enable workflow. You supply cloud CLIs, provider identities, backend state, registry credentials, and existing app resources.

## Docker and other systems

The [Dockerfile](../Dockerfile) and [Compose file](../compose.yaml) package the authority service only. They don't install cloud CLIs or mount the Docker socket. Set the controller database to `/state/deploy-ops.db`. The service runs as UID 10001, so make mounted configuration readable by that user and keep the token secret.

The local console runs from the Python package. Source and wheel journeys were checked on macOS, and tests and container execution passed on Linux. Windows is unqualified. See the [support matrix](support-matrix.md).

For app delivery on a disposable engine or cluster, copy the [Compose](../examples/local-compose/README.md) or [Kubernetes](../examples/local-kubernetes/README.md) example from the source archive. Both cover first deployment, app-only reuse, approvals, and cleanup, and Kubernetes also covers promotion.

## Exit codes

The CLI returns 0 on success, 1 for a failed or uncertain release, 2 for invalid or refused input, and 3 when a run is waiting for approval. Read-only status commands return 0 when the record is found. CI must not treat a pending deployment as a successful rollout.

## Stop and recover local controller authority

Open **Controls → Controller stop and recovery** and refresh the controller. Inspect a request's target, changes, and exact digest before approving or rejecting it. Approval records permission only, and execution is a separate release action.

**Pause authority** revokes bounded delegations, cancels approved requests, and marks running requests uncertain. It blocks new approvals and claims and can't undo operations a provider already accepted. Inspect actual outcomes, then record a reconciliation reason and outcome, which records the observation without replaying deployment. **Resume authority** allows fresh decisions without restoring revoked grants.

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

Exact decisions use `operator decision REQUEST_ID --values` with `digest`, `decision` (`approve` or `reject`), and an optional `note`. Reconciliation needs an inspection reason of 10 to 500 characters. Keep credentials out of notes. The panel and CLI cover the local controller only. Remote and isolated modes keep their own interfaces. After reconciling, use the release's recovery action if it was also interrupted.

## Edit existing local build and deployment settings

In **Controls → Local build and deployment settings**, select an existing directory or static component. Build arguments are ordered lists, not shell commands. Each option shows its effective value and source. **Pin override** authors a value even when it equals the inherited one, and **Use inherited value** removes the override. Applying fields updates the TOML draft only.

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

`local_edit_forms` in inspection reports effective values and their source. Use whole `${env.name}` and `${outputs.component.key}` references only. Draft validation doesn't check that a future output exists or run build code.

Save with `config save --file draft.toml --source-hash SAVED_SOURCE_HASH` or the console. A configuration change after the origin release needs a fresh release before promotion. These operations cover local build checks and command plus DirectoryOptions and StaticOptions, not environment-specific builds or arbitrary provider options.

## Serve a selected static revision

A successful static component offers **Start serving** in its release detail. The console starts an owned listener on a separate OS-assigned loopback port. Open the session's URL explicitly. **Stop serving** closes that session only. Neither action builds, deploys, changes active pointers or approvals, retires previews, or deletes files. Session status describes the listener and preview lifetime, not activation or application health.

```bash
deploy-ops preview RELEASE_ID --component web --port 8000
```

`--port 0` picks an OS-assigned port. Console shutdown closes its sessions, and the console can't stop a CLI session. At most eight sessions belong to a console process. Served content is read-only, bounded files from the retained reviewed revision. Keep secrets out of static artifacts, since other local processes can read loopback content and this isn't a sandbox. Serve it on its own origin, never through a proxy or iframe at the console origin.

Native infrastructure forms also show each field's source. **Pin … for environment** explicitly authors
its current value, including an equal inherited value. **Use inherited …** removes only that field's
environment override. Unapplied sibling field edits remain in the form. The CLI uses the selected native
form name, for example `foundation/staging`, with the same environment edit/reset operations:

```bash
deploy-ops config edit --file draft.toml --draft-out draft.toml --name foundation/staging --scope environment --values '{"region":"us-east-1"}'
deploy-ops config reset --file draft.toml --draft-out draft.toml --name foundation/staging --scope environment --field region
```

Loading forms from revised TOML refreshes untouched local fields. If both the TOML and a retained field
edit changed the same value, the local panel shows both versions and requires **Keep my …** or
**Use current …** before applying. Ordered build arguments remain ordered. Field drafts stay in the tab's
memory; reloading or locking the tab clears them.

Validate reports the saved and proposed contract digests and explains the effect of Save on promotion,
rollback, dependency output reuse, provider inspection and preview retirement. Comments alone do not
change the contract. Exact historical gates remain in force. Save retains one previous workspace copy
in local state; preserve that exact TOML separately before another Save replaces it. Returning to an old
contract uses the normal validate, review and hash-guarded Save flow, without altering approvals.

Controller review and release detail show the observed request/approval expiries, provisional budget
before claim, configured maximum execution window and the actual admitted execution deadline afterward.
The contact timestamp and its age describe the server observation, not proof that a provider stopped.
A known budget below the server's minimum hides Execute; the controller still rechecks every claim and
heartbeat. Old controllers without this projection report timing unavailable. A component's longer
command timeout cannot extend authority, and no countdown renews approval or retries an operation.

## Inspect interrupted local recovery

Use `deploy-ops recovery RELEASE_ID` to inspect current recovery availability before
`deploy-ops recover RELEASE_ID --note "What was inspected after stopping the original worker"`.
Retained `status` and `events` still work without current workspace initialization; the operational
`recovery` check needs the current local workspace and owner.

Release detail shows the same recovery reason for interrupted `running`, `cancel_requested` and
`uncertain` records. It offers reconciliation only when supported current worker evidence permits it.
The backend checks again when acting. Local worker admission and stop facts commit with their locks
in the existing journal. A dead supervisor is insufficient while a tracked command group may still run.
Unknown command ownership, legacy locks, and copied or foreign-host worker evidence remain unavailable.
No process is killed or lock cleared by reading this view. Target inspection and the bounded note are
still required; local process exit does not prove that an accepted provider operation stopped.

Remote and isolated releases use their original operation owner. An old interrupted release cannot be
made recoverable by starting another worker. Reconciliation records an inspected outcome without
replaying deployment, restoring data or manufacturing verified outputs. If the controller completed
before its worker died, that terminal outcome remains intact and missing outputs stay unverified.

Preview cleanup exposes its separate check with `deploy-ops previews recovery RELEASE_ID` and in the
release inspector. A completed provider readback cannot certify an older deployment or cleanup worker.
New local claims bind their exact worker admission to the controller execution. If that correspondence
is absent, a later local attempt cannot manufacture stopped ownership for the original execution.
