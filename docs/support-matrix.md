# Support and qualification

These are bounded observations for the 0.8.0 preview, checked on 2026-11-03. A supported configuration
schema does not prove runtime or cloud compatibility. Versions outside this table may work but have not
been qualified here. No production reliability or security certification is claimed.

| Surface | Checked environment | Observed result | Remaining limits |
|---|---|---|---|
| Python package on macOS | Python 3.12.10, macOS 26.7.1 | Source checks, 177 tests, installed-wheel demo, static HTTP delivery and promotion | Other macOS/Python combinations |
| Python package on Linux | Python 3.14.4, Ubuntu 26.04, x86_64 | All 177 tests | Other distributions and architectures |
| CI | Ubuntu, Python 3.11 and 3.13 | Source/tests/demo; inspect each commit's workflow result | CI does not exercise live providers |
| Local browser console | Chrome 156.0.8078.80 on macOS | Auth, editing, approvals, delivery, promotion, recovery, appearance, focus and narrow layout | Safari, Firefox and assistive-technology audit |
| Controller container | Docker 29.1.3, Linux x86_64 | Build, nonroot/read-only startup, persistent state volume and HTTP health | HA, backup restoration and production ingress |
| Compose application | Docker 29.1.3, Compose 2.40.3 | First deployment, health wait, app-only update, HTTP content and cleanup | Image build/publish, promotion and production host policy |
| Kubernetes application | kind 0.31.0, Kubernetes/kubectl 1.35.0 | First rollout, app-only reuse, same-image promotion, rollback, stale-target refusal and reconciled retry | Managed clouds, admission controls, multi-node or multi-architecture behavior |
| AWS ECS, Azure Container Apps, GCP Cloud Run | Command fixtures | Exact command and selected refusal contracts | No live provider journey |
| OpenTofu receipt producer | 1.10.0 command/state fixtures | S3 backend binding, typed public outputs, receipt expiry/revocation and failed-apply invalidation | No live S3 or IaC apply |
| Slack and notification destinations | HTTP/interface fixtures | Signature/approver checks, stable event IDs and retry behavior | No live workspace or paging acknowledgement |
| Windows | None | Not qualified | Process supervision and filesystem behavior need testing |

The local runtime checks used a dedicated disposable VM without host mounts. The application examples
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
