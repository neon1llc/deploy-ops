# Architecture

## One deployment model

A workspace joins repositories, environments, components, and dependency outputs. A release freezes its
source revisions and configuration. Each component prepares evidence, requests authority, executes an exact
reviewed artifact, verifies the result, and records public outputs.

```mermaid
flowchart TD
  Human[Developer or identified agent] --> Trigger[CLI, local UI, or trusted GitHub workflow]
  Trigger --> Workspace[Operator-owned workspace contract]
  Workspace --> Graph[Dependency order and environment selection]
  Graph --> Source[Freeze separate repository inputs]
  Source --> Build[Checks and build in an unprivileged path]
  Build --> Plan[Prepare adapter-specific evidence]
  Plan --> Policy[Independent authorization controller]
  Policy -->|Manual| Review[Exact-change approval]
  Policy -->|Bounded window| Grant[Check scope, expiry, and remaining allowance]
  Review --> Claim[One-time execution claim]
  Grant --> Claim
  Claim --> Apply[Apply the saved plan or immutable artifact]
  Apply --> Verify[Provider or local verification]
  Verify --> Outputs[Public outputs and deployment receipt]
  Outputs --> Next[Next dependent component]
  Verify --> Audit[Audit, events, notification outbox]
```

## Owned code and external tools

`delivery/spec.py` owns the configuration contract. `delivery/engine.py` owns release state and dependency
coordination. `delivery/adapters/` owns tool-specific preparation and execution. `service.py`, `policy.py`,
and `identity.py` own authorization. `store.py` provides transactions and the audit chain. The CLI and console
call the same operations. No approval or deployment logic is copied into JavaScript.

External tools perform their own specialized work. Terraform/OpenTofu manage their provider graph and
state. Docker builds and runs containers. AWS, Azure, GCP, and Kubernetes retain their own rollout semantics.
DeployOps does not emulate clouds or claim one provider switch can translate an application's architecture.

## Current execution boundaries

```mermaid
flowchart LR
  subgraph Local[Trusted local operator]
    UI[Loopback UI or CLI] --> LE[Delivery engine]
    LE --> LG[Local authority mode]
    LE --> Files[Local sources, builds, and deployments]
  end
  subgraph Remote[Reviewed isolated workflow]
    CI[GitHub OIDC identity] --> Gate[HTTPS authority service]
    Gate --> Lease[Time-limited execution lease]
    Lease --> Native[Native app rollout adapter]
    Native --> Cloud[Scoped cloud identity]
  end
  subgraph IaC[Retained split-job IaC path]
    ReadJob[Planning job and read identity] --> PlanArtifact[Saved plan]
    PlanArtifact --> Approval[Independent approval]
    Approval --> WriteJob[Separate execution job and identity]
  end
```

The local operator identity is labelled `local`, never represented as verified GitHub identity.
A local token grants control of the trusted local workspace. It is not a multi-user security boundary.

The remote delivery worker does not execute repository-defined build hooks, generic workflows, or IaC.
Its GitHub job could otherwise mint writer credentials while untrusted preparation code runs.
Cross-repository cloud orchestration must compose separate workers and durable receipts. That is an explicit
next phase, not a hidden claim that the current local scheduler already supplies it.

## State and interruption

```mermaid
stateDiagram-v2
  [*] --> queued
  queued --> preparing
  preparing --> waiting: approval required
  preparing --> running: valid delegation
  waiting --> running: exact approval and claim
  running --> succeeded: verification confirmed
  running --> uncertain: failure, cancellation, or lost authority
  preparing --> uncertain: interrupted command
  waiting --> cancelled: operator cancels
  uncertain --> failed: operator inspects and reconciles
  succeeded --> [*]
  failed --> [*]
  cancelled --> [*]
```

SQLite records release progress, requests, claims, delegation use, outputs, audit links, and the notification
outbox. Use one service process on local durable storage. Network filesystems and multi-replica scheduling
are not supported. A stale execution lock is deliberately not cleared automatically.

An output receipt proves what DeployOps recorded, not that the live infrastructure remains unchanged.
Application-only runs intentionally reuse the last successful matching-contract receipt. They do not run
infrastructure discovery or drift reconciliation. Review that receipt or deploy infrastructure again when needed.
