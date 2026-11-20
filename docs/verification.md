# Verification

A dated ledger of what was run and what each run does not show. Fixture, source, and live provider checks are separate evidence.

Unless a section says otherwise, these are not qualified: mounted browser controls, keyboard and layout behavior, assistive technology, human acceptance, billed cost, and any provider or cloud behavior beyond what the section names. No grant, cloud batch, or publication changed.

## Private Cloud Run traffic qualification, 2026-11-05

One bounded, nonproduction Cloud Run journey ran from the installed local build against a separately provisioned official sample service (internal ingress, IAM protection, zero minimum instances, at most two small revisions).

- The CLI prepared an exact change. Browser approval left it waiting until a separate CLI resume.
- Both native operations completed. The candidate image matched and traffic converged to it.
- Browser and CLI inspection observed the result without replaying the deployment.
- A fresh approval and separate execution verified a same-image no-op with no native write.
- Readback counted exactly two revisions. Owned deletion completed, the service returned 404, and no revisions remained. The console and grant closed within the recorded lifetime.

Not qualified: application HTTP, external ingress, build and push, infrastructure-to-application execution, other runtime shapes. Earlier failed attempts and their cleanup remain separate evidence. The browser exposed a review gap: the UI showed a normalized update and digests without the target, image, or planned route steps.

## Private-cache primary-state correction, 2026-11-05

Cause: independent native-owner scheduling completed a waiting release before readback took its lock, and whole-release cache writes then lost the confirmed success and outputs. Fix: readback reloads and revalidates under actual ownership, and cache writes merge only cache metadata through the existing ledger transaction.

## Separate private cache cleanup outcomes, 2026-11-05

The sealed earlier Engine reproduced a harmless owned marker surviving a narrow unlink denial, with no cleanup observation and released locks. Current cases keep primary waiting, success, uncertainty, and readback, record a separate category failure, continue the other cache check, and refuse link and nonregular paths. Cache values and exception sentinels stay out of public results. Local synthetic-cache cases only, not credential retention, secure erasure, or provider tests.

## Waiting worker admission correction, 2026-11-05

Cause: a markerless record loaded before the worker lock could emit a duplicate after another worker migrated it. Advance now takes its native lock, then reloads and validates the release. Real-owner interleavings keep one migration event and outbox set and stop settled cancellation from being overwritten.

## Native configuration filename correction, 2026-11-05

Cause: the guard refused `.scratch.tf` although native Terraform ignored it. Root configuration selection now skips native hidden and editor names and follows OpenTofu extension precedence. OpenTofu behavior is source and fixture coverage, not an installed native-tool run.

## Automatic root IaC inputs, 2026-11-05

Cause: a native Terraform plan showed an ignored automatic variable file overriding the tracked default while the workdir check accepted the commit. Cases now refuse ignored or untracked root configuration, automatic variables, and lock files, plus a committed configuration symlink, before output creation, native tools, or credentials. Not covered: child-module or external-input provenance, a live provider case, race-proof isolation.

## Retained capture inspection correction, 2026-11-05

Cause: an obsolete failed inspection reached the global banner after a newer success, and a retry kept its old error. Both now use the same session, generation, and field binding. DOM cases cover late-error suppression and error replacement on success.

## Inspectable source capture attempts, 2026-11-05

A real second-source refusal copied the first source before stopping, with no registered release or controller request. Records now keep that phase and the first source's identity. Backup and restore keep only an unregistered attempt marker, omit partial source trees, and refuse writes through restored state. Local cases only.

## Completed command output accounting, 2026-11-05

Cause: real local children held at an exit barrier showed the old source accepting an excess diagnostic stream and an excess combined output. Terminal accounting now refuses both, including after a local marker effect and exit 0. Exactly 10 MB combined output stays usable. Local command evidence only, not cloud rollback, resource exhaustion, or an OS disk quota.

## Bounded Slack review, 2026-11-05

Cause: the renderer dropped the 25th deletion, resource type, and finding while still offering approval. Fixtures now keep all normalized review text over bounded pages, including 2000 rows and non-BMP Unicode, and final-page approval leaves execution unclaimed. A later isolated-IaC planning request exposed a missing public packet, and the renderer now shows every public manifest binding. Local protocol checks only. No Slack message was sent.

## Later evidence and responder handoff, 2026-11-05

