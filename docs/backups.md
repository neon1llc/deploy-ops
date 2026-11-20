# Private state backup and inspection restore

Capture the ledger together with its owned files before moving or retiring a workspace. A backup holds private configuration, source, artifacts, deployment data, and audit history, so keep it in private storage. DeployOps creates owner-only files and directories and doesn't encrypt or upload them. For a smaller report to share for review, use [portable inspection](inspection.md).

## Capture and verify

Finish or reconcile active work first. Capture refuses retained operation locks, running workers, controller executions, and paging claims. It never clears ownership to get a snapshot.

```bash
deploy-ops --project /path/workspace/deploy-ops.toml backup options
deploy-ops --project /path/workspace/deploy-ops.toml backup capture --out /private/backups/new-copy
deploy-ops backup inspect /private/backups/new-copy
```

Choose a new directory outside the workspace and state directory. An existing directory is never overwritten. Capture reserves the ledger while SQLite's native backup creates a self-contained database, including committed WAL data. It copies known owned files, checks the before and after inventory and configuration, and writes the manifest last. An interrupted directory stays available for inspection, but no manifest means no completed backup.

The manifest binds file hashes, sizes, executable flags, original locations, release count, and the verified audit prefix. Bounds are 20,000 entries, 512 MB total, and 100 MB per file or database. Missing files or unavailable evidence produce an explicit partial capture, so inspect its limits before relying on it. Hashes detect changed bytes, not an untrusted author.

Included: frozen release repositories, component source, artifacts, and prepared evidence, known native operation journals, local deployment and preview data, cleanup evidence, Save recovery copies, and the current workspace file when available. Excluded: runtime home and build-home caches, external credentials and source repositories, services, lock-file identities, and unknown state roots. Ambiguous legacy namespaces are reported unavailable rather than sweeping credential homes. Read the manifest's missing, unavailable, and omitted lists.

## Restore into new locations

Inspect the backup, then choose new private workspace and state directories:

```bash
deploy-ops backup restore /private/backups/new-copy --workspace-out /private/restores/new-workspace
deploy-ops backup inspect-restored /private/restores/new-workspace
deploy-ops --state /private/restores/new-workspace/.deploy-ops status
deploy-ops --state /private/restores/new-workspace/.deploy-ops events RELEASE_ID
```

State defaults to `.deploy-ops` inside the workspace. Use `--state-out` during restore and `--state-directory` during restored inspection for a separate location. CLI restore optionally takes `--digest` with the inspected manifest SHA-256, and the console always binds restore to its inspected digest. The original project filename is kept even if it isn't `deploy-ops.toml`.

Restore verifies source and destination bytes and audit lineage. Its receipt and manifest allow `inspect-restored` without the original backup. Existing directories are refused. A failed or interrupted restore stays inspection-only, so preserve it and choose fresh targets after diagnosis.

The backup and the restored workspace and state both carry inspection boundaries. Writable Store, Engine, configuration, and controller paths refuse them, including alternate state selections. No worker restarts, lock clearing, approval revival, or path or digest rewriting occurs. Keep the markers and the matching runtime. Removing them isn't an execution-admission procedure.

## Original locations and relocated copies

A relocated copy keeps absolute references for truthful history and doesn't redirect them. Read-only status, events, and audit verification stay useful even when source or artifacts are missing. Dependent operations refuse incompatible evidence.

For an original-location exercise, preserve the old owned workspace elsewhere and restore into its now-absent original root and state location. Never overwrite an active owner. When all exact paths and files match, a successful retained static artifact can be read through the loopback preview owner:

```bash
deploy-ops --project /original/workspace/deploy-ops.toml --state /original/workspace/.deploy-ops preview RELEASE_ID --component web --retained
```

This serves reviewed bytes without initializing configuration, grants, or schema. Stop the command to close its listener. Relocated or missing evidence is refused. Static readback doesn't establish provider health, authorize deployment, or resume historical execution. Provider reconciliation and execution admission are separate, unsupplied work.

## Browser console

Open **Controls → Private state backups**. Suggestions fill only empty fields. Capture and Inspect share the CLI's backend. Review completeness, audit extent, and omitted scope, then choose new restore locations. Restore needs a separate confirmation and defaults focus to Cancel. **Inspect restored copy** checks an existing workspace without creating or executing it. Validation and failed requests keep entered paths. Nothing captures in the background or uploads.

The implementation uses [Python's SQLite backup API](https://docs.python.org/3.12/library/sqlite3.html#sqlite3.Connection.backup) and the [SQLite online backup mechanism](https://www.sqlite.org/backup.html). Copying a raw database without its WAL is not the capture procedure.
