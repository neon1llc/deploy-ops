# Source capture history

Source copying can stop before a release exists. A source capture record identifies the selected
components/repositories, last recorded phase, copied component count and successful repository
identities. It uses the intended release ID without registering a deployment early. No build,
provider preparation, approval or deployment is started by reading it.

```bash
deploy-ops captures list --limit 20
deploy-ops captures show CAPTURE_ID
# Read original or restored state without loading its workspace or credentials.
deploy-ops --state /absolute/retained/state captures show CAPTURE_ID
```

In **Releases**, expand **Source capture history**. A preparation refusal opens it without discarding
the release form. Each record separates the last source phase from registration observed in the
retained database. **Open registered release** appears only when its ID, project, environment,
configuration, source identity and component order match. It opens inspection, not execution.

For another original or restored location, expand **Inspect retained capture elsewhere**, enter its
state directory and capture ID, then inspect it. Use an absolute path or `~/`. This works through a
running local console without loading that location's workspace. Registration there never becomes
permission for the current console to resume it. Authored paths stay intact during live-history polling.

## What the record means

The private marker is `STATE/releases/CAPTURE_ID/capture.json`. It is atomically replaced before
capture phases. Its source selectors use the existing repository schema. Current-files mode ignores
the configured Git ref. Committed mode records the resolved Git identity after a successful capture.
A promoted component identifies its retained source release separately from configured selectors.
Captured digests describe that capture, not later build output or current target health.

The final source phase can remain `registration-requested` even when registration is observed as
successful. The database observation links the same identity independently, avoiding a second
post-registration file write that could falsely make a successful registration appear lost. Missing
or unreadable registration is shown separately. A marker alone never proves a release exists.

A refusal preserves its phase and readable reason when recording is possible. Process interruption
or storage failure can leave only the last published phase, or an unrecorded directory if the initial
record could not be published. The recorded creator PID is not proof that it is currently alive or
stopped. These are observations with no resume, deletion, adoption or automatic retry action.

Known missing repositories or unavailable committed-source directories refuse before allocating an
attempt. A later source refusal keeps the earlier copied files private and inspectable as an attempt.
Unknown directories and older releases without markers remain unchanged and are not backfilled.

## Bounded history and private backup

History defaults to 20 records, supports at most 100 per page, and scans at most 10,000 entries in one
directory level. It orders marker modification times, newest first. Live updates can move offset
pages. Exact-ID inspection does not require that history scan. Unsupported or changed records are
unavailable rather than interpreted as ownership or registration evidence.

Private backups include a registered release's marker alongside its existing supported source extent.
For an unregistered attempt, only the supported marker is included. Partial source directories are
explicitly omitted. Preserve the original private folder if those partial bytes matter. Restored markers
and database snapshots remain inspection-only and do not authorize continuation or adopt unknown files.
