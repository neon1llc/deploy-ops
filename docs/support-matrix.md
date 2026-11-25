# Support and qualification

Stage: local developer preview, not production-supported. The [README](../README.md#status) summarizes this table.

The [v1.0.0 exit criteria](v1-readiness.md) compare these claims with the release bar.

Bounded observations from v0.10.0 through v0.12.0 and later source checks, checked on 2026-11-03. A supported schema doesn't prove runtime or cloud compatibility. Versions outside this table may work but aren't qualified. No production reliability or security certification is claimed.

| Surface | Checked environment | Observed result | Remaining limits |
|---|---|---|---|
| Python package on macOS | Python 3.12.10, macOS 26.7.1, x86_64 | v0.14.0 on 2026-11-13: fresh venv install from the README, 653 tests, and the demo from an editable install and an installed wheel, including static HTTP delivery and promotion | Other macOS and Python combinations |
| Python package on Linux | Python 3.14.4, Ubuntu 26.04, x86_64 | v0.7 runtime base: all 177 tests | Later incident/preview tests not rerun on Python 3.14. Other distributions and architectures |
| CI | Ubuntu and macOS, Python 3.11 and 3.13 | At `3bb77d6`, all four Linux/macOS test jobs passed, both pipx wheel and source install demos passed, and dependency and secret scans passed in the [hosted run](https://github.com/neon1llc/deploy-ops/actions/runs/45665267490). The macOS test step passed but remains non-blocking after an earlier hosted `PermissionError` while signaling a process group | Repeat hosted runs before removing the macOS soft failure. CI does not exercise live providers |
| Local browser console | Chrome 156.0.8078.80 on macOS | Auth, editing, approvals, delivery, promotion, recovery, appearance, focus, narrow layout | Safari, Firefox and assistive-technology audit |
| Controller container | Docker 29.1.3, Linux x86_64 | v0.7 image build, nonroot/read-only startup, persistent state volume and HTTP health | HA, backup restoration and production ingress |
| Compose application | Docker 29.1.3, Compose 2.40.3 | First deployment, health wait, app-only update, HTTP content and cleanup | Image build/publish, promotion and production host policy |
| Kubernetes application | kind 0.31.0, Kubernetes/kubectl 1.35.0 | First rollout, app-only reuse, same-image promotion, rollback, stale-target refusal and reconciled retry | Managed clouds, admission controls, multi-node or multi-architecture behavior |
| AWS ECS | AWS CLI 2.36.34, Fargate Linux/X86_64, us-east-1 | Live infrastructure-to-app and app-only image update. Healthy task/digest/logs, unchanged infrastructure | No public HTTP endpoint, image publication or cross-worker live receipt |
| Azure Container Apps | Azure CLI 2.91.0, Consumption, eastus | Live infrastructure-to-app and app-only update, revision health and version logs | No ingress HTTP proof. Cleanup completed beyond the planned lifetime. New recovery journal only fixture-qualified |
| GCP Cloud Run | Native v2 API fixtures | Conditional candidate/traffic operations, actual image/readiness and read-only recovery | No live workload journey qualified |
| Native IaC tooling | OpenTofu 1.10.0, Terraform 1.12.2 in isolated Linux VM | Offline init/validate/saved-plan/show of `terraform_data` | No cloud provider plan or apply |
| OpenTofu receipt producer | 1.10.0 command/state fixtures | S3 backend binding, typed public outputs, receipt expiry/revocation and failed-apply invalidation | No live S3 or IaC apply |
| CloudFormation | AWS CLI 2.36.34, us-east-1. Additional command fixtures | Live private S3 create, tag update, no-change and owned deletion. Independent bucket-absence check | Other resources, CDK assets and live rollback remain unqualified |
| AWS CDK assembly | Python 3.12 and Node.js 24.19.0, aws-cdk-lib 2.275.0, constructs 10.8.1, schema 55 | Python and TypeScript synthesis under filesystem and network restrictions. Both assemblies consumed through fixture CloudFormation | No live asset publication, bootstrap change, Docker asset build or cloud stack |
| ARM deployment | Azure CLI 2.91.0 certificate route | Live frozen Bicep create and raw ARM tag update. Unchanged infrastructure during app releases | Native DeployOps destruction unsupported. Operator cleanup completed after a provider deletion overrun |
| Bicep compilation | Standalone v0.41.2 (3e403ea7c1), macOS x86_64 | Sample compiled with network and home reads denied. Both example workspaces checked | Does not qualify permissions, Azure state, cost or application health |
| Isolated static previews | Local filesystem, SQLite and Chrome | Owned namespace, manual cleanup, retained data, recovery, HTTP 200 to 410 | No cloud/runtime teardown or shared data ownership |
| Local incidents | Local SQLite, CLI/API and Chrome | Durable decisions, bounded escalation, concurrent outbox, browser workflow | No live pager or recipient scheduling |
| Slack and notification destinations | HTTP/interface fixtures | Signature/approver checks, stable event IDs and retry behavior | No live workspace or paging acknowledgement |
| GCP receipt producer | OpenTofu 1.10.0, Terraform 1.12.2, Google provider 6.50.0 | Locked validation, offline GCS backend override, controller and consumer fixtures | No live GCP apply or GitHub workflow |
| Paging | Six native protocol fixtures, Chrome and local TLS receiver | Routing, per-destination actions, Better Stack readback, durable claims, unknown-outcome handling | No live SaaS delivery |
| Isolated GCP recipe | OpenTofu 1.10.0, Terraform 1.12.2, Google 6.50.0, dedicated Lima guest | Offline validation, immutable inputs, supervised process cleanup, CLI/API and receipt fixtures | Live IAM/plan/apply and new browser panel unqualified. Native destroy unsupported |
| Windows | None | Not qualified | Process supervision and filesystem behavior need testing |

The container, Compose, and Kubernetes evidence qualifies the v0.7 execution base and the v0.8 examples. Later changes have local tests and CI, but this matrix wasn't rerun for each. Runtime checks used a disposable VM without host mounts. The examples use public digest-pinned Python images, which establish nothing about vulnerability status, provenance, or licensing. Keep Docker socket and kubeconfig access scoped. The local worker is for trusted source and isn't a sandbox for hostile build or IaC programs.
