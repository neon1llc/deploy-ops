# Isolated static previews

A static preview is a new, owned deployment namespace with an owner label and an expiry. It uses the normal
repository snapshots, exact approvals and local static adapter. Cleanup is a separate reviewed operation.
Existing deployment paths are never adopted as previews, and expiry never deletes files.

## Declare and deploy

Enable previews only on a local, nonproduction environment:

```toml
[environments.review]
provider = "local"
preview = true
```

Use one static application with at most one directory foundation. Include its full dependency graph.
Static destinations must use the isolated default or a path output from that same preview's dependency;
arbitrary destination paths are refused. The ordinary local starter's foundation/path contract works.

```bash
deploy-ops run review --component web --preview-owner operations --preview-hours 8
```

Approve the waiting foundation and application requests normally. Each run allocates its own namespace
under `.deploy-ops/previews/RELEASE_ID`, outside ordinary deployments. Its ownership marker binds the
release, source, workspace, environment, owner, expiry and retention choice. Owners are descriptive labels,
not authenticated remote identities. Expiry must be 1 to 72 hours.

A preview is single use. It cannot reuse another release's infrastructure, perform an app-only update,
or participate in promotion/rollback. Create a fresh isolated preview for another revision. Preview
targets never receive unattended delegation. Ordinary local deployment behavior is unchanged.

The browser shows owner/expiry/retention fields when a preview environment is selected. The release list
shows preview state and whether cleanup is due; the detail shows deployment and cleanup outcomes separately.
The guided environment form can enable preview mode, with schema validation before saving.

## Serve and inspect expiry

```bash
deploy-ops preview RELEASE_ID --component web --port 8000
deploy-ops previews list
```

The existing loopback server serves the generated site. New requests receive HTTP 410 after expiry or
once cleanup execution begins. Preparing or approving a cleanup plan does not stop serving or delete files.
Already accepted HTTP responses may finish. The server process remains operator-managed; stop it when
finished. No permanent hosting service or background cleanup scheduler is created.

## Review and execute cleanup

Finish the deployment or inspect/reconcile an interrupted deployment before preparing cleanup:

```bash
deploy-ops previews prepare-cleanup RELEASE_ID
```

Inspect `cleanup.plan`: exact generated files eligible for removal, retained entries and empty generated
directories. Source snapshots, prepared artifacts, approvals, audit and incidents remain outside the
cleanup scope. The ownership marker and its namespace directory always remain as a durable tombstone.

```bash
deploy-ops previews approve-cleanup RELEASE_ID --digest EXACT_REQUEST_DIGEST
deploy-ops previews cleanup RELEASE_ID
```

These use the same approval controller's submit, decision, claim, heartbeat and completion protocol.
The native action is `delete` and is always manual. Only explicitly declared preview targets permit it;
ordinary targets and production policy are unchanged. Approval does not execute cleanup. The browser
also separates preparation, exact approval and execution.

After claiming authority, cleanup rechecks the authenticated plan, ownership, file hashes, pointers and
entry types. It unlinks only verified generated files and removes only empty generated directories.
Directory handles are opened without following symlinks. It never recursively deletes a namespace.
Changes since preparation cause an uncertain outcome requiring inspection and fresh authority.

## Retain application data

Unknown or modified files and unrecognized directories remain. The result is `retired-retained`, with an
owner and retained-entry report. `retired` means the selected generated content was removed; the ownership
tombstone still remains. Neither state changes the original deployment outcome.

To retain all deployed files when retiring, declare that choice at creation:

```bash
deploy-ops run review --component web --preview-owner operations --preview-hours 8 --retain-preview-data
```

There is no cleanup override that deletes retained data. This initial contract covers generated static files
and a directory marker, not databases, shared dependencies, containers, clusters or cloud resources.
Source/build code is trusted local code; these ownership checks do not turn it into a hostile-code sandbox.

## Recover interrupted cleanup

A stale plan, interruption or uncertain controller completion remains visible. Stop the actual worker and
inspect the exact namespace before recording a note:

```bash
deploy-ops previews reconcile RELEASE_ID --note 'Inspected remaining files after stopping the worker'
deploy-ops previews prepare-cleanup RELEASE_ID
```

Approve the new digest before executing the new plan. Even a zero-file plan after lost completion needs
fresh authority. The retained ownership tombstone allows this without adopting an unmarked replacement
directory. Active worker processes block recovery. Stale lock inspection and replacement are atomic, so
concurrent reconcilers cannot remove one another's ownership.

If an operator independently removed the entire namespace after inspection, `reconcile --confirm-removed`
can record that explicit finding when no retained data was planned. A missing path alone is never treated
as successful cleanup. Do not use that flag when any namespace or retained data remains.

## Upgrade and boundaries

Disabled preview defaults are omitted from legacy workspace fingerprints. Existing target policies and
pending ordinary approvals remain compatible; an actual v0.9 pending request was completed under v0.10.
An additive lock-owner metadata table records the local worker PID without changing the existing lock-table
layout. Back up state before upgrading and finish or reconcile old workers using their matching version.
Unattributed legacy locks fail closed in the new recovery path; do not delete lock rows to bypass it.
Do not downgrade while preview operations are in progress.

Runtime/process teardown, shared storage, mutable services, cloud previews, automatic expiry deletion and
cross-preview artifact reuse remain unsupported. The [support matrix](support-matrix.md) distinguishes
local evidence from provider qualification.