A real local directory deployment succeeded, its marker changed, and it was observed as drifted. API checks opened an incident bound to that observation, held native pages, reviewed the route, and queued one explicit handoff to a local sender fixture. Delivery success stayed unchanged and quiet expiry produced no automatic page. A late-fallback race now rechecks local eligibility before starting a native page. Not established: remote acceptance, responder identity, acknowledgement, recovery.

A bounded desktop review found unclear release labels and lost search focus. Source now adds component names with a legacy fallback and restores search focus only when its own control was displaced. A local browser journey then covered incident opening, route preview, Escape cancellation, queued handoff, and local resolution. No destination was contacted.

## Kubernetes connection and object identity, 2026-11-05

kubectl v1.36.1 against an owned loopback TLS API fixture kept the original connection when the on-disk alias changed, rejected a recreated UID atomically, and sent one accepted PATCH whose acknowledgement was lost. Read-only inspection observed the result without replay. Raw native config stayed in memory and anonymous stdin.

The first fixture lacked watch-list initial bookmarks and timed out. Implementing the documented bookmark fixed it without changing product semantics or weakening TLS. No dedicated real cluster was available and the local Docker daemon was unavailable. Not qualified: Kubernetes controller, workload, RBAC, or managed-cluster behavior. The v0.7 VM proof below stays separate.

## Generic IaC readiness, 2026-11-05

Real Terraform 1.16.0 with `terraform_data` and private local state refused a known complex selected output before apply, applied a valid saved plan and validated its previously unknown public scalar output, and refused a changed initialized backend before apply. Local source overrides and backend selectors are not adopted silently. Remote backend shapes are fixtures, not remote storage, cloud identity, restore, or provider qualification.

## Complete approval effects, 2026-11-05

A real Terraform 1.16.0 local saved plan produced import plus update and move plus replacement, and normalization kept all four effect rows. Only the local baseline was applied. CloudFormation fixtures cover stack removal for empty or retained-only stacks, live retention, explicit and native-default RDS snapshots, independent snapshot grant limits, and legacy refusal before runner claim. Not live snapshot creation, restored snapshot usefulness, or billable-capacity qualification.

## Selected-file signatures and cleanup choices, 2026-11-05

A private Cosign v3.1.4 executable matched the official GitHub asset digest and release checksums. This is an HTTPS and checksum bootstrap, not independent authentication of the executable. A local-key bundle probe verified the official release and refused a different subject under a fresh HOME. No configured-proxy requests were seen, which is not OS-level network isolation.

The source integration froze an official 99,023,586-byte signed release file beside an unsigned neighbor. It verified the signed file and refused the neighbor under the same published key and artifact tree. Neither result implies whole-artifact trust, transparency, time, or revocation checks, or release-policy admission. Process fixtures are not cryptographic proof.

Cause of a cleanup prompt bug: blank and `removed` could not meet the dialog's ten-character minimum. The fix passed the dialog helpers in DOM tests, while short notes stayed blocked. Backend ownership checks and deletion authority are unchanged.

## Separate local blue-green operations, 2026-11-05

The source journey staged a static app without a foundation, measured candidate HTTP, held a separate switch approval, changed the active-target URL, measured the new route, and kept the old fixed revision. A fresh manual retirement request removed only reviewed unchanged old files, and modified and unknown data stayed. Changed candidates and baselines refused before claim.

Three exited-subprocess boundaries cover pointer replacement before receipt, interruption before controller completion, and controller completion before the final release update. Dead-owner inspection resolved incomplete authority without replay. A synthetic post-check failure kept an uncertain route and baseline capacity. Partial retirement needed reconciliation and a new inventory, not replay.

The DOM harness found a missing renderer timestamp helper, now fixed. Source, DOM, and HTTP checks only. No cloud, container, or weighted routing and no artifact-policy enforcement follow.

## Active-target local serving, 2026-11-05

Real loopback HTTP served a fixed revision and an active-target listener. Separate execution activated new bytes on the target URL while the fixed URL kept the original. Foreign, malformed, missing, and externally reverted pointers, uncertain completion, altered reviewed evidence, and a changed inventory returned 410. Static target routing alone does not qualify blue-green readiness, switch authority, post-check, or previous-capacity retirement.

## Retained static artifact observations, 2026-11-05

