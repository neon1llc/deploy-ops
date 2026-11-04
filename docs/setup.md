# Setup

## Install and run

Use Python 3.11 or newer. Local verification used Python 3.12.10 on macOS and Python 3.14.4 on Linux.
Start with the commands in the [README](../README.md). The version-pinned requirements
record this environment. They are not an independently audited dependency lock or an SBOM.

```bash
deploy-ops init ../my-workspace
cd ../my-workspace
deploy-ops plan development
deploy-ops run development
deploy-ops status
```

`plan` inspects the dependency graph without executing repository code. `run` prepares real artifacts,
which can execute configured checks and build commands. It stops at required approvals.
Read the returned preview and request digest before approving anything.

```bash
deploy-ops approve RELEASE_ID foundation --digest EXACT_REQUEST_DIGEST
deploy-ops resume RELEASE_ID
```

Approve the next waiting component in the same way. An application-only run reuses the recorded
infrastructure output receipt instead of redeploying infrastructure:

```bash
deploy-ops run development --component web --only
```

## Browser console

```bash
deploy-ops ui --token-file "$HOME/.config/deploy-ops/operator.token"
```

Open the loopback address printed by the command, rather than the packaged HTML file. Paste the token
from that file into **Connect workspace**.
The server creates it with mode `0600` when absent. The browser holds it in memory only.
The console has separate Home, Releases, Infrastructure, Incidents and Controls views. Prepare a release
from **Releases**, inspect its proposed actions, approve the exact change, then choose **Execute approved
release**. Approval alone does not start execution. Complete evidence remains available in expandable
sections. Moving between views preserves unsaved form fields.

**Controls** includes searchable repository, environment, component and native infrastructure settings.
A search result opens its field in the relevant entry. Boolean fields use switches, known choices use
selection controls, and deployment edits remain in a draft until reviewed and saved. Advanced TOML is
available for options without guided controls. These forms do not yet cover every workspace setting.

Saving configuration requires all releases to be finished, cancelled, or reconciled. The previous file
is retained in `.deploy-ops/previous-workspace.toml`. Remote policy cannot be edited through this console.
Successful application components offer promotion and rollback in the release detail. The core lists declared
environment paths and explains missing dependency receipts. Each action reuses the artifact in a new release
with fresh authority and target checks. There is no native Windows or macOS installer.

Appearance follows the system by default. Open **Controls → Appearance** to choose a preset, density and
corner radius. These preferences save immediately in the browser.
Advanced controls import and export validated appearance JSON, including optional custom colors. Invalid
or low-contrast settings leave the current appearance and your draft intact. Changing a preset clears
custom colors while retaining the selected density and radius. Reset restores the system default.
Only appearance preferences use browser storage under `deploy-ops.appearance.v1`. Operator tokens stay in
memory. If storage is unavailable, appearance changes still work for the current session.

The UI and API bind to `127.0.0.1`. Do not reverse-proxy this local operator interface to the Internet.
Use the separate authority service for authenticated remote workers. Do not serve deployed app files
from the operator console's origin.

## Review and save configuration from the CLI

Use a separate draft file to retain your saved workspace until review. The CLI and console share validation,
conflict detection, backups and audit records.

```bash
cp deploy-ops.toml draft.toml
deploy-ops config inspect
deploy-ops config validate --file draft.toml
deploy-ops config diff --file draft.toml
deploy-ops config save --file draft.toml --source-hash SAVED_SOURCE_HASH
```

Use the `source_hash` returned by inspecting the saved workspace, or `saved_source_hash` from draft
validation. `config patch --file draft.toml` returns revised TOML without writing it. A save refuses a changed
saved file or an outstanding release. Console and CLI operations must use the same operator state directory
for coordination. After a successful save, reload any running console to use the new configuration.

## Cloud setup

Cloud access is opt-in and is not provisioned by installation. Read [security](../SECURITY.md) first.
The [authority setup](authority-setup.md) and [cloud trust templates](../integrations/clouds) cover
separate planner and executor identities. Replace every placeholder and validate the provider configuration.

The retained `deploy-ops gate` workflow separates IaC planning and execution jobs. The newer delivery
worker supports native app rollouts from an already-built immutable image. It refuses repository build
hooks, generic commands, and IaC in a cloud-authorized delivery job. These need separate workers before
safe full cross-repository cloud automation can be claimed.

`integrations/github/delivery-worker.yml` is a reviewed-runner recipe, not a ready-to-enable public workflow.
Cloud CLIs, provider identities, backend state, registry credentials, and existing app resources must be supplied.

## Docker and other systems

The [Dockerfile](../Dockerfile) and [Compose file](../compose.yaml) package the authority service only.
They do not install cloud CLIs or mount the Docker socket. Set the controller database to `/state/deploy-ops.db`.
The service runs as UID 10001. Ensure mounted configuration is readable by that user and keep the token secret.

The local console currently runs from the Python package. A thin desktop wrapper is a later packaging choice,
not a second implementation. Source and wheel journeys were checked on macOS; the test suite and container execution also passed on
Linux. Windows remains unqualified. See the [support matrix](support-matrix.md) for exact versions and limits.

For real application delivery on a disposable engine or cluster, copy the [Compose example](../examples/local-compose/README.md)
or [Kubernetes example](../examples/local-kubernetes/README.md) from the source archive. Both document
first deployment, app-only reuse, exact approvals and scoped cleanup. Kubernetes also covers promotion.
The foundation component is a local marker; cluster or engine setup is an explicit prerequisite.

## Exit codes and local preview

CLI operations return 0 on success, 1 for a failed or uncertain release, 2 for invalid or refused input,
and 3 when a run is waiting for approval. Read-only status commands return 0 when the record is found.
CI must not treat a pending deployment as a successful rollout.

Serve a successful static release on a separate loopback origin:

```bash
deploy-ops preview RELEASE_ID --component web --port 8000
```

This process stays in the foreground until stopped. It does not expose the operator console or create
a permanent hosting service.
