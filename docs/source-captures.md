# Source capture history

Source copying can stop before a release exists. A source capture record names the selected components and repositories, the last recorded phase, the copied component count, and the identities of successful repositories. It uses the intended release ID without registering a deployment early. Reading it starts no build, provider preparation, approval, or deployment.

```bash
deploy-ops captures list --limit 20
deploy-ops captures show CAPTURE_ID
# Read original or restored state without loading its workspace or credentials.
deploy-ops --state /absolute/retained/state captures show CAPTURE_ID
```

In **Releases**, expand **Source capture history**. A preparation refusal opens it without discarding the release form. Each record separates the last source phase from registration observed in the retained database. **Open registered release** appears only when its ID, project, environment, configuration, source identity, and component order match, and it opens inspection, not execution.

For another original or restored location, expand **Inspect retained capture elsewhere**, enter its state directory (absolute or `~/`) and capture ID, then inspect. This works through a running console without loading that location's workspace, and registration there never lets the current console resume it. Authored paths stay intact during polling.

## What the record means

The private marker is `STATE/releases/CAPTURE_ID/capture.json`, atomically replaced before capture phases. Source selectors use the repository schema. Current-files mode ignores the configured Git ref, and committed mode records the resolved Git identity after a successful capture. A promoted component names its retained source release separately. Captured digests describe that capture, not later build output or target health.

The final phase can stay `registration-requested` even when registration is observed as successful, because the database observation links the same identity independently. This avoids a second post-registration file write that could make a success look lost. Missing or unreadable registration is shown separately, and a marker alone never proves a release exists.

A refusal keeps its phase and readable reason when recording is possible. Interruption or storage failure can leave only the last published phase, or an unrecorded directory if the first record couldn't be published. The recorded creator PID doesn't show whether that process is alive. There is no resume, deletion, adoption, or automatic retry.

Known missing repositories or unavailable committed-source directories refuse before an attempt is allocated. A later source refusal keeps the earlier copied files private and inspectable as an attempt. Unknown directories and older releases without markers stay unchanged and aren't backfilled.

## Bounded history and private backup

History defaults to 20 records, allows at most 100 per page, and scans at most 10,000 entries in one directory level, ordered by marker modification time, newest first. Live updates can move offset pages. Exact-ID inspection doesn't need the scan. Unsupported or changed records are unavailable, never read as ownership or registration evidence.

Private backups include a registered release's marker with its supported source extent. For an unregistered attempt, only the marker is included and partial source directories are omitted, so preserve the original private folder if those bytes matter. Restored markers and database snapshots stay inspection-only and can't authorize continuation or adopt unknown files.
