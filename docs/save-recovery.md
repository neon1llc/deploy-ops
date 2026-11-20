# Recover an interrupted configuration Save

Save keeps exact UTF-8 before and draft copies, including authored line endings, before replacing the workspace. It flushes the copies, intent, and replacement to disk. The file replacement and the SQLite audit commit are separate, so a crash can leave the old or new workspace with no committed update event, or the new workspace with its event already committed.

The console's **Save recovery → Inspect Save state** and the CLI use the same owner:

```bash
deploy-ops config recovery
deploy-ops config reconcile --operation-id SAVE_ID --source-hash CURRENT_SOURCE_HASH \
  --note 'Inspected the current file and retained before/draft copies'
```

Use `--project` and `--state` before `config` when the workspace or state is elsewhere. Status reads existing state without initializing an Engine, grants, credentials, or schema. Reconciliation opens the verified database for an explicit audit write and doesn't create or migrate state. Both work when an external edit has made the current TOML invalid.

## Choose what to retain

Recovery shows whether the current file matches the before copy, the proposed draft, or an external edit, and whether the original `workspace.updated` audit event committed. Review the current source hash and recovery copies, then choose **Keep current file and reconcile Save**. An inspection note is required. A changed hash or Save identity needs a fresh inspection. When before, draft, and current bytes are identical, recovery reports `same_content` and can't infer the replacement boundary. The audit observation stays separate.

Reconciliation keeps the current file byte for byte, including external or invalid edits. It appends `workspace.save_reconciled` with the observed hash and original audit status, and doesn't fabricate a missing `workspace.updated` event. It removes only the verified owned pending draft and Save intent. The before and draft copies stay under `workspace-saves/SAVE_ID/`. An interrupted reconciliation can finish without a duplicate audit observation, and repeated requests return their retained result. If the current file changes again after a committed reconciliation but before cleanup finishes, a fresh inspection records that hash as a new observation without rewriting the earlier one.

To restore a retained version, use a separate reviewed, hash-guarded Save: validate that file as a draft, inspect its diff, and save against the current source hash. An invalid current file stays available for inspection until you correct it in your editor. Recovery doesn't repair TOML, replace unrelated edits, replay a deployment, or change approval identities.

The console keeps unsaved drafts through inspection and reconciliation. After a valid configuration is reconciled, **Reload console** is a separate choice. Preserve drafts first, since a reload clears the session token and form drafts.

## Boundaries

An interprocess workspace lock excludes cooperative Saves, release preparation, and retirement preparation. A retained Save intent blocks new work until reconciled. Recovery refuses a live writer, changed lock identity, relocated intent, missing or altered recovery copies, or an unrelated pending draft, and has no force option. Keep all files when ownership can't be verified. A legacy `.pending` without the new intent is unverified, so preserve and review it before renaming or removing it.

External editors don't take part in this advisory lock. Save rechecks the source identity just before replacement, but that isn't a filesystem compare-and-swap against an uncooperative editor, so pause external editing during Save. Process-interruption checks don't establish hardware power-loss behavior or distributed-filesystem locking.

Recovery copies and the compatibility `previous-workspace.toml` are private local configuration, not portable inspection artifacts or complete backups. Per-Save copies stay until you remove them. A later Save replaces the compatibility copy. There is no automatic retention cleanup, so keep copies you still need. Retained-file reads are bounded to 2 MiB.
