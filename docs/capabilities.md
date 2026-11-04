# Capabilities

This table describes the current preview implementation. "Implemented" describes source behavior. "Live" means it actually ran
in this build environment. Mocked provider commands are not cloud integration evidence.

| Capability | Current behavior | Evidence or limit |
|---|---|---|
| Separate infrastructure and app repositories | Frozen inputs, DAG order, public output references | Live local path and committed-source tests |
| Independent app deployment | Reuses successful infrastructure outputs | Live local and ECS paths; cross-worker receipts use fixtures |
| Environments and overrides | Provider, production flag, variables, component target options | Schema and engine checks |
| Local deployment | Directory infrastructure and versioned static artifact deployment | Live filesystem and HTTP checks |
| Build and test | Trusted local argv checks/build hooks | Live Python example, no shell interpolation |
| OCI build/publish | Docker Buildx metadata produces immutable image reference | Command-contract checks; registry build/publish not exercised |
| Docker Compose | Rendered digest-pinned images, bounded host features, health wait | Real Docker/Compose first and app-only delivery, HTTP and health checks |
| Terraform/OpenTofu | Saved plan, tool and lock checks, state contract, named public outputs | Existing gate tests and new command-contract fixtures |
| CloudFormation | Native change-set preview, exact execution, state checks, public outputs, events and owned-stack deletion | Live private S3 create/update/no-change/delete and additional command fixtures; preview requires scoped cloud authority |
| AWS CDK | Frozen assembly, selected stack, file/zip assets and the CloudFormation lifecycle | Command fixtures; synthesis is external, no bootstrap mutation or Docker/executable asset builds |
| ARM/Bicep | Frozen JSON and public parameters, manual what-if review, fresh prediction before incremental deployment, named-operation readback | ARM fixtures and real Bicep v0.41.2 sample compilation; no live Azure or native resource destruction |
| AWS ECS app | Existing service, frozen task definition, exact image, durable mutation/readback, waiter and revision check | Live infrastructure-to-app and second app-only update; container health, image and startup log verified |
| Azure Container Apps | Existing Single-mode app, revision health check | Mocked commands, no live Azure |
| GCP Cloud Run | Existing single-traffic service, new revision, explicit 100% traffic switch | Mocked commands, no live GCP |
| Kubernetes app | Existing Deployment, JSON patch precondition, rollout and image checks | Real disposable kind cluster: rollout, app-only, promotion, rollback and stale-target refusal |
| Promotion and rollback | Reuse previously built static artifact or immutable image | Real static and local Kubernetes paths; cloud runtime paths remain unqualified |
| Generic tool integration | Trusted local prepare/apply/verify argv contract, always manual | Implemented, not a native tool semantic guarantee |
| Exact approvals and bounded windows | Shared authority controller and transactional claims | Regression and delivery tests |
| Slack approvals | Signature, workspace and approver validation | Mocked interface tests, no live Slack workspace |
| Notifications | Durable outbox to JSON webhook, Slack webhook, or ntfy | Mocked HTTP retries and stable event IDs |
| Local incidents | Explicit opening, owner label, versioned acknowledgement/resolution and bounded escalation signals | Durable/concurrent CLI/API checks and real browser; no external pager or on-call schedule |
| External paging | PagerDuty and GoAlert, named destinations, ordered routing, durable fan-out, explicit per-destination actions and foreground worker | Real browser, protocol fixtures and local TLS transport; no live SaaS page or remote callback/readback |
| Local GUI | Token unlock, config editor, graph preview, run, approval, local delegation, events, promotion and rollback | API tests and real headless Chrome journey on macOS, including keyboard unlock and narrow layout |
| Guided configuration | Schema-derived existing-entry forms, effective environment options for native IaC, comment-preserving TOML drafts, diff and stale-save refusal | Shared configuration model; unsupported adapter forms and new entries remain TOML |
| Appearance | System mode, six presets, validated import/export, density and radius | Real browser checks for drafts, storage failures and narrow layout |
| Catalog | Curated prior-art and integration metadata | Research only, no automatic installation or subscriptions |
| Cross-repo cloud coordination | Controller-issued AWS ECS/S3 and GCP Cloud Run/GCS receipts, pinned engines and independently approved app delivery | Durable restart, invalidation and two app releases checked with fixtures; native isolated GCS backend probes; no live cross-worker journey |
| Isolated static previews | Opt-in local namespaces, owner/expiry, exact manual cleanup, unknown/modified data retention | Local file, concurrency, recovery and browser/HTTP checks; no cloud/runtime cleanup |
| Local drift observation | Reviewed static files, active pointer and directory marker; records findings without repair | Actual local deployment tests; cloud, Compose and application health not observed |
| Canary, blue/green, SBOM enforcement | Research and phased plan | Not implemented |
| Pulumi, Helm/Kustomize, Argo CD and Flux | Can be invoked through a trusted manual local workflow where applicable | Native lifecycle and reconciliation adapters remain unfinished |

Production never allows unattended delegation in this version. The project is not limited to cloud use,
but local execution does not simulate the semantics of AWS, Azure, or GCP.

See [native infrastructure](native-infrastructure.md) for supported combinations, prerequisites,
mutation boundaries and recovery. A compiler result, preview or provider response alone does not prove
application health or a complete cloud journey.
