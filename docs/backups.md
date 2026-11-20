# Private state backup and inspection restore

Capture the ledger together with its owned files before moving or retiring a workspace. A backup
contains private configuration, source, artifacts, deployment data and audit history. Keep it in private
storage. DeployOps creates owner-only files and directories. It does not encrypt or upload them.
For a smaller report intended for review, use [portable inspection](inspection.md) instead.

## Capture and verify

Finish or reconcile active work first. Capture refuses retained operation locks, running workers,
controller executions and paging claims. It does not clear ownership to obtain a snapshot.

```bash
deploy-ops --project /path/workspace/deploy-ops.toml backup options
deploy-ops --project /path/workspace/deploy-ops.toml backup capture --out /private/backups/new-copy
deploy-ops backup inspect /private/backups/new-copy
```

Choose a new directory outside the source workspace and state directory. An existing directory is
never overwritten. Capture reserves the ledger while SQLite's native backup operation creates a
self-contained database, including committed WAL data. It copies known owned files, checks the
before/after inventory and exact configuration, and writes the manifest last. Interrupted directories
remain available for inspection. An absent manifest is not a completed backup.

The manifest binds file hashes, sizes, executable flags, original locations, release count and the
verified audit prefix. Bounds are 20,000 entries, 512 MB total and 100 MB per file or database.
Missing files or unavailable evidence produce an explicit partial capture. Inspect its limits before
relying on it. Hash verification detects changed bytes. It does not authenticate an untrusted author.

Included scope covers frozen release repositories, component source/artifacts/prepared evidence,
known native operation journals, local deployment and preview data, cleanup evidence, Save recovery
copies and the current workspace file when available. Runtime home/build-home caches, external
credentials and source repositories, services, lock-file identities and unknown state roots are
excluded. Ambiguous legacy repository/component namespaces are reported as unavailable rather than
sweeping credential homes. Read the manifest's missing, unavailable and omitted lists.

## Restore into new locations

Inspect the backup, then select new private workspace and state directories:

```bash
deploy-ops backup restore /private/backups/new-copy --workspace-out /private/restores/new-workspace
deploy-ops backup inspect-restored /private/restores/new-workspace
deploy-ops --state /private/restores/new-workspace/.deploy-ops status
deploy-ops --state /private/restores/new-workspace/.deploy-ops events RELEASE_ID
```

State defaults to `.deploy-ops` inside the selected workspace. Use `--state-out` during restore and
`--state-directory` during restored inspection for a separate state location. CLI restore optionally
accepts `--digest` with the inspected manifest SHA-256. The console always binds restore to its selected
inspected digest. The original project filename is retained, even when it differs from `deploy-ops.toml`.

Restore verifies source and destination bytes and audit lineage. Its receipt and manifest allow
`inspect-restored` without the original backup attached. Existing directories are refused. A failed or
interrupted restore remains inspection-only. Preserve it and choose fresh targets after diagnosis.

Both the backup and restored workspace/state carry inspection boundaries. Normal writable Store,
Engine, configuration and controller paths refuse them, including alternate state selections. No worker
restarts, lock clearing, approval revival, path rewriting or digest rewriting occurs. Keep these markers
and the matching runtime. Removing them is not an execution-admission procedure.

## Original locations and relocated copies

A relocated copy preserves absolute references for truthful history. It does not redirect them to
other files. Read-only status, events and audit verification can remain useful even when source or
artifacts are missing. Dependent operations must refuse incompatible evidence.

For a separate original-location exercise, preserve the old owned workspace elsewhere and restore
into its now-absent original root, with the original state location. Never overwrite an active owner.
When all exact paths and files match, a successful retained static artifact can be read through the
existing loopback preview owner:

```bash
deploy-ops --project /original/workspace/deploy-ops.toml --state /original/workspace/.deploy-ops preview RELEASE_ID --component web --retained
```

This serves reviewed bytes without initializing configuration, grants or schema. Stop the command to
close its owned listener. Relocated or missing evidence is refused. Successful static readback does
not establish current provider health, authorize deployment or resume historical execution. Fresh
provider reconciliation and execution admission remain separate, unsupplied work.

## Browser console

Open **Controls → Private state backups**. Suggestions fill only empty fields. Capture and Inspect
share the same backend as the CLI. Review completeness, audit extent and omitted scope, then choose
new restore locations. Restore requires a separate confirmation and defaults focus to Cancel.
**Inspect restored copy** checks an existing selected workspace without creating or executing it.
Validation and failed requests preserve entered paths. No background capture or automatic upload occurs.

The implementation uses [Python's SQLite backup API](https://docs.python.org/3.12/library/sqlite3.html#sqlite3.Connection.backup)
and the [SQLite online backup mechanism](https://www.sqlite.org/backup.html). Copying a raw database
without its WAL is not the capture procedure.
