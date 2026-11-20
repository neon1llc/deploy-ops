# Recover an interrupted configuration Save

Save retains exact UTF-8 before/draft copies, including authored line endings, before replacing the
workspace. It flushes the copies, intent and replacement to disk. The file replacement and SQLite
audit commit are separate operations. A crash can leave either the old or new workspace with no
committed update event, or the new workspace with its update event already committed.

The console's **Save recovery → Inspect Save state** and the CLI use the same owner:

```bash
deploy-ops config recovery
deploy-ops config reconcile --operation-id SAVE_ID --source-hash CURRENT_SOURCE_HASH \
  --note 'Inspected the current file and retained before/draft copies'
```

Use `--project` and `--state` before `config` when the workspace or state has another location. The
status command reads existing state without initializing an Engine, grants, credentials or schema.
Reconciliation opens the verified existing database for an explicit audit write. It does not create
or migrate state. This remains usable when an external edit has made current TOML invalid.

## Choose what to retain

Recovery shows whether the current file matches the before copy, the proposed draft or an external
edit. It also reports whether the original `workspace.updated` audit event committed. Review the
exact current source hash and recovery copies before choosing **Keep current file and reconcile Save**.
An inspection note is required. A changed hash or Save identity requires a fresh inspection.
When before/draft/current bytes are identical, recovery reports `same_content`; it cannot infer the
replacement boundary from identical contents. The audit observation remains separate.

Reconciliation keeps the current file byte-for-byte, including external or invalid edits. It appends
`workspace.save_reconciled` with the observed hash and original audit status. It does not fabricate
a missing `workspace.updated` event. It removes only the verified owned pending draft and Save intent.
Exact before/draft copies remain under `workspace-saves/SAVE_ID/`. An interrupted reconciliation
can finish without appending a duplicate audit observation. Repeated requests return their retained
result without changing the current configuration.
If the current file changes again after a committed reconciliation but before its cleanup finishes,
a fresh inspection records that different hash as a new observation. It does not rewrite the earlier one.

Restoring a retained version is a separate reviewed, hash-guarded Save. Validate that file as a draft,
inspect its diff, and save against the current source hash. An invalid current file remains available
for inspection. Correct it with your editor before restarting normal deployment work. Recovery itself
does not repair TOML, replace unrelated edits, replay a deployment or change approval identities.

The console preserves unsaved drafts during inspection and reconciliation. Once a valid configuration
is reconciled, **Reload console** is a separate choice. Preserve drafts before reloading. A reload
clears the session token and form drafts.

## Boundaries

A real interprocess workspace lock excludes cooperative Saves, release preparation and retirement
preparation. A retained Save intent blocks new work until reconciled. Recovery refuses a live writer,
changed lock identity, relocated intent, missing/altered recovery copies or an unrelated pending draft.
It supplies no force option. Keep all files when ownership cannot be verified. A legacy `.pending`
without the new intent is unverified. Preserve and review it before manually renaming or removing it.

External editors do not participate in this advisory lock. Save rechecks exact source identity just
before replacement, but this is not a filesystem compare-and-swap guarantee against an uncooperative
concurrent editor. Pause external editing during Save. Process-interruption checks do not establish
hardware power-loss behavior or a distributed-filesystem lock guarantee.

Recovery copies and the compatibility `previous-workspace.toml` are private local configuration,
not portable inspection artifacts or complete backups. Per-Save copies are retained until you remove
them deliberately. The compatibility copy is replaced by a later Save. Keep copies you still need;
there is no automatic retention cleanup. Ordinary retained-file reads are bounded to 2 MiB.
