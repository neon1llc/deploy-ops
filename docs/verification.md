# Verification

**Checked:** 2026-11-03. The local and browser observations below qualify the v0.10.0 baseline.
Later sections record the v0.11.0–v0.12.0 increments and subsequent checks separately.

## Local execution

A fresh network install of the pinned requirements passed on macOS with Python 3.12.10.
`make check` passed source, configuration, documentation-link and whitespace checks and all 207 tests.
The tests cover approval and claim boundaries, concurrency, identity validation, changed evidence,
source snapshots, promotion, rollback, process cleanup, notification retries and console access.
A complete delivery and app-only release also ran from two committed Git repositories while uncommitted
application changes remained excluded.
Provider commands and federation responses in these tests are fixtures.

A fresh local demo deployed infrastructure, delivered the application independently, and promoted its
unchanged artifact to staging. The promoted page returned HTTP 200 with the expected content.
The same demo, schema and catalog commands passed from an independently installed wheel.

Focused review reproduced and repaired unreviewed files under previously excluded artifact directories,
cancellation after approval, and descendants surviving their command's exit. Regression checks exercise
those failures. Prepared evidence now includes every file, while working-tree exclusions apply only
before source copying. Local runtime state is also excluded from the container build context.
Promotion and rollback authenticate the original prepared envelope before reading its source or artifact
metadata. Rewriting both an artifact and its metadata is refused before a new approval can be requested.

## Browser and packaging

A headless Chrome 156.0.8078.80 session exercised the actual loopback console: invalid-token refusal,
keyboard unlock, configuration validation and save, workflow preview, two exact approvals, deployment,
pending cancellation, bounded delegation, app-only delivery, event history and lock.
Promotion and rollback preserved the original static artifact through new approvals. Missing dependency
receipts were explained without offering an executable control, and polling retained keyboard focus. Browser storage
contained no credentials. The 390-pixel layout had no horizontal overflow. JavaScript syntax also passed.
Receipt input errors preserved the draft, local mode refused remote-only receipt authority, and a separate
fixture console displayed producer, backend, state and validity evidence without exposing raw-state secrets.

Appearance checks exercised all six presets, system preference changes, retained explicit choices, density,
corner radius, unsaved workspace drafts, invalid and low-contrast import refusal, cross-tab updates,
appearance-only export and storage, reset, and blocked-storage fallback. The narrow layout was inspected
visually. Appearance settings use their own storage key and do not contain operator credentials.

Guided configuration checks covered schema-derived controls, form drafts retained across panels, preserved
TOML comments and advanced settings, explicit diff review, no automatic save, raw-editor conflicts, late
responses, backups and stale-tab refusal. CLI/API draft parity, dependency validation and concurrent
release/save boundaries passed focused tests. The editor does not translate provider-specific options.

Cancellation during preparation produced an uncertain outcome. After inspecting events and the unchanged
deployment pointer, explicit reconciliation allowed further work. That result does not prove that a
cancelled cloud operation has stopped.

Both the source distribution and wheel built successfully. Their contents retained Apache-2.0, NOTICE,
the prior MIT notice and the bundled theme MIT notice. The installed wheel includes the console, local starter and catalog resources.
The source distribution includes shared test fixtures, check commands, configuration examples and user
documentation so the documented checks can run from the extracted archive.
Pinned requirements are version snapshots, not an independently audited dependency lock or SBOM.

## Local drift observations

Actual filesystem checks covered unchanged deployment, edited and missing files, changed pointers, valid
supersession, activation order differing from creation order, history beyond 200 releases, unsafe entries,
unreadable or tampered evidence and concurrent execution. Observations preserved original release status
and deployed bytes. They record a bounded comparison, not application health or an automatic repair.

## Disposable container and cluster execution

The v0.7 runtime base passed all 177 tests on Linux with Python 3.14.4. Real execution used Docker Engine 29.1.3,
Compose 2.40.3, kind 0.31.0 and Kubernetes/kubectl 1.35.0 in a disposable Ubuntu 26.04 VM with no host
filesystem mounts or SSH agent forwarding. The existing host Docker context and other VMs were unchanged.

