# Security

DeployOps is a local developer preview, not production-supported. It has not had an independent security audit, and its cloud paths are only partly live-checked. See the [support matrix](docs/support-matrix.md).
Keep account-owner credentials and untrusted agent control outside the controller, policy files,
deployment workflow, runner host and operator token. Trusted local qualification can use explicit native
credential references under an expiring operator grant. Those checks do not reduce the source identity’s
provider permissions; administrator keys remain administrator keys. See [local cloud access](docs/local-cloud-access.md).

## Boundaries that exist

The controller verifies GitHub OIDC identity and pinned workflow claims. Exact approvals bind artifact,
source, environment, and policy evidence. Claims and delegation counters are transactional. Workers check
leases online. Uncertain executions block the target until an operator inspects and reconciles it.
Production delegation is disabled. The local console uses a random bearer token, loopback binding,
Host and Origin checks, no CORS, no browser token storage, and a restrictive content security policy.

Infrastructure receipts bind typed public outputs to an authenticated producer execution and the current
registered policy. Consumers need matching environment, account and region. Exact references are rechecked
online. These records are not offline signatures or fresh observations of out-of-band backend changes.
A later infrastructure claim invalidates earlier evidence even if the apply fails.

## Boundaries that do not exist

Clearing inherited environment variables is not a sandbox. Arbitrary source code, a malicious provider,
a compromised CLI, or a process running as the same OS user may read files, inspect processes, call the
network, or abuse permissions. Local working-tree mode is only for trusted code. Use isolated runners,
separate OS identities, restricted network egress, and least-privilege provider roles for higher-risk work.

A GitHub job with cloud-trusted OIDC authority can request cloud credentials independently of an application
approval unless its trusted execution path or cloud trust prevents that. Do not run untrusted tests or build
hooks in such a job. The remote delivery engine refuses those hooks and directs IaC to separate plan/apply
jobs. This does not make a compromised trusted workflow safe.

Temporary cloud tokens remain usable until their provider expiry. Cancelling a local process, revoking a
grant, or expiring a lease does not instantly revoke those tokens or undo asynchronous cloud operations.
Provider-native policy, narrow resource scope, and independent audit are still required.

A hash-linked local audit ledger detects edits to an observed chain. An attacker who controls the database
can rewrite a complete chain. Export checkpoints to an independently protected system for stronger evidence.
SQLite is single-node state, not a distributed scheduler. Back up it and the artifact store consistently.

Cost estimates and delegation counters are not hard spending limits. New resource types are manual unless
explicitly allowed by server policy. There is no blanket overnight approval mode.

## Inputs, logs, and integrations

Do not place secrets in TOML, public output bindings, source commits, or notification URLs intended for logs.
Saved IaC plans and provider state may contain secrets even when outputs are marked sensitive. Snapshots and
artifacts are private local files and must not be published. Command output redaction is best effort, not a
secret-classification guarantee. Unknown provider errors are intentionally not copied into the audit log.

Slack approval identity is checked by workspace, app, and configured member mapping. Notification webhooks
are transport, not approval authority. Configure only operator-approved destinations. Package installation,
marketplace terms, and paid subscriptions require a separate explicit decision.

## Reporting and operation

Report a suspected issue privately to the maintainer through the repository's available private reporting
channel. Do not assume GitHub security reporting is enabled until the repository is configured.
Capture the version, affected path, reproduction, and expected boundary without publishing live credentials.
Stop affected workers, revoke grants, remove provider trust where appropriate, and inspect CloudTrail or the
provider's audit system before replaying a release. Provider-side cleanup requires its own authorization.
