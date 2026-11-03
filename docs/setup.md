# Setup

## Install and run

Use Python 3.11 or newer. The latest local verification used Python 3.12.10 on macOS.
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

Open the loopback address printed by the command. Paste the token from that file into the unlock form.
The server creates it with mode `0600` when absent. The browser holds it in memory only.
The console can edit and validate TOML, save local configuration, inspect plans, prepare releases,
approve exact changes, grant bounded local windows, cancel work, and inspect events.

Saving configuration requires all releases to be finished, cancelled, or reconciled. The previous file
is retained in `.deploy-ops/previous-workspace.toml`. Remote policy cannot be edited through this console.
Promotion and rollback are CLI operations in this preview. There is no native Windows or macOS installer.

The UI and API bind to `127.0.0.1`. Do not reverse-proxy this local operator interface to the Internet.
Use the separate authority service for authenticated remote workers. Do not serve deployed app files
from the operator console's origin.

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
not a second implementation. The local source and wheel journeys were checked on macOS. Windows and the container runtime still need platform verification.

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
