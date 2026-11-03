# Capabilities

This table describes version 0.7.0. "Implemented" describes source behavior. "Live" means it actually ran
in this build environment. Mocked provider commands are not cloud integration evidence.

| Capability | Current behavior | Evidence or limit |
|---|---|---|
| Separate infrastructure and app repositories | Frozen inputs, DAG order, public output references | Live local path and committed-source tests |
| Independent app deployment | Reuses a successful infrastructure receipt | Live local path |
| Environments and overrides | Provider, production flag, variables, component target options | Schema and engine checks |
| Local deployment | Directory infrastructure and versioned static artifact deployment | Live filesystem and HTTP checks |
| Build and test | Trusted local argv checks/build hooks | Live Python example, no shell interpolation |
| OCI build/publish | Docker Buildx metadata produces immutable image reference | Implemented, Docker unavailable here |
| Docker Compose | Rendered digest-pinned images, bounded host features, health wait | Implemented, runtime not exercised |
| Terraform/OpenTofu | Saved plan, tool and lock checks, state contract, named public outputs | Existing gate tests and new command-contract fixtures |
| AWS ECS app | Existing service, clone task definition, exact image, waiter, revision check | Mocked commands, no live AWS |
| Azure Container Apps | Existing Single-mode app, revision health check | Mocked commands, no live Azure |
| GCP Cloud Run | Existing single-traffic service, new revision, explicit 100% traffic switch | Mocked commands, no live GCP |
| Kubernetes app | Existing Deployment, JSON patch precondition, rollout and image checks | Mocked commands, no live cluster |
| Promotion and rollback | Reuse previously built static artifact or immutable image | CLI/API and browser static paths tested, runtime paths need live tests |
| Generic tool integration | Trusted local prepare/apply/verify argv contract, always manual | Implemented, not a native tool semantic guarantee |
| Exact approvals and bounded windows | Shared authority controller and transactional claims | Regression and delivery tests |
| Slack approvals | Signature, workspace and approver validation | Mocked interface tests, no live Slack workspace |
| Notifications | Durable outbox to JSON webhook, Slack webhook, or ntfy | Mocked HTTP retries and stable event IDs |
| Paging escalation | External incident system can consume webhooks | Escalation, acknowledgement, and on-call schedule logic are not implemented |
| Local GUI | Token unlock, config editor, graph preview, run, approval, local delegation, events, promotion and rollback | API tests and real headless Chrome journey on macOS, including keyboard unlock and narrow layout |
| Guided configuration | Schema-derived existing-entry forms, comment-preserving TOML drafts, diff and stale-save refusal | Shared CLI/API tests; provider options and new entries remain TOML |
| Appearance | System mode, six presets, validated import/export, density and radius | Real browser checks for drafts, storage failures and narrow layout |
| Catalog | Curated prior-art and integration metadata | Research only, no automatic installation or subscriptions |
| Cross-repo cloud coordination | Controller-issued typed AWS ECS receipts, pinned S3 backend and independently approved app delivery | Durable restart and two app releases checked with command fixtures; no live AWS |
| Preview environments and cleanup | Planned contract and acceptance criteria | Not implemented |
| Local drift observation | Reviewed static files, active pointer and directory marker; records findings without repair | Actual local deployment tests; cloud, Compose and application health not observed |
| Canary, blue/green, SBOM enforcement | Research and phased plan | Not implemented |
| Pulumi/CDK/Bicep/Argo CD/Flux | Can be invoked through a trusted manual local workflow | Native isolated adapters and cloud semantics not implemented |

Production never allows unattended delegation in this version. The project is not limited to cloud use,
but local execution does not simulate the semantics of AWS, Azure, or GCP.
