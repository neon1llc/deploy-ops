# Migration from DeployGuard

The display name is **DeployOps**, the repository and command are `deploy-ops`, the package is `deploy_ops`, and environment variables use `DEPLOY_OPS_`. The proposed distribution is `neon1-deploy-ops`. Registry and trademark availability haven't been cleared.

The prior controller, policy, OIDC, Slack, cloud-federation, and split-job IaC behavior was kept and renamed. Its former CLI is now `deploy-ops gate`. The new default CLI owns complete release operations. The previous 66 regression tests remain in the source tree.

Start with a fresh state database and a new operator token. Live DeployGuard requests, leases, and grants don't migrate. To switch:

1. Export historical audit records.
2. Let active work finish.
3. Revoke old grants.
4. Replace cloud trust only after testing the new pinned workflow.

Don't copy an active lease into a renamed service or reuse old audience settings. The MIT license notice stays in this repository, and new features use Apache-2.0 with NOTICE.

## Native infrastructure and local cloud grants

The v0.11.0 native adapters use the existing release ledger and approval state machine, so no schema migration is needed. The optional `local_cloud` target flag defaults to false and is omitted from historical target fingerprints when unused.

Default protected resource types now include CloudFormation and ARM names alongside Terraform names. This changes policy fingerprints for targets using the default denylist. Finish or reconcile in-flight work before upgrading, then prepare fresh requests and recreate delegated windows. Don't reuse an approval under a changed policy.

## Isolated execution and Container Apps recovery

The optional `local_isolated_iac` target mode keeps local operator provenance separate from GitHub claims. It's omitted from existing target fingerprints when unused, and no database schema migration is needed. Register a new private grant for the isolated route. Existing local or federated grants don't gain this authority.

Container Apps requests prepared before exact revision journaling must be completed or reconciled under their original version. Prepare a fresh release for the new adapter, which refuses old prepared requests without a reviewed recovery identity. Existing Single-mode apps must contain exactly one named application container.

## Waiting transition identity

New waiting nodes retain the exact `waiting_request` ID/digest in the same transaction as their
first `release.waiting` event and outboxes. Existing history and delivery IDs stay unchanged. An
older waiting node without this binding records one current decision-needed transition on its next
advance, then unchanged observations stay quiet. Stop old writers before upgrading; older writers
do not enforce this deduplication. This adds no reminder scheduler or authority.

## Local target ownership

New directory/static preparations use adapter contract 2. Readers authenticate old evidence without
rewriting it. Pending contract 1 local operations require fresh preparation before execution; finish
or reconcile old work under its matching runtime before upgrading. Previously prepared ownership
is derived from authenticated artifacts when checking another writer. Missing historical evidence
requires inspection before assigning a different owner. Failed and cancelled reservations are retained.
No database migration, automatic takeover or implicit cleanup is supplied.

## Observed controller timing

Request reads add a safe `timing` projection without changing retained manifests, request digests,
approvals or database records. Older clients can ignore it. New clients report timing unavailable
when an older controller omits it; existing controller enforcement remains authoritative. The
projection contains explicitly selected timestamps and limits, without execution tokens or hashes.

## Local worker evidence

New local release admissions/stops and command launch/stop records append to the existing events and
audit chain. No original manifest, digest or historical record is rewritten. The database schema is
unchanged. Historical readers can inspect retained records, but older recovery writers do not enforce
this protocol and must not be used to bypass a refusal. Keep the matching runtime with interrupted work.
Legacy or relocated evidence without matching local worker ownership remains readable but does not
authorize reconciliation in this runtime. There is no automatic ownership backfill or force-unlock.

Local execution records additionally retain the controller's internal callback binding to the exact
worker admission. This is outside the approved manifest. HTTP claim input cannot supply this binding.
Deployment, preview cleanup and provider readback keep their separate meanings under the shared lock.
An imported, legacy or external claim without matching ownership remains unknown; no later successful
readback or refused local execution authorizes resolving it.

## Portable inspection

The version 1 inspection format is additive and read-only. It does not change the database schema,
approved envelopes, controller manifests or authority. New native journal writes add recorded and
observation timestamps; old records without them remain readable and report time unavailable.
The JSON reader rejects unsupported versions and does not turn an imported summary into runtime state.
Keep the original state and matching runtime for recovery. The inspection artifact is not a backup.