Native Syft 1.51.1 generated CycloneDX 1.7 for a synthetic artifact with one software package and one file component, counted separately. The collector also read the artifact delivered in the earlier Edge journey. Its one file of 332 bytes matched the retained `516ed16f…` subject digest, and Syft found no software packages. Trivy 0.74.0 was unavailable with the fresh offline advisory cache. Both results were retained. Empty inventory, unavailable scanning, and command success are not security admission.

A new Edge tab reached the token form, then browser control detached during token entry and stayed unavailable through a bounded recovery. Mounted artifact controls are not qualified. Trivy warns that third-party SBOM inputs can lose detection properties, so Syft-to-Trivy conversion is not a complete scan. This increment supplies observations, not OCI verification, trusted-signature enforcement, or a release-policy gate.

## Cloud Run native boundary, 2026-11-05

Cloud Run v2 API fixtures cover separate conditional candidate and traffic writes, lost acknowledgements at both boundaries, etag conflict, external route, configuration, and UID changes, no-op, and read-only inspection. Inspection format 2 adds Cloud Run scope and phase. A version 1 extent stays readable without reserialization.

Three native attempts used newly owned internal-only, IAM-protected sample services, an existing runtime identity, no secret data, and no IAM, API, billing, or public-access changes.

1. The first two stopped before an application write. They showed that parent-service names must be short and default false protobuf booleans must be omitted. The adapter and fixtures were fixed.
2. The third created a candidate with a completed native operation, then stopped before traffic switching. Readback showed `latestReadyRevision` can stay the serving baseline while the candidate receives no traffic. The guard now uses the candidate's own image and readiness. No fourth live attempt was made.

All three services were deleted, later GETs returned 404, and revision inventories were empty. The first cleanup receipt hit a helper error after deletion, and a separate native read confirmed deletion and zero revisions. Failed and uncertain records and operation IDs are retained. No success was fabricated.

The later private qualification above covers the narrow traffic, no-op, and mounted review and readback path.

## Private backup candidate and desktop browser, 2026-11-05

The candidate build's Edge connection covered interrupted Save inspection, Escape cancellation with focus return, current-file reconciliation keeping an unsaved TOML comment, and a warned reload that cleared the token. A backup captured 20 files and 39 audit events. Cancelled restore left its target absent, and confirmed relocated restore verified the same audit head and showed execution not admitted.

A fresh two-component release passed workflow preview, exact approval and separate execution for each component, and delivery. The browser produced the offline HTML inspection file, and the download-event helper timed out, so the file was verified separately. Its visual rendering was not checked. Candidate source was used, not an installed final wheel. Not qualified: narrow viewport, cloud browser operations, promotion, rollback.

## Waiting transition identity, 2026-11-05

Cause: fixtures reproduced four waiting events and both outbox obligations for one unchanged request. A durable request identity now keeps repeated checks and reconstruction quiet while keeping distinct next-component events, and a lost return after the waiting commit reopens without another transition. No reminder scheduler or sender was added.

## Local execution

A fresh install of the pinned requirements passed on macOS with Python 3.12.10. `make check` passed source, configuration, documentation-link, and whitespace checks and all 207 tests. A complete delivery and app-only release also ran from two committed Git repositories, with uncommitted application changes excluded. Provider commands and federation responses in these tests are fixtures. A local demo deployed infrastructure, delivered the application independently, and promoted its unchanged artifact to staging, which returned HTTP 200 with the expected content. The same demo, schema, and catalog commands passed from an installed wheel.

Focused review reproduced and fixed three failures: unreviewed files under previously excluded artifact directories, cancellation after approval, and descendants surviving their command's exit. Prepared evidence now includes every file. Promotion and rollback authenticate the original prepared envelope before reading its source or artifact metadata, and rewriting both an artifact and its metadata is refused before a new approval can be requested.

## Browser and packaging

Headless Chrome 156.0.8078.80 exercised the loopback console: invalid-token refusal, keyboard unlock, configuration validation and save, workflow preview, two exact approvals, deployment, pending cancellation, bounded delegation, app-only delivery, history, and lock. Promotion and rollback kept the original static artifact through new approvals. Browser storage held no credentials, and the 390-pixel layout had no horizontal overflow. Appearance checks covered all six presets, density, corner radius, import refusal for invalid and low-contrast themes, cross-tab updates, and blocked-storage fallback. Guided configuration checks covered preserved TOML comments, explicit diff review, no automatic save, raw-editor conflicts, and stale-tab refusal. The editor does not translate provider-specific options.

