# Isolated static previews

A static preview is a new, owned deployment namespace with an owner label and an expiry. It uses the normal repository snapshots, exact approvals, and local static adapter. Cleanup is a separate reviewed operation. Existing deployment paths are never adopted as previews, and expiry never deletes files.

## Declare and deploy

Enable previews only on a local, nonproduction environment:

```toml
[environments.review]
provider = "local"
preview = true
```

Use one static application with at most one directory foundation, and include its full dependency graph. Static destinations must use the isolated default or a path output from that preview's own dependency. Arbitrary destination paths are refused. The ordinary local starter's foundation/path contract works.

```bash
deploy-ops run review --component web --preview-owner operations --preview-hours 8
```

Approve the waiting foundation and application requests normally. Each run gets its own namespace under `.deploy-ops/previews/RELEASE_ID`, outside ordinary deployments. Its ownership marker binds the release, source, workspace, environment, owner, expiry, and retention choice. Owners are descriptive labels, not authenticated identities. Expiry must be 1 to 72 hours.

A preview is single use. It can't reuse another release's infrastructure, do an app-only update, or take part in promotion or rollback. Create a fresh preview for another revision. Preview targets never get unattended delegation.

The browser shows owner, expiry, and retention fields when a preview environment is selected. The release list shows preview state and whether cleanup is due, and the detail shows deployment and cleanup outcomes separately. The guided environment form can enable preview mode.

## Serve and inspect expiry

```bash
deploy-ops preview RELEASE_ID --component web --port 8000
deploy-ops previews list
```

The loopback server serves the generated site. New requests get HTTP 410 after expiry or once cleanup execution begins. Preparing or approving a cleanup plan doesn't stop serving or delete files. Responses already accepted may finish. Stop the server yourself when finished. No permanent hosting service or background cleanup scheduler is created.

## Review and execute cleanup

Finish the deployment or inspect/reconcile an interrupted deployment before preparing cleanup:

```bash
deploy-ops previews prepare-cleanup RELEASE_ID
```

Inspect `cleanup.plan`: the exact generated files eligible for removal, retained entries, and empty generated directories. Source snapshots, prepared artifacts, approvals, audit, and incidents are outside cleanup scope. The ownership marker and its namespace directory always remain as a tombstone.

```bash
deploy-ops previews approve-cleanup RELEASE_ID --digest EXACT_REQUEST_DIGEST
deploy-ops previews cleanup RELEASE_ID
```

These use the approval controller's submit, decision, claim, heartbeat, and completion protocol. The native action is `delete` and is always manual. Only declared preview targets permit it. Approval doesn't execute cleanup, and the browser separates preparation, approval, and execution too.

After claiming authority, cleanup rechecks the authenticated plan, ownership, file hashes, pointers, and entry types. It unlinks only verified generated files and removes only empty generated directories. Directory handles open without following symlinks, and it never deletes a namespace recursively. Changes since preparation cause an uncertain outcome that needs inspection and fresh authority.

## Retain application data

Unknown or modified files and unrecognized directories remain. The result is `retired-retained`, with an owner and retained-entry report. `retired` means the selected generated content was removed, though the tombstone remains. Neither state changes the original deployment outcome.

To retain all deployed files when retiring, declare that choice at creation:

```bash
deploy-ops run review --component web --preview-owner operations --preview-hours 8 --retain-preview-data
```

No cleanup override deletes retained data. This contract covers generated static files and a directory marker, not databases, shared dependencies, containers, clusters, or cloud resources. Source and build code is trusted local code, and these checks don't make it a hostile-code sandbox.

## Recover interrupted cleanup

A stale plan, interruption, or uncertain controller completion stays visible. Stop the worker and inspect the namespace before recording a note:

```bash
deploy-ops previews reconcile RELEASE_ID --note 'Inspected remaining files after stopping the worker'
deploy-ops previews prepare-cleanup RELEASE_ID
```

Approve the new digest before executing the new plan. Even a zero-file plan after lost completion needs fresh authority. The tombstone allows this without adopting an unmarked replacement directory. Active worker processes block recovery.

If you removed the whole namespace yourself after inspection, `reconcile --confirm-removed` records that finding when no retained data was planned. A missing path alone never counts as successful cleanup. Don't use the flag when any namespace or retained data remains.

## Upgrade and boundaries

Disabled preview defaults are omitted from legacy workspace fingerprints, so existing target policies and pending ordinary approvals stay compatible. A real v0.9 pending request was completed under v0.10. A lock-owner metadata table records the local worker PID without changing the lock-table layout. Back up state before upgrading, and finish or reconcile old workers with their matching version. Unattributed legacy locks fail closed in recovery. Don't delete lock rows to bypass that. Don't downgrade while preview operations are in progress.

Runtime teardown, shared storage, mutable services, cloud previews, automatic expiry deletion, and cross-preview artifact reuse are unsupported. The [support matrix](support-matrix.md) separates local evidence from provider qualification.
