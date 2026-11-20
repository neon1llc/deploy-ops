# Isolated static previews

A static preview is a new, owned deployment namespace with an owner label and an expiry. It uses the normal snapshots, exact approvals, and static adapter. Cleanup is a separate reviewed operation. Existing deployment paths are never adopted, and expiry never deletes files.

## Declare and deploy

Enable previews only on a local, nonproduction environment:

```toml
[environments.review]
provider = "local"
preview = true
```

Use one static application with at most one directory foundation, and include its full dependency graph. Static destinations must use the isolated default or a path output from that preview's own dependency.

```bash
deploy-ops run review --component web --preview-owner operations --preview-hours 8
```

Approve the waiting requests normally. Each run gets its own namespace under `.deploy-ops/previews/RELEASE_ID`, with an ownership marker binding the release, source, workspace, environment, owner, expiry, and retention choice. Owners are labels, not authenticated identities. Expiry must be 1 to 72 hours.

A preview is single use. It can't reuse another release's infrastructure, do an app-only update, or be promoted or rolled back, and it never gets unattended delegation.

## Serve and inspect expiry

```bash
deploy-ops preview RELEASE_ID --component web --port 8000
deploy-ops previews list
```

The loopback server serves the generated site. New requests get HTTP 410 after expiry or once cleanup execution begins. Preparing or approving cleanup doesn't stop serving or delete files. Stop the server yourself. There is no hosting service or cleanup scheduler.

## Review and execute cleanup

Finish the deployment, or reconcile an interrupted one, before preparing cleanup:

```bash
deploy-ops previews prepare-cleanup RELEASE_ID
```

`cleanup.plan` lists the generated files eligible for removal, retained entries, and empty generated directories. Source snapshots, prepared artifacts, approvals, audit, and incidents are outside cleanup. The ownership marker and namespace directory remain as a tombstone.

```bash
deploy-ops previews approve-cleanup RELEASE_ID --digest EXACT_REQUEST_DIGEST
deploy-ops previews cleanup RELEASE_ID
```

Cleanup uses the approval controller's protocol with a native `delete` action that is always manual and allowed only for declared preview targets. Approval doesn't execute cleanup. After claiming authority, cleanup rechecks the plan, ownership, file hashes, pointers, and entry types, unlinks only verified generated files, and removes only empty generated directories. Changes since preparation cause an uncertain outcome that needs inspection and fresh authority.

## Retain application data

Unknown or modified files and unrecognized directories remain. The result is `retired-retained`, with a retained-entry report. `retired` means the selected generated content was removed. Neither changes the deployment outcome. To keep all deployed files when retiring, declare that at creation:

```bash
deploy-ops run review --component web --preview-owner operations --preview-hours 8 --retain-preview-data
```

No cleanup override deletes retained data. This covers generated static files and a directory marker, not databases, shared dependencies, containers, clusters, or cloud resources. Source and build code is trusted local code, not a sandbox.

## Recover interrupted cleanup

In the console, first record a descriptive inspection note, then choose whether the namespace and files remain or the owned namespace is absent. These are native selection choices, separate from the note's ten-character minimum. Cancelling either dialog submits nothing. Choosing absence never bypasses the backend's ownership, retained-data, or absence checks and doesn't execute deletion.

A stale plan, interruption, or uncertain completion stays visible. Stop the worker and inspect the namespace before recording a note:

```bash
deploy-ops previews reconcile RELEASE_ID --note 'Inspected remaining files after stopping the worker'
deploy-ops previews prepare-cleanup RELEASE_ID
```

Approve the new digest before executing the new plan, even for a zero-file plan. Active worker processes block recovery. If you removed the whole namespace yourself, `reconcile --confirm-removed` records that when no retained data was planned. A missing path alone never counts as cleanup.

## Upgrade and boundaries

Back up state before upgrading, and don't downgrade while preview operations are in progress. Don't delete lock rows, since unattributed legacy locks fail closed in recovery. Unsupported: runtime teardown, shared storage, mutable services, cloud previews, automatic expiry deletion, and cross-preview artifact reuse.
