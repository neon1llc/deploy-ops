# Security

DeployOps is a local developer preview, not production-supported. It has had no independent security audit, and its cloud paths are only partly live-checked. See the [support matrix](docs/support-matrix.md).

Keep account-owner credentials and untrusted caller control out of the controller, policy files, deployment workflow, runner host, and operator token. Trusted local qualification can use explicit native credential references under an expiring operator grant. That doesn't reduce the source identity's provider permissions, so administrator keys stay administrator keys. See [local cloud access](docs/local-cloud-access.md).

## Boundaries that exist

- The controller verifies GitHub OIDC identity and pinned workflow claims.
- Exact approvals bind artifact, source, environment, and policy evidence.
- Claims and delegation counters are transactional. Workers check leases online.
- An uncertain execution blocks its target until an operator inspects and reconciles it.
- Production delegation is disabled.
- The local console uses a random bearer token, loopback binding, Host and Origin checks, no CORS, no browser token storage, and a restrictive content security policy.
- Infrastructure receipts bind typed public outputs to an authenticated producer execution and the current registered policy. Consumers need a matching environment, account, and region. Exact references are rechecked online. Receipts are not offline signatures or fresh observations of out-of-band backend changes. A later infrastructure claim invalidates earlier evidence even if its apply fails.

## Boundaries that don't exist

- **No sandbox.** Clearing inherited environment variables isn't one. Arbitrary source code, a malicious provider, a compromised CLI, or a process running as the same OS user can read files, inspect processes, call the network, or abuse permissions. Local working-tree mode is only for trusted code. For higher-risk work, use isolated runners, separate OS identities, restricted network egress, and least-privilege provider roles.
- **A trusted GitHub job can bypass approval.** A job with cloud-trusted OIDC authority can request cloud credentials without an application approval unless its execution path or the cloud trust prevents it. Don't run untrusted tests or build hooks in such a job. The remote delivery engine refuses those hooks and sends IaC to separate plan and apply jobs. That doesn't make a compromised trusted workflow safe.
- **No token revocation.** Temporary cloud tokens stay usable until the provider expires them. Cancelling a local process, revoking a grant, or expiring a lease doesn't revoke them or undo asynchronous cloud operations. You still need provider-native policy, narrow resource scope, and independent audit.
- **A tamper-evident ledger, not a tamper-proof one.** The hash-linked local audit ledger detects edits to an observed chain. An attacker who controls the database can rewrite a whole chain, so export checkpoints to an independently protected system. SQLite is single-node state, not a distributed scheduler. Back it up together with the artifact store.
- **No hard spending limits.** Cost estimates and delegation counters aren't limits. New resource types are manual unless server policy explicitly allows them. There is no blanket overnight approval mode.

## Inputs, logs, and integrations

Don't put secrets in TOML, public output bindings, source commits, or notification URLs meant for logs. Saved IaC plans and provider state may hold secrets even when outputs are marked sensitive. Snapshots and artifacts are private local files. Don't publish them. Output redaction is best effort. Unknown provider errors are deliberately kept out of the audit log.

Slack approval identity is checked by workspace, app, and configured member mapping. Notification webhooks are transport, not approval authority. Configure only operator-approved destinations. Package installation, marketplace terms, and paid subscriptions need a separate explicit decision.

## Reporting and response

Report a suspected issue privately to the maintainer. GitHub private reporting isn't enabled on this repository yet, so don't assume it exists. Include the version, affected path, reproduction, and expected boundary, and leave out live credentials.

If you suspect a compromise, stop affected workers, revoke grants, remove provider trust where appropriate, and inspect CloudTrail or the provider's audit system before replaying a release. Provider-side cleanup needs its own authorization.