Cancellation during preparation produced an uncertain outcome. After inspecting events and the unchanged deployment pointer, explicit reconciliation allowed further work. That does not prove a cancelled cloud operation has stopped.

The source distribution and wheel both built and kept Apache-2.0, NOTICE, the prior MIT notice, and the bundled theme MIT notice. Pinned requirements are version snapshots, not an audited lock or SBOM.

## Local drift observations

Filesystem checks covered unchanged deployment, edited and missing files, changed pointers, valid supersession, history beyond 200 releases, unsafe entries, tampered evidence, and concurrent execution. They record a bounded comparison, not application health or automatic repair.

## Disposable container and cluster execution

The v0.7 runtime base passed all 177 tests on Linux with Python 3.14.4. Real execution used Docker Engine 29.1.3, Compose 2.40.3, kind 0.31.0, and Kubernetes and kubectl 1.35.0 in a disposable Ubuntu 26.04 VM with no host mounts or SSH agent forwarding.

Compose deployed a digest-pinned, nonroot application, waited for health, then delivered an app-only update without reapplying the foundation. The [Compose example](../examples/local-compose/README.md) also passed both releases and scoped cleanup.

Kubernetes updated an existing Deployment, reused foundation evidence for an app-only image change, promoted the same image to staging, and rolled development back through a new approval. A real API mutation between preparation and apply made the resource-version precondition fail and the image stayed unchanged. Explicit reconciliation and a fresh request then succeeded. The [Kubernetes example](../examples/local-kubernetes/README.md) passed first delivery, app-only reuse, same-image promotion, and both HTTP probes with an explicit test cluster context override.

The controller image built from the v0.7 source served `/healthz` as UID 10001 with a read-only root filesystem, dropped capabilities, a persistent state volume, and no Docker socket. Not proof of cloud identity, production isolation, registry publishing, sustained availability, or managed-cluster compatibility. See the [support matrix](support-matrix.md).

## Local incident lifecycle

Fourteen focused checks cover restart, duplicate opening, immutable policy, stale decisions, concurrent escalation, acknowledgement racing escalation, notification supersession, and the transactional attempt limit. A forced out-of-order delivery sent resolved version 2 before opened version 1, and a receiver applying only newer versions kept the resolution. A real browser opened an incident from a deliberately failed preparation, checked an actual 60-second escalation deadline, and acknowledged and resolved it. HTTP transport success does not acknowledge an incident, and no external pager was involved.

## Isolated static preview cleanup

Sixteen focused checks cover exact cleanup authority, modified and unknown data retention, symlink and artifact refusal, expiry, partial cleanup, and lost completion. Review reproduced two recovery races. The shared ledger now refuses live-worker recovery and takes recovery ownership atomically, and an ownership tombstone remains after cleanup. The real browser deployed an isolated preview, approved cleanup separately, and kept an added unknown data file. The static server returned HTTP 200 before cleanup and 410 afterwards. A real v0.9 pending request completed under v0.10 with unchanged policy digests. Local static checks, not cloud or runtime teardown.

## Native infrastructure

CloudFormation and CDK contract fixtures cover exact change-set execution, changed target, state, and evidence, no-change, rollback outcomes, ownership-bound deletion, and retained data. They use synthetic provider responses and do not contact AWS.

A live test used AWS CLI 2.36.34 in us-east-1 through the DeployOps engine. One empty private, encrypted S3 bucket passed stack creation, a tag update, no-change handling, and owned deletion. CloudFormation reported DELETE_COMPLETE, an independent read returned 404, and nothing remains. This qualifies that storage lifecycle, not ECS, CDK assets, or arbitrary templates. The run predates the final default protected-type policy update, which source and regression checks cover.

The Python and TypeScript CDK examples both synthesized with `aws-cdk-lib` 2.275.0, `constructs` 10.8.1, and Node.js 24.19.0 (Python 3.12). Network access and home-directory reads were denied except the Node runtime, and writes were confined to a disposable directory. The adapter consumed both schema-55 assemblies through fixture AWS preparation and execution. Not live AWS.

