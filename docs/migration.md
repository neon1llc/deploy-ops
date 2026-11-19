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

## Complete migration and stack-deletion effects

New Terraform/OpenTofu migration manifests carry `native_effects_version = 2` alongside caller schema 2.
Their change rows preserve native actions plus import/move. Existing manifest bytes, digests and audit
remain unchanged. The new writer refuses a legacy migration claim before execution admission and
shows a fresh-plan blocker in CLI/API and UI projections. Read, rejection, pause and historical
resolution remain available. Legacy caller-schema-1 migration submissions require a current caller
submission; nonmigration schema-1 history and claims remain supported. There is no database migration.

New CloudFormation/CDK destruction prepares `destroy_contract_version = 2`. The application runner
refuses old deletion envelopes before claim; the native adapter also refuses before any native read
or write. Old inspection stays available. Prepare fresh evidence to review stack removal and all
resource fates. Local grants must explicitly include the stack type, delete and any snapshot action;
they are never edited automatically. New snapshot rows require the current reader; an older typed
reader may refuse them. Older writers do not acquire these new enforcement guarantees by reading a
new report. Use the current writer consistently for mutations; no cross-version writer lock or
historical backfill is introduced. See [native infrastructure](native-infrastructure.md).

## Generic IaC readiness

The generic Terraform/OpenTofu delivery adapter prepares `iac_contract_version = 2`, including the
saved-plan digest, effective initialized backend binding, selected-output readiness and native effects
version 2. This extends the controller-path migration guard to the separate generic delivery
runner. Earlier generic prepared envelopes lack that complete contract and refuse before runner claim
or native adapter use; keep their historical evidence and prepare a fresh release. Native controller
manifests and unrelated adapters keep their existing formats. No database migration, state migration,
address rename, grant change or automatic historical backfill is performed.

Current generic writers support exact owned local state or s3/gcs/azurerm remote metadata in the default
workspace. Unknown output values remain a runtime validation boundary; known unusable selections refuse
in preparation. Older writers do not enforce this contract. Historical inspection is retained, while
mutations should use the current writer consistently. Remote backend/provider qualification remains
separate from local metadata and native built-in-resource proof. See [configuration](configuration.md).

## Kubernetes connection/UID contract

New preparations carry a version 1 `kubernetes_request` and exact request digest. They bind selected
client connection, public TLS identity, tool version and existing object UID/version/container baseline.
The new writer refuses older Kubernetes mutation evidence before runner claim and native adapter use.
Historical records are not rewritten. Shared read-only prepared compatibility checks also project
legacy IaC/stack-deletion reasons into console review without changing their requests or audit.

A new public native journal preserves one-shot patch intent, acknowledgement and observation. Coherent
backup format1 admits this known journal while excluding credential homes; older strict readers may
refuse that added extent. Deployment inspection format2 explicitly refuses the new operation scope;
legacy summaries retain their meaning. No database migration, automatic namespace/resource adoption,
cluster creation, kubeconfig edit, permission or host trust change is introduced. See [Kubernetes](kubernetes.md).

## Later observations and responder handoff

Saved observations gain a returned sequence/digest reference without changing their stored detail or
original delivery outcome. New observation-origin incidents initially hold native pages. Explicit
handoff binds the active route and incident version, then queues a distinct event. Existing incidents
retain their opening behavior. There is still one incident per release, with no replacement or replay
of resolved episodes. Old quiet decisions never become automatic catch-up work.

Use one matching runtime version for all writers and stop older paging workers before upgrading.
There is no cross-version writer lock. Older workers do not enforce the new handoff/fallback admission
rules. Existing outbox rows are not rewritten or backfilled, and existing in-flight remote outcomes
still require inspection. Back up state first and retain the matching package for read-only recovery.

## Bounded Slack review

New cards render the full normalized review over bounded native pages and expose approval only on the
last page. Read-only continuation binds the request digest and displayed review digest. Restart review
if its content changes. Upgrade the callback consumer with the renderer so new continuation actions are
understood. Existing request bytes, audit rows, exact-digest decision buttons and the separate execution
contract are unchanged. No reading-progress ledger or retroactive human-review claim is introduced.

The correction includes the registered public isolated-IaC planning packet, which the first
bounded renderer omitted. Other public manifest fields now come from the existing controller contract
rather than a second field list. Target identity stays visible on each page. Earlier source
and review results remain historical. Restart an incomplete review after upgrading.

## Source capture evidence

New creations write a version 1 `deploy-ops.source-capture` marker in their newly reserved private root.
Existing release/request/database formats and older releases without markers remain supported. The
marker does not create a release, grant or alternate work queue. Current CLI and console readers work
without loading the inspected location's workspace. Unsupported marker versions refuse.

The private backup extent adds the allowlisted marker filename. Unregistered partial source folders
remain explicitly omitted. Use the matching current runtime to inspect/restore that extent: an older
backup reader can refuse the newly supported filename. Restoration stays read-only.

## Private runtime-cache observations

New writers retain optional per-component private-cache observations through existing release/event
storage. Older releases without them have no recorded cleanup qualification; no backfill or new
database table is introduced. Existing backup/artifact allowlists still exclude private homes while
retaining ordinary public release metadata. An existing runtime home with no native cleanup record is not adopted or removed; inspect it
through the original operator before further native use. Stop older writers before upgrade; their silent cache
removal cannot maintain these observations. Primary source/artifact/approval contracts are unchanged.