## Interrupted configuration Save

New Saves retain a version 1 local intent and private per-operation before/draft copies, outside
approved deployment artifacts. `workspace.updated` adds exact source hashes and a Save operation ID;
old audit records and request digests remain unchanged. Reconciliation appends a distinct observation
and keeps current content. No database schema migration or automatic historical backfill occurs.
Retained readers still default to read-only; the explicit existing-state audit writer never creates
or initializes a database. Legacy pending files without verified intent require manual inspection.
Finish or reconcile new pending Saves with this matching runtime; an older writer does not enforce
the new guard. See [Save recovery](save-recovery.md) for current-file, ownership and relocation limits.

## Private backup and restore

Backup format 1 adds a coherent SQLite snapshot and exact owned-file manifest without changing the
database schema or historical rows. Restored state and backup payloads are inspection-only; use the
matching runtime to enforce that boundary. Older runtimes do not understand it and must not be used
to bypass admission. Original absolute references remain unchanged. `preview --retained` permits
only compatible, reviewed static readback. There is no automatic writable restore or provider-resume
procedure. See [private backups](backups.md) for scope, bounds and original-location exercises.

## Cloud Run native operations

New Cloud Run preparation carries a version 1 native request inside the existing approved envelope.
The former CLI-only preparation lacks this identity and cannot be executed by the new writer. Retain
the matching runtime for old work, inspect uncertain provider effects and prepare a fresh release;
no historical approval, digest or audit row is rewritten. The new fixed-origin REST writer uses current
native credentials and service etags, records both operation boundaries and never retries an attempted
write. It supports one existing container and an untagged single route. Portable inspection format 2
adds this native scope/phase; the reader preserves exact format 1 extents. Private backups additionally
include `cloud-run-operation.json`. No database schema migration or implicit grant change is supplied.

## Artifact observations

The optional explicit collector writes a version 1 report outside workspace/state. It changes no
workspace schema, digest, approved envelope, authority or database schema. Legacy reviewed static
artifacts can be measured when their existing format and exact retained bytes validate. The shared
canonical tree helper preserves prior ordering and digest meaning; file/byte inventory remains separate
from package detection. Unknown report versions refuse offline inspection. Collected results neither
backfill a historical approval nor enforce a new release policy. See [artifact observations](artifact-observations.md).

## Active-target serving

The optional local listener follows the existing reviewed static target without changing approval,
prepared-envelope, database or pointer formats. Fixed revision serving remains the default. Target
mode requires physical ownership in adapter contract 2 and refuses isolated previews or legacy
ownership evidence. Existing revisions and audit bytes are preserved. See [local serving](local-serving.md).

## Separate local activation

Static `activation = "separate"` prepares adapter contract 3; direct activation continues to prepare
contract 2. Readers preserve contracts 1/2, and old approved bytes are not reserialized. A separate
candidate needs an existing baseline and fresh matching dependency receipts after a workspace change.
New manual route/retirement requests and per-operation `rollouts/` plans use the existing controller
and release journal; there is no database schema migration or implicit listener adoption. Ordinary local
static targets gain deletion authority only through this opt-in profile's manual retirement proposals;
existing isolated-preview cleanup remains separate.

The new version 1 rollout snapshot has its own offline reader and no execution authority. Deployment
inspection formats 1/2 refuse separately activated releases because their component outcome does not
represent routing completion. Required notices and existing history remain unchanged. See
[local rollout](local-rollouts.md) for operation, interruption, backup and inspection behavior.

## Selected-file signatures

The optional verifier writes a separate version 1 `deploy-ops.file-signature-observation` report. It
changes no approved artifact envelope, workspace digest, approval, store schema or existing collection
report. Its raw extent includes a bounded selected-file copy plus the local public key and modern
bundle; diagnostics/tool home stay outside that extent. Older inventory/vulnerability reports retain
their original meaning. No imported signature result is turned into execution or policy authority.
See [file signatures](file-signatures.md).

Preview cleanup reconciliation now uses a native namespace-state selection after the descriptive note.
It no longer asks for blank or `removed` through a dialog that requires ten-character notes. The note
constraint, cancellation and all backend cleanup checks remain intact.