ARM fixtures cover frozen template and parameter binding, tenant, subscription, and principal mismatch, changed what-if results, access-change and secure-input refusal, lost responses, and readback without replay. Destruction and complete-mode deployment are refused. They do not establish Azure authentication or deployment success.

The Bicep example compiled with the standalone v0.41.2 compiler (3e403ea7c1) on macOS x86_64 after its SHA-256 was checked. A repeat compile with network access and home-directory reads denied produced the same parsed template. Both Azure example workspaces passed local validation. Compilation does not qualify Azure access, permissions, cost, resource state, or application health.

The v0.11.0 candidate suite passed 296 tests and the focused native suite passed 60 cases, including lost-response ownership recovery. Chrome against synthetic provider responses exercised exact CloudFormation and ECS approvals, provider readback, and app-only output reuse. The provider responses are fixtures.

## Live ECS application journey

AWS CLI 2.36.34 in us-east-1 created a disposable thirteen-resource CloudFormation stack through DeployOps v0.11.0, with its own network and one Fargate Linux/X86_64 task (0.25 vCPU, 0.5 GiB). The task had no inbound access or public endpoint. ECS health and a startup log verified the initial immutable image running Python 3.13.18.

DeployOps v0.11.1 then completed two independent application releases using the retained outputs. The second changed the image to Python 3.14.10. Task health, image digest, and startup log matched, and the CloudFormation template and last-update timestamp stayed unchanged. This qualifies native image rollout and container health, not custom image build and publish, public HTTP delivery, cross-worker receipt exchange, or production reliability.

Stack deletion completed and independent reads confirmed the VPC, network interfaces, gateway, logs, and execution role were gone. The two app-created task revisions were deregistered and submitted for deletion, and AWS kept nonbillable DELETE_IN_PROGRESS metadata. The batch took under seventeen minutes.

The ECS mutation journal now records registration intent, the returned task revision, and activation intent before proceeding. It disables native mutation retries and supports bounded tagged-revision discovery after a lost registration response. Six recovery cases cover lost acknowledgements, changed evidence, and replay refusal. The v0.11.1 snapshot passed 302 tests.

## Isolated native tooling

OpenTofu 1.10.0 and Terraform 1.12.2 initialized, validated, saved, and decoded a real `terraform_data` plan in a dedicated Linux VM. The unprivileged worker had no host mounts, cloud configuration, SSH agent, sudo, or Docker socket and ran in an offline network namespace. Both plans predicted one resource creation. Neither was applied. A guest Docker 26.1.5 and buildx 0.13.1 process built a scratch fixture with network disabled. Not a cloud provider plan, remote-job credential isolation, or registry publication.

## GCP receipts, Azure access, and paging

The v0.12.0 candidate passed 417 tests. GCP receipt checks cover both pinned engines, exact scope, backend, and output binding, failed-apply invalidation, and independent app-only reuse. Offline GCS backend probes replaced hostile source endpoint and key settings before credential use. Locked Google provider 6.50.0 validation passed under both engines. No GCP deployment or GitHub OIDC exchange is claimed.

Azure CLI 2.91.0 completed certificate service-principal login, exact tenant, subscription, and client checks, and an authorized resource-group read. The native-grant route passed Engine fixtures for ARM and Container Apps, including mismatched scope and unsafe certificate locations.

Paging passed 26 focused cases and a Chrome journey covering two native destinations, routing drafts, preview, activation, independent acknowledgement and resolution, and failed-draft retention. A local fixture intercepted browser delivery, and a local TLS receiver exercised transport deadlines and delayed connection-close bodies. No external recipient was paged.

## Azure application journey

The installed v0.12.0 package used Azure CLI 2.91.0 to create a disposable Container Apps environment and app from frozen Bicep output, then update infrastructure tags through raw ARM. Independent app releases reused the outputs and changed an immutable Python image from 3.13.18 to 3.14.10. Revision health and startup logs matched, and the ARM template hash and timestamp stayed unchanged. The app had no ingress, used the Consumption profile, and requested 0.25 CPU and 0.5 GiB with one replica.

An initial read-only what-if failed because the native template needs a null log destination instead of the CLI label `none`. Independent reads confirmed no submission or created resources. The corrected template passed native compilation before a fresh release. The failed preparation stays in the evidence.

