# Support and qualification

These are bounded observations for the v0.10.0 baseline through v0.12.0 and subsequent source checks,
checked on 2026-11-03. A supported configuration
schema does not prove runtime or cloud compatibility. Versions outside this table may work but have not
been qualified here. No production reliability or security certification is claimed.

| Surface | Checked environment | Observed result | Remaining limits |
|---|---|---|---|
| Python package on macOS | Python 3.12.10, macOS 26.7.1 | Source checks, 207 tests, installed-wheel demo, static HTTP delivery and promotion | Other macOS/Python combinations |
| Python package on Linux | Python 3.14.4, Ubuntu 26.04, x86_64 | v0.7 runtime base: all 177 tests | Later incident/preview tests not rerun on Python 3.14; other distributions and architectures |
| CI | Ubuntu, Python 3.11 and 3.13 | Source/tests/demo; inspect each commit's workflow result | CI does not exercise live providers |
| Local browser console | Chrome 156.0.8078.80 on macOS | Auth, editing, approvals, delivery, promotion, recovery, appearance, focus and narrow layout | Safari, Firefox and assistive-technology audit |
| Controller container | Docker 29.1.3, Linux x86_64 | v0.7 image build, nonroot/read-only startup, persistent state volume and HTTP health | HA, backup restoration and production ingress |
| Compose application | Docker 29.1.3, Compose 2.40.3 | First deployment, health wait, app-only update, HTTP content and cleanup | Image build/publish, promotion and production host policy |
| Kubernetes application | kind 0.31.0, Kubernetes/kubectl 1.35.0 | First rollout, app-only reuse, same-image promotion, rollback, stale-target refusal and reconciled retry | Managed clouds, admission controls, multi-node or multi-architecture behavior |
| AWS ECS | AWS CLI 2.36.34, Fargate Linux/X86_64, us-east-1 | Live infrastructure-to-app and app-only image update; healthy task/digest/logs, unchanged infrastructure | No public HTTP endpoint, image publication or cross-worker live receipt |
| Azure Container Apps | Azure CLI 2.91.0, Consumption, eastus | Live infrastructure-to-app and app-only update, revision health and version logs | No ingress HTTP proof; cleanup completed beyond the planned lifetime; new recovery journal only fixture-qualified |
| GCP Cloud Run | Command fixtures | Exact command and selected refusal contracts | No live provider journey |
| Native IaC tooling | OpenTofu 1.10.0, Terraform 1.12.2 in isolated Linux VM | Offline init/validate/saved-plan/show of `terraform_data` | No cloud provider plan or apply |
| OpenTofu receipt producer | 1.10.0 command/state fixtures | S3 backend binding, typed public outputs, receipt expiry/revocation and failed-apply invalidation | No live S3 or IaC apply |
| CloudFormation | AWS CLI 2.36.34, us-east-1; additional command fixtures | Live private S3 create, tag update, no-change and owned deletion; independent bucket-absence check | Other resources, CDK assets and live rollback remain unqualified |
| AWS CDK assembly | Python 3.12 and Node.js 24.19.0, aws-cdk-lib 2.275.0, constructs 10.8.1, schema 55 | Native Python/TypeScript synthesis under filesystem/network restrictions; both assemblies consumed through fixture CloudFormation | No live asset publication, bootstrap change, Docker asset build or cloud stack |
| ARM deployment | Azure CLI 2.91.0 certificate route | Live frozen Bicep create and raw ARM tag update; unchanged infrastructure during app releases | Native DeployOps destruction unsupported; operator cleanup completed after a provider deletion overrun |
| Bicep compilation | Standalone v0.41.2 (3e403ea7c1), macOS x86_64 | Sample compiled with network/home reads denied and writes restricted; equivalent template and both example workspaces checked | Does not qualify permissions, Azure state, cost or application health |
| Isolated static previews | Local filesystem, SQLite and Chrome | Owned namespace, manual cleanup, retained data, recovery and HTTP200 to410 | No cloud/runtime teardown or shared data ownership |
| Local incidents | Local SQLite, CLI/API and Chrome | Durable decisions, bounded explicit escalation, concurrent outbox and browser workflow | No live pager or recipient scheduling |
| Slack and notification destinations | HTTP/interface fixtures | Signature/approver checks, stable event IDs and retry behavior | No live workspace or paging acknowledgement |
| GCP receipt producer | OpenTofu 1.10.0, Terraform 1.12.2, Google provider 6.50.0 | Native locked validation and offline GCS backend override; typed controller/consumer fixtures | No live GCP apply or GitHub workflow |
| Paging | Six native protocol fixtures, Chrome and local TLS receiver | Guided routing, independent actions, Better Stack readback, durable claims and unknown-outcome handling | No live SaaS delivery; optional Apprise planned |
| Isolated GCP recipe | OpenTofu 1.10.0, Terraform 1.12.2, Google 6.50.0, dedicated Lima guest | Native offline validation, immutable inputs, supervised process cleanup, shared CLI/API and receipt fixtures | Live IAM/plan/apply and new browser panel unqualified; native destroy unsupported |
| Windows | None | Not qualified | Process supervision and filesystem behavior need testing |

The container, Compose and Kubernetes evidence qualifies the v0.7 execution base and the v0.8 runnable
examples. Later incident and preview changes have their own local tests/browser checks and per-commit CI;
the container/cluster matrix has not been rerun for every later increment. The local runtime checks used a
dedicated disposable VM without host mounts. The application examples
use public digest-pinned Python images; pinning binds identity but does not establish vulnerability status,
provenance or licensing of every image layer. Container engines, kind, kubectl and images are installed
separately and are not included in the Python distribution.

Source distributions include the [Compose](../examples/local-compose/README.md) and
[Kubernetes](../examples/local-kubernetes/README.md) examples and their explicit prerequisite/cleanup steps.
The wheel includes the default local static starter. Keep runtime access scoped: a Docker socket grants
substantial host control, and a kubeconfig grants whatever its selected identity permits. The local worker
is intended for trusted source; it is not a sandbox for hostile build or IaC programs.

See [verification](verification.md) for detailed evidence and [capabilities](capabilities.md) for unfinished
product scope. Later commits must rerun checks affected by their changes.
