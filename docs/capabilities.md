# Capabilities

"Implemented" means source behavior. "Live" means it ran in this build environment. Mocked provider commands aren't cloud evidence.

| Capability | Current behavior | Evidence or limit |
|---|---|---|
| Separate infrastructure and app repositories | Frozen inputs, DAG order, public output references | Live local path, committed-source tests |
| Independent app deployment | Reuses infrastructure outputs | Live local and ECS paths. Cross-worker receipts use fixtures |
| Environments and overrides | Provider, production flag, variables, target options | Schema and engine checks |
| Local deployment | Directory infrastructure, versioned static artifacts | Live filesystem and HTTP checks |
| Build and test | Trusted local argv checks and build hooks | Live Python example, no shell interpolation |
| OCI build and publish | Docker Buildx metadata gives an immutable image reference | Command-contract checks only. Registry publish not exercised |
| Docker Compose | Digest-pinned images, bounded host features, health wait | Real Docker first and app-only delivery, HTTP checks |
| Terraform/OpenTofu | Saved plan, tool and lock checks, public outputs | Gate tests and command fixtures |
| CloudFormation | Change-set preview, exact execution, public outputs, owned-stack deletion | Live private S3 create, update, no-change, and delete, plus command fixtures. Preview needs scoped cloud authority |
| AWS CDK | Frozen assembly, selected stack, file and zip assets, CloudFormation lifecycle | Command fixtures. Synthesis is external. No bootstrap or Docker asset builds |
| ARM/Bicep | Frozen JSON, public parameters, what-if review, incremental deployment, readback | Live Bicep creation and ARM tag update. No native destruction |
| AWS ECS app | Existing service, frozen task definition, exact image, readback, revision check | Live infrastructure-to-app and second app-only update. Container health, image, and startup log verified |
| Azure Container Apps | Single revision mode, one named container, revision health, mutation journal | Live image update and logs. Recovery journal checked with fixtures |
| GCP Cloud Run | Existing single-container/untagged route, conditional candidate and traffic updates, durable native operations and actual revision-image readback | Native API fixtures; live workload qualification remains separate. See [Cloud Run](cloud-run.md) |
| Kubernetes app | Existing Deployment, patch precondition, rollout and image checks | Real disposable kind cluster: rollout, app-only, promotion, rollback, stale-target refusal |
| Promotion and rollback | Reuse a built static artifact or immutable image | Real static and local Kubernetes paths. Cloud runtime paths unqualified |
| Generic tool integration | Trusted local prepare, apply, and verify argv, always manual | Implemented, no native tool guarantee |
| Exact approvals and bounded windows | Shared authority controller, transactional claims | Regression and delivery tests |
| Slack approvals | Signature and approver validation | Mocked tests, no live Slack workspace |
| Notifications | Durable outbox to JSON webhook, Slack, or ntfy | Mocked HTTP retries |
| Local incidents | Explicit opening, owner label, versioned acknowledgement and resolution, bounded escalation | Durable and concurrent CLI/API checks, real browser. No external pager or on-call schedule |
| External paging | PagerDuty, GoAlert, incident.io, Better Stack, Grafana Cloud IRM, ilert, durable routing, per-destination actions | Browser, protocol, and TLS fixtures, Better Stack readback fixtures. No live SaaS page |
| Local GUI | Token unlock, config editor, run, approval, delegation, promotion, rollback | API tests and real headless Chrome journey on macOS, including keyboard unlock and narrow layout |
| Guided configuration | Shared CLI/API drafts, directory and static creation controls, existing-entry and native IaC forms, diff, guarded save | CLI/API onboarding and DOM contracts. New creation controls still need authenticated browser, keyboard, and layout qualification |
| Appearance | System mode, six presets, validated import and export, density, radius | Real browser checks for drafts, storage failures, narrow layout |
| Catalog | Curated prior-art and integration metadata | Research only, no installation or subscriptions |
| Isolated GCP recipe | Fixed typed recipe, native VM execution, separate plan and apply approvals, app receipt reuse | Credentialless native and synthetic checks. No live journey or native destroy |
| Cross-repo cloud coordination | Controller-issued AWS ECS/S3 and GCP Cloud Run/GCS receipts, pinned engines | Fixture checks only. No live cross-worker journey |
| Isolated static previews | Opt-in local namespaces, owner and expiry, exact manual cleanup | Local file, recovery, and browser/HTTP checks. No cloud or runtime cleanup |
| Local drift observation | Reviewed static files, active pointer, directory marker. Records findings without repair | Local deployment tests. Cloud, Compose, and application health not observed |
| Canary, blue/green, SBOM enforcement | Research and phased plan | Not implemented |
| Pulumi, Helm/Kustomize, Argo CD, Flux | Invoke through a trusted manual local workflow | Native lifecycle and reconciliation adapters unfinished |

Production never allows unattended delegation in this version. Local execution doesn't simulate AWS, Azure, or GCP. A compiler result, preview, or provider response alone doesn't prove application health or a complete cloud journey. See [native infrastructure](native-infrastructure.md).
