# Setup

Start with a disposable development account or project. Keep your human administrator identity separate from the controller and from the deployment roles.

## 1. Run the local demo

Follow the installation commands in the [README](../README.md). `deploy-ops gate demo` needs no configuration or credentials. `deploy-ops gate --help` lists the real command interface.

The demonstration calls the controller's domain layer directly with synthetic identity records. There is no unauthenticated demo mode in the HTTP service.

## 2. Start one controller

```bash
cp config/controller.example.toml config/controller.toml
mkdir -p "$HOME/.config/deploy_ops"
deploy-ops gate secret --out "$HOME/.config/deploy_ops/operator.token"
export DEPLOY_OPS_ADMIN_TOKEN="$(cat "$HOME/.config/deploy_ops/operator.token")"
deploy-ops gate config-check --config config/controller.toml
deploy-ops gate serve --config config/controller.toml
```

Replace the sample repository IDs, workflow pin, cloud identities, and Slack settings before using a real workflow. The sample configuration is structurally valid but cannot match a real pinned workflow. `config-check` validates its shape, not its account access.

Store the live configuration outside repositories that agents can edit. Keep its file and database permissions restricted to the controller operator. The operator token authorizes approvals and delegation. It does not belong in agent environments, repository secrets, chat messages, or cloud roles.

The service listens on `127.0.0.1:8787` by default. For remote Actions jobs and Slack, expose it through a TLS reverse proxy. Add only that hostname to `allowed_hosts`. Configure request limits, rate limits, and authentication log retention at the proxy. Do not log request bodies or authorization headers. The included server disables access logs and does not trust arbitrary forwarded headers.

For Docker Compose, set `database = "/state/deploy_ops.db"` in the live configuration, then run `docker compose up --build`. The sample Compose file still binds to loopback. This release supports one controller and a local SQLite volume, not multiple independently scaled instances or a shared network database.

## 3. Separate planning from execution

Each target registers a repository, immutable repository and owner IDs, a branch, a pinned reusable workflow, and two GitHub environments.

The planning environment gets a read identity, plus only the state-backend permissions that planning and locking require. The execution environment gets a separate, resource-scoped writer identity. Neither identity should administer IAM, grant new roles, disable audit logs, change account ownership, or modify the controller.

The [cloud examples](../integrations/clouds) show federation resources. Review their permissions and existing identity requirements before applying them with your human administrator. These templates have not been applied or validated against live cloud accounts in this release.

Use a separate target for personal AWS and neon1 AWS. There is no reason for them to share a writer role or an unattended work window.

## 4. Install the protected workflow

Publish the source privately first. Record its commit SHA. Replace `TOOLKIT_COMMIT_SHA` in [reusable-deploy.yml](../integrations/github/reusable-deploy.yml) with that reviewed source revision.

Install the configured workflow as `.github/workflows/deploy.yml` in the authority repository. Commit it, then pin its new workflow SHA in the controller configuration and in the application repository's [caller workflow](../integrations/github/caller.yml). The toolkit and workflow pins are different revisions on purpose. A commit cannot contain its own hash.

Allow the application repository to use the private reusable workflow and private composite action through GitHub's Actions access settings. Protect the authority repository and its dependency updates. Don't let the proposing agent push to its protected branch, change its rules, or approve its own changes.

Set `DEPLOY_OPS_URL` in both planning and execution GitHub environments. Their names must match the registered target. Commit a reviewed `.terraform.lock.hcl` in each configured infrastructure directory. The wrapper uses `init -lockfile=readonly` and does not upgrade providers.

The sample workflow only runs for a private application repository because it transfers a raw saved plan through Actions artifacts. Public repository support needs a separately protected or encrypted artifact transport. The HTTP approval protocol can be used for that integration, but this archive does not supply an encrypted transport.

## 5. Pin cloud trust to the workflow

Do not authorize a writer role using only a repository name or branch. Another job in that repository could then skip DeployOps.