Compose deployed a digest-pinned, nonroot application, waited for health, then delivered an app-only
content update without reapplying the foundation. HTTP returned 200 with each expected body. The exact
[Compose example](../examples/local-compose/README.md) also passed both releases and scoped cleanup.

Kubernetes updated an existing Deployment, reused foundation evidence for an app-only image change,
promoted the same image to staging and rolled development back through a new approval. Both scoped
Services returned HTTP 200. A real API mutation between preparation and apply caused the resource-version
precondition to fail; the image remained unchanged. Explicit reconciliation followed by a fresh request
succeeded. The [Kubernetes example](../examples/local-kubernetes/README.md) passed first delivery, app-only
reuse, same-image promotion and both HTTP probes using an explicit test cluster context override.

The controller Docker image built from the v0.7 source used for runtime qualification. It served `/healthz` successfully as UID 10001,
with a read-only root filesystem, dropped capabilities, a persistent state volume and no Docker socket.
These are local runtime observations, not proof of cloud identity, production isolation, registry publishing,
sustained availability or managed-cluster compatibility. See the [support matrix](support-matrix.md).

## Local incident lifecycle

Fourteen focused checks cover restart, duplicate opening, immutable policy, stale decisions, direct
resolution, concurrent escalation, acknowledgement racing escalation, notification supersession,
concurrent delivery outcomes and the transactional attempt limit. A forced out-of-order delivery sent
resolved version 2 before opened version 1; a receiver applying only newer versions retained resolution.
This demonstrates the receiver contract with a fixture, not an external pager integration.

A real browser opened an incident from a deliberately failed local preparation, retained investigation
notes and keyboard focus through polling, checked an actual 60-second escalation deadline, acknowledged
and resolved the incident. The original release outcome remained unchanged. The narrow layout and empty
credential storage checks passed. HTTP transport success does not acknowledge an incident.

## Isolated static preview cleanup

Sixteen focused checks exercise isolated deployment, exact cleanup authority, modified/unknown data
retention, symlink and artifact refusal, expiry, ordinary policy compatibility, partial cleanup and lost
completion. Independent review reproduced two recovery races; the shared ledger now refuses live-worker
recovery and atomically acquires recovery ownership. Both reproductions were rerun successfully. An
ownership tombstone remains after cleanup, avoiding an interruption window that could lose ownership proof.

The real browser deployed an isolated preview, reviewed and separately approved cleanup, preserved an
added unknown data file and the ownership marker, and retained the original release outcome/evidence.
The static server returned HTTP 200 before cleanup execution and HTTP 410 afterwards. Preparing and
approving cleanup did not delete files or stop serving. Narrow layout and empty credential storage passed.
An actual v0.9 pending ordinary request was approved and completed under v0.10 with unchanged workspace
and target policy digests. These are local static checks, not cloud or runtime teardown qualification.

## Native infrastructure

CloudFormation/CDK contract fixtures exercise exact change-set execution, changed target/state/evidence,
no-change, rollback outcomes, ownership-bound deletion, retained data and file-asset checks. They use
synthetic provider responses and do not contact AWS.

A separate live test used AWS CLI 2.36.34 in us-east-1 through the actual DeployOps engine. One empty
private, encrypted S3 bucket passed stack creation, a tag update, no-change handling and owned deletion.
CloudFormation reported DELETE_COMPLETE; an independent bucket read returned 404. No bucket or objects
remain from that batch. This qualifies that storage lifecycle, not ECS, CDK assets or arbitrary templates.
The live run used the native implementation before the final default protected-type policy update;
source and regression checks cover the final candidate separately.

The Python and TypeScript CDK examples both synthesized with `aws-cdk-lib` 2.275.0, `constructs` 10.8.1
and Node.js 24.19.0. Python used 3.12. The execution boundary denied network access and home-directory
reads except the selected Node runtime, and confined writes to a disposable directory. Sentinel and network
checks confirmed those restrictions. The adapter consumed both real schema-55 assemblies through fixture
AWS preparation and execution. This is native synthesis plus command-contract evidence, not live AWS.