Owned cleanup removed the application. The environment stayed `ScheduledForDelete` past the 30-minute batch deadline and disappeared about 47 minutes after batch start. An empty resource group and an exact-resource Not Found confirmed cleanup, and the existing API Center remained. This records a provider cleanup overrun, not compliance with the planned lifetime. The Container Apps mutation journal and exact-revision recovery checks were added after this run, covered by fixtures and retained native response shapes. Live uncertain-write recovery is not qualified.

## Isolated GCP and six native paging targets

The v0.13.0 candidate passed 585 tests and the 189-file source checker. The isolated GCP route uses one controller for separately approved planning, saved-plan execution, and application receipt reuse. Credentialless VM checks covered native initialization and validation, immutable inputs, distinct worker identities, supervisor exclusion, and descendant termination on EOF, deadline, or heartbeat loss. No GCP token, plan, apply, or rollout was exercised. A browser fixture with synthetic native responses covered recipe review, separate planning and saved-plan approvals, and receipt prefill into the application form.

A later bounded live attempt authenticated native impersonation and published two immutable images to its private registry, but produced no successful infrastructure plan. Both planning requests failed during host transport cleanup and were reconciled after confirming no Cloud Run service, empty infrastructure state, and no remaining worker. No apply or release followed. Credentialless reproduction found macOS refusing a process-group signal while the exited Lima leader stayed unreaped. The transport now reaps that leader first, keeping descendant cleanup and genuine permission failures. Both native exit cases and all 589 tests passed after the fix. The fixed transport still needs a fresh bounded live deployment. Readback confirmed removal of the temporary state bucket, registry, five service accounts, and six custom roles within the 30-minute batch. Enabled APIs, Google-managed service agents, and existing project log retention remain. No workload was created.

Native paging now includes PagerDuty, GoAlert, incident.io, Better Stack, Grafana Cloud IRM, and ilert. Provider-shaped fixtures cover each supported lifecycle and distinguish accepted delivery from observed remote state. No external on-call destination was contacted. Grafana Cloud IRM and incident.io offer no acknowledgement action through their selected webhook contracts. Ilert acceptance does not establish remote lifecycle state.

## Remaining qualification

Not qualified: GCP workloads, Slack, registry build and publish, cloud Terraform and OpenTofu journeys, OpenTofu against S3, an isolated GitHub-to-AWS journey. GCP service-account authentication succeeded through native ADC and the DeployOps gcloud process, and project and billing reads succeeded after approved enablement of the Cloud Resource Manager and Cloud Billing APIs. Installed command availability alone does not establish working integrations. GitHub Actions results must be inspected separately from local execution.

This review is not a production security audit, penetration test, sustained reliability test, or proof of safety against a compromised runner.

Controller-backed receipt exchange passed durable-restart, authenticated-consumer, and two-release fixtures, including expiry, revocation, policy changes, failed-apply invalidation, and backend binding. A supervised subprocess exercised extraction with a synthetic OpenTofu executable. Raw state secrets stayed out of receipts and audit records. Existing workspace digests stay compatible when local cloud access is absent. Native protected-type defaults change policy fingerprints, so get fresh approvals as described in [migration](migration.md). See [security](../SECURITY.md) and [capabilities](capabilities.md) for current boundaries.

## Reproduce

```bash
python -m pip install -r requirements-dev.txt -e .
make check
deploy-ops demo /tmp/deploy-ops-verification
deploy-ops gate config-check --config config/controller.example.toml
deploy-ops doctor
node --check src/deploy_ops/ui/app.js
```

Use a new demo directory each time. The command refuses to overwrite an existing workspace.

## Local target and configuration increment

On 2026-11-05 the local source suite passed 711 tests. Target regressions cover directory and static aliases, symlink changes after approval, repeated same-owner updates, foundation and application composition, two reservation processes, and nested state roots. Local configuration validation rejects the same malformed fields, paths, references, and empty executables through the schema, CLI, and API without running commands. A real pending request created by the installed v0.14.0 runtime was refused before claim by the new runtime. Its approval stayed approved and no local target was created.

Edge exercised retained name and build conflicts, refusal before conflict resolution, both resolution choices, save-impact review, equal pin and reset, and focus return. At 390 pixels the page width stayed 390. These were disposable draft operations without Save or provider execution.
