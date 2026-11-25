# v1.0.0 exit criteria

DeployOps is not ready for `v1.0.0` until the complete local workflow, one real cloud path, approval binding, recovery, and unaided use have current evidence.

## Exit criteria

| Area | Evidence required to exit |
|---|---|
| Clean install | The documented pipx install and built wheel install work on Linux and macOS with Python 3.11 and 3.13. Each installed command completes the local demo without cloud credentials. Windows remains unsupported until tested. |
| Local delivery | A developer completes setup, review, exact approval, delivery, app-only update, promotion, rollback, inspection, and recovery through the CLI and browser console. Failure, partial, and uncertain outcomes remain inspectable and do not replay mutations. |
| Primary cloud path | One AWS workflow provisions infrastructure with Terraform, releases an application artifact, and verifies the running result. It records exact identity, target, artifact digest, approval, recovery, and bounded cost. No unrelated infrastructure changes are allowed. |
| Approval binding | Tests and review show that changed source, artifact, target, identity, policy, or relevant state cannot reuse an approval. No open approval-binding defect remains. |
| Security review | A threat model and independent review cover approval binding, artifact digests, Kubernetes identity, and Cloud Run identity. High-impact findings are fixed or explicitly resolved before release. |
| Contracts and migration | The API, request format, and ledger schema have versioned contracts. A migration guide covers changes, and supported records remain readable or have a safe migration path. |
| Outside use | Three people outside neon1 each complete a delivery without help. Their problems are fixed or documented as limits before release. |
| Support claims | The support matrix names the exact checked versions, paths, and limits. The README, package metadata, and security page make no broader support claim than current evidence. |

## Gap table against `main`

Baseline: `origin/main` at `24f5becd490b8014c02c5fe4c8adf52f62471c5f`, fetched November 25, 2026. The support matrix is the claim inventory. Its cloud and runtime observations are historical unless a row says otherwise.

| Support claim | Current evidence in the matrix | Gap to v1.0.0 |
|---|---|---|
| Python package on macOS | A fresh venv install and wheel demo passed on Python 3.12 for v0.14.0. | Repeat the documented pipx and wheel paths on the current source and supported Python versions. |
| Python package on Linux | Historical Python 3.14 test results plus current CI on Python 3.11 and 3.13. CI installs and demos the wheel. | Current clean-install evidence is limited to CI. Keep a reproducible local result for the release candidate. |
| CI | Linux and macOS run source checks, tests, and the demo. macOS test failure is allowed after a hosted process-group permission failure. | Make the Node runtime explicit. Remove the macOS soft failure only after the process-group failure is fixed and repeated hosted runs pass. |
| Browser console | A historical Chrome journey covers editing, approvals, delivery, promotion, recovery, and appearance. | Repeat a current browser journey for the complete CLI and console workflow. Safari and assistive technology remain unqualified. |
| Controller container | The v0.7 image passed startup, identity, read-only filesystem, and health checks. | Recheck the current package and document the exact supported use. HA, restore, and production ingress remain unsupported. |
| Compose application | A historical Docker run covers deployment, health wait, app-only update, HTTP content, and cleanup. | Reproduce against the current release path. Image publication, promotion, and production host policy remain unqualified. |
| Kubernetes application | A historical kind run covers first rollout, app-only reuse, promotion, rollback, stale-target refusal, and reconciled retry. | Reproduce against the current release path. Managed clouds, admission controls, and multi-node behavior remain unqualified. |
| AWS ECS | Live delivery and app-only image updates ran against an existing Fargate service. | The planned AWS path still needs a fresh Terraform apply, published app artifact, exact approval, runtime verification, and tested recovery. |
| Azure Container Apps | Bounded live infrastructure-to-app and app-only updates ran. Cleanup exceeded the planned lifetime. | Keep the cleanup overrun and lack of ingress HTTP proof visible. This does not complete the v1 AWS path. |
| GCP Cloud Run | One bounded nonproduction run is documented and was not reproduced independently. Fixture checks cover later conditional operations. | No current live workload journey is qualified. Do not describe Cloud Run as supported. |
| Native IaC tooling | Offline OpenTofu and Terraform initialization, validation, saved-plan, and show steps passed. | No cloud provider plan or apply is qualified. AWS Terraform apply is still open. |
| OpenTofu receipt producer | Command and state fixtures cover backend binding, outputs, expiry, revocation, and failed-apply invalidation. | No live S3 or IaC apply is qualified. |
| CloudFormation | One private S3 create, tag update, no-change, and owned deletion ran live. | Other resource types and live rollback remain unqualified. |
| AWS CDK assembly | Python and TypeScript examples synthesized; fixture CloudFormation consumed both assemblies. | No live asset publication, bootstrap change, Docker asset build, or cloud stack is qualified. |
| ARM deployment | Bounded live Bicep create and raw ARM tag update ran. Cleanup completed after a provider deletion overrun. | DeployOps-driven ARM deletion is unsupported. Keep the cleanup delay visible. |
| Bicep compilation | The standalone compiler validated both example workspaces offline. | Compilation does not qualify Azure access, permissions, cost, resource state, or application health. |
| Isolated static previews | Local filesystem, SQLite, and Chrome checks cover ownership, retained data, recovery, and HTTP 200 to 410. | Repeat the current user path and cleanup recovery after installing the release candidate. |
| Local incidents | Local SQLite, CLI/API, and Chrome checks cover decisions, escalation, outbox behavior, acknowledgement, and resolution. | No live pager or recipient scheduling is qualified. |
| Slack and notification destinations | HTTP and interface fixtures cover signature checks, stable event IDs, and retry behavior. | No live workspace or paging acknowledgement is qualified. |
| GCP receipt producer | Locked offline validation and controller/consumer fixtures cover state and output binding. | No live GCP apply or GitHub workflow is qualified. |
| Paging | Provider fixtures and local receivers cover routing, retries, acknowledgement, and unknown outcomes. | No external recipient or live SaaS delivery is qualified. |
| Isolated GCP recipe | Offline guest validation and CLI/API fixtures cover inputs, worker supervision, and receipt flows. | Live IAM, plan, apply, browser operations, and native destroy remain unqualified. |
| Windows | No runtime evidence. | Remains unsupported until process, filesystem, install, and recovery behavior is tested. |
| Approval and recovery | High coverage exercises exact artifact reuse, changed evidence refusal, interrupted execution, and local reconciliation. | Independent security review is incomplete. Confirm no approval-binding defect remains and carry the tests through the selected AWS path. |
| API, request, and ledger stability | Current code and migration notes exist. | Freeze versioned contracts and publish the migration path before `v1.0.0`. |
| User outcome | No outside-user completion evidence is listed. | Three outside users must complete a delivery without help. |

## Release decision

The current evidence supports a local developer preview with partial cloud observations. It does not meet the v1.0.0 bar. The main release blockers are the current AWS Terraform-to-application journey, independent approval-binding review, frozen contracts, and outside-user completions. Do not run cloud tests without separate authority for the target, spend, and cleanup.