ARM fixtures exercise frozen template/parameter binding, tenant/subscription/principal mismatch,
changed what-if results, unsupported analysis, explicit access-change refusal, secure-input refusal,
lost responses and readback without replay. Destruction and complete-mode deployment are refused.
These checks do not establish Azure authentication or provider deployment success.

The self-contained Bicep example compiled using the standalone v0.41.2 compiler (3e403ea7c1) on macOS
x86_64 with no external module restore. The official release asset's SHA-256 was checked before use.
A repeated compilation with network access and home-directory reads denied and writes confined to a
disposable directory produced the same parsed template.
Both Azure example workspaces and their equivalent template resource/output contracts passed local
validation. Compilation does not qualify Azure access, permissions, cost, resource state or application health.

Native source comments and grouping were reviewed alongside the implementation. Syntax, fixtures,
compiler execution and browser/provider qualification remain distinct evidence categories.

The full v0.11.0 candidate suite passed 296 tests. Source syntax, local links and whitespace checks passed.
The focused native suite passed 60 cases, including lost-response ownership recovery. A real Chrome session
against synthetic provider responses exercised inherited native options, retained drafts, exact CloudFormation
and ECS approvals, provider readback, app-only output reuse and a narrow layout. Browser local storage remained
empty. These browser interactions use the shared core, but the provider responses remain fixtures.

## Live ECS application journey

AWS CLI 2.36.34 in us-east-1 created a disposable thirteen-resource CloudFormation stack through
DeployOps v0.11.0, including its own network and one Fargate Linux/X86_64 task (0.25 vCPU, 0.5 GiB).
The task had no inbound access or public service endpoint. Native ECS health and a startup log verified
the initial immutable image running Python 3.13.18.

DeployOps v0.11.1 then completed two independent application releases using the retained infrastructure
outputs. The second changed the image to Python 3.14.10; native task health, image digest and startup
log matched. The CloudFormation template and last-update timestamp remained unchanged across those
app releases. This qualifies native image rollout and container health, not custom image build/publish,
public HTTP delivery, cross-worker receipt exchange or production reliability.

Owned stack deletion completed and independent reads confirmed the VPC, network interfaces, gateway,
logs and execution role were absent. The two app-created task revisions were deregistered and submitted
for native deletion; AWS retained nonbillable DELETE_IN_PROGRESS metadata. The entire batch, including
cleanup checks, took under seventeen minutes.

The ECS mutation journal now records registration intent, the returned task revision and activation
intent before proceeding. It disables native mutation retries and supports bounded tagged-revision
discovery after a lost registration response. Six focused recovery cases cover lost acknowledgements,
changed evidence and replay refusal; the full v0.11.1 snapshot passed 302 tests.

## Isolated native tooling

OpenTofu 1.10.0 and Terraform 1.12.2 initialized, validated, saved and decoded a real `terraform_data`
plan in a dedicated Linux VM. The unprivileged source worker had no host mounts, cloud configuration,
SSH agent, sudo or Docker socket, and ran in an offline network namespace. Both plans predicted one
resource creation; neither was applied. A separate guest Docker 26.1.5/buildx 0.13.1 process built a
scratch fixture with network access disabled. These checks establish native local tooling behavior,
not a cloud provider plan, credential isolation for a remote job or a registry publication.

## GCP receipts, Azure access and paging

The v0.12.0 candidate passed 417 tests and repository syntax/link/whitespace checks. GCP receipt checks
cover both pinned engines, exact scope/backend/output binding, failed-apply invalidation and independent
app-only reuse. Real offline GCS backend probes replaced hostile source endpoint/key settings before
credential use; locked Google provider 6.50.0 validation passed under both engines. No GCP deployment
or real GitHub OIDC exchange is claimed from these checks.

Azure CLI 2.91.0 completed certificate service-principal login, exact tenant/subscription/client checks
and an authorized resource-group read. The product’s corresponding native-grant route passed full
Engine fixtures for ARM and Container Apps, including mismatched scope and unsafe certificate locations.

Paging passed 26 focused cases and a real Chrome journey covering two native destinations, routing
drafts, preview, activation, independent acknowledgement/resolution, failed-draft retention and a
390-pixel layout. Browser delivery was intercepted by a local fixture. A real local TLS receiver
exercised transport deadlines and delayed connection-close bodies; no external recipient was paged.