The [subject template](../integrations/github/subject-template.json) includes immutable IDs, the branch, environment context, and the reusable workflow. Applying it changes the repository's OIDC subject format and can affect existing federation. Review the migration before changing an existing repository.

Use `deploy-ops gate inspect-identity` in the actual pinned reusable workflow to inspect verified, non-secret claims. Configure AWS and Azure with the **observed exact subject**. Don't guess it from old examples. GitHub's subject format can include immutable IDs in the repository segment. GCP can check the individual claims directly. Its example uses a separate identity pool for each stage to avoid sharing a broad impersonation binding.

Update cloud trust and controller configuration deliberately whenever the trusted workflow pin changes. The repository cannot change either of them on its own.

## 6. Connect Slack

Create a Slack app using [manifest.yaml](../integrations/slack/manifest.yaml), with your HTTPS callback URL. Install it in your workspace and invite it to the intended private review channel.

Set `DEPLOY_OPS_SLACK_SIGNING_SECRET` and `DEPLOY_OPS_SLACK_BOT_TOKEN` in the controller's secret store or process environment. Set the workspace ID, app ID, channel ID, and allowed Slack user IDs in the administrator-owned configuration. Map each approver to their numeric GitHub user ID.

A pending request produces an approval card. Buttons authorize the exact request digest. Requests are checked for a valid Slack signature, recent timestamp, the expected app and workspace, and an allowed member.

```text
/deploy-ops pending
/deploy-ops show REQUEST_ID
/deploy-ops delegate neon1-aws-dev 6 3
/deploy-ops revoke DELEGATION_ID
/deploy-ops stop
/deploy-ops resume
```

The delegation command means six hours and at most three claimed executions. Resource types, action types, change counts, and estimate allowances still come from the protected target policy. It cannot grant arbitrary permissions from chat.

Slack self-approval is rejected when the approver's GitHub ID matches the workflow initiator. Use a distinct GitHub App identity for agent-initiated work. A self-reported agent label does not turn your own GitHub account into another identity. The operator-token CLI remains a single-maintainer bootstrap path without individual identity or step-up authentication.

Notifications are best effort. A failed delivery creates an audit event and never grants approval. Use the operator CLI to inspect requests when Slack is unavailable. Durable delivery retries and a passkey-protected approval page are not implemented.

## 7. Enable a bounded work window

The sample target disables delegation. Enable it only after checking the writer role's resource scope, provider lock, source review process, and failure behavior.

The default policy also requires a monthly cost estimate. The reference planning workflow does not invent one. It waits for a human unless your trusted workflow supplies current Infracost JSON through `deploy-ops gate plan --cost-file PATH`, or you deliberately turn off the estimate requirement and rely on the other limits.

An estimate is not a hard spending cap. Resource count limits do not limit desired task counts, runtime, egress, or every usage-based charge. Keep cloud role restrictions, resource quotas, monitoring, and billing alerts outside the agent's control.

```bash
export DEPLOY_OPS_URL=https://deploy-ops.example.com
deploy-ops gate delegate neon1-aws-dev --hours 6 --uses 3
deploy-ops gate delegations
deploy-ops gate pause
```

`pause` stops new authorization, revokes work windows, and invalidates active execution authority. `resume` does not restore old grants. Production targets cannot enable delegation in this release.

## 8. Reconcile an interrupted execution

A runner heartbeat failure, expired lease, revoked delegation, or operator pause can leave an execution `uncertain`. The target remains locked. Inspect the actual cloud resources, the IaC state lock, and any operation already accepted by the provider before resolving it.

```bash
deploy-ops gate list
deploy-ops gate status REQUEST_ID --operator
deploy-ops gate resolve REQUEST_ID --outcome failed \
  --note "Checked the cloud operation and state. No apply is still running"
deploy-ops gate audit --out audit-export.json
```

Never treat process termination as rollback. Plan again against the current infrastructure after reconciliation. Archive audit exports and the verified head hash in storage the controller and agents cannot rewrite.
