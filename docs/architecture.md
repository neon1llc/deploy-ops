# Architecture

## One deployment model

A workspace joins repositories, environments, components, and dependency outputs. A release freezes source revisions and configuration. Each component prepares evidence, requests authority, executes an exact reviewed artifact, verifies the result, and records public outputs.

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

`delivery/spec.py` owns the configuration contract, `delivery/engine.py` release state and dependency coordination, and `delivery/adapters/` tool-specific preparation and execution. `service.py`, `policy.py`, and `identity.py` own authorization, and `store.py` provides transactions and the audit chain. The CLI and console call the same operations. No approval or deployment logic is copied into JavaScript.

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

The local operator identity is labeled `local`, never a verified GitHub identity. A local token controls the trusted local workspace and isn't a multi-user boundary.

The remote delivery worker doesn't run repository build hooks, generic workflows, or IaC, because its GitHub job could mint writer credentials while untrusted code runs. The OpenTofu worker can issue typed AWS ECS output receipts. A separate app worker binds their digests into its approval, and the controller rechecks them at submission, claim, heartbeat, and completion. A later infrastructure claim permanently invalidates earlier receipts. See [infrastructure receipts](infrastructure-receipts.md).

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

SQLite records release progress, requests, claims, delegation use, outputs, audit links, and the notification outbox. Use one service process on local durable storage. Network filesystems and multi-replica scheduling aren't supported, and a stale execution lock is never cleared automatically.

An output receipt proves what DeployOps recorded, not that live infrastructure is unchanged. App-only runs reuse the last matching receipt and skip infrastructure discovery and drift reconciliation.