## Azure application journey

The installed v0.12.0 package used Azure CLI 2.91.0 to create a disposable Container Apps environment
and app from frozen Bicep output, then update infrastructure tags through raw ARM. Independent app
releases reused the infrastructure outputs and changed an immutable Python image from 3.13.18 to
3.14.10. Native revision health and startup logs matched both versions. The ARM deployment template
hash and timestamp stayed unchanged during application releases. The app had no ingress, used the
Consumption profile and requested 0.25 CPU and 0.5 GiB with one replica.

An initial read-only what-if failed because the native template requires a null log destination instead
of the CLI label `none`. Independent reads confirmed no submission or created resources. The corrected
Bicep template passed native compilation before a fresh release. This failed preparation remains part
of the evidence.

Owned cleanup removed the application. The environment remained `ScheduledForDelete` beyond the
30-minute batch deadline, then disappeared after roughly 47 minutes from batch start. An empty resource
group and an independent exact-resource Not Found response confirmed cleanup. The pre-existing API
Center remained present. This records a provider cleanup overrun, not compliance with the planned lifetime.
The new Container Apps mutation journal and exact-revision recovery checks were added after this live
run. Local fixtures and retained native response shapes cover them; live uncertain-write recovery
remains unqualified.

## Isolated GCP and six native paging targets

The v0.13.0 candidate passed 585 tests and the 189-file source checker. The isolated GCP
route uses the same controller for separately approved planning, saved-plan execution and application
receipt reuse. Actual credentialless VM checks covered native initialization and validation, immutable
inputs, distinct worker identities, supervisor exclusion and descendant termination on EOF, deadline
or heartbeat loss. No real GCP token, plan, apply or application rollout was exercised by those checks.
The console panel has shared API coverage; its new browser interactions still require verification.

A subsequent bounded live attempt authenticated native impersonation and published two immutable
images to its private registry, but did not establish a successful infrastructure plan. Both planning
requests failed during host transport cleanup and were reconciled after confirming no Cloud Run
service, empty infrastructure state and no remaining worker. No apply or application release followed.
Credentialless reproduction identified macOS refusing a process-group signal while its exited Lima
leader remained unreaped. The transport now reaps that leader first, retaining descendant cleanup and
genuine permission failures. Both native exit cases and all 589 tests passed after the fix. The fixed
transport still needs a fresh bounded live deployment qualification.

Native paging now includes PagerDuty, GoAlert, incident.io, Better Stack, Grafana Cloud IRM and ilert.
Provider-shaped fixtures cover each supported lifecycle and distinguish accepted delivery from observed
remote state. Local browser fixtures exercised the guided configuration and supported transitions.
No external on-call destination was contacted. Grafana Cloud IRM and incident.io do not offer an
acknowledgement action through their selected webhook contracts; ilert acceptance does not establish
remote lifecycle state.

## Remaining qualification

GCP workloads, Slack, registry build/publish and cloud Terraform/OpenTofu journeys remain unqualified. GCP service-account authentication succeeded through
native ADC and the actual DeployOps gcloud process. Project and billing reads succeeded after approved
enablement of the Cloud Resource Manager and Cloud Billing APIs. No GCP workload was created.
Installed command availability alone does not establish working integrations. GitHub Actions results
must be inspected separately from local execution.

This focused review is not a production security audit, penetration test, sustained reliability test or
proof of safety against a compromised runner. Controller-backed receipt exchange passed durable-restart, authenticated-consumer and two-release fixtures,
including expiry, revocation, policy changes, failed-apply invalidation and backend binding. A real supervised
subprocess exercised extraction with a synthetic OpenTofu executable. These checks do not qualify OpenTofu
against S3 or an isolated GitHub-to-AWS journey. Raw state secrets stayed out of receipts and audit records.
Existing workspace digests remain compatible when local cloud access is absent. Native protected-type
defaults change policy fingerprints; obtain fresh approvals as described in [migration](migration.md). See [security](../SECURITY.md) and
[capabilities](capabilities.md) for current boundaries.

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
