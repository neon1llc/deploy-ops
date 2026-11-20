# Setup

## Request compatibility

New requests use version 2 with optional self-reported `caller.label` and `caller.run` values. Verified `source.actor` and `source.actor_id` remain the authenticated identity. Caller labels grant no authority. The CLI exposes `--caller-label`, and legacy option spellings still work.

Upgrade the controller, client, and Slack consumer together before writing version 2 requests, since older controllers reject the new fields. Version 1 requests stay supported through the legacy `agent` contract. Existing manifests, digests, approvals, and audit records are never rewritten, and a changed request needs its own exact approval.

`deploy-ops gate audit --out PATH` verifies the exported rows before privately replacing the output file. Its count, chain head, format, and file hash describe that saved extent, and later appends don't invalidate a valid prefix. A fetch or validation failure keeps the previous file.

Start with a disposable development account or project. Keep your human administrator identity separate from the controller and the deployment roles.

## 1. Run the local demo

Install as in the [README](../README.md). `deploy-ops gate demo` needs no configuration or credentials, and `deploy-ops gate --help` lists the commands. Put the controller URL before the command, for example `deploy-ops gate --url http://127.0.0.1:8787 list`. It overrides `DEPLOY_OPS_URL`. HTTPS is required outside loopback, and operator commands still need the operator token. The demo calls the domain layer with synthetic identities. The HTTP service has no unauthenticated demo mode.

## 2. Start one controller

```bash
cp config/controller.example.toml config/controller.toml
mkdir -p "$HOME/.config/deploy_ops"
deploy-ops gate secret --out "$HOME/.config/deploy_ops/operator.token"
export DEPLOY_OPS_ADMIN_TOKEN="$(cat "$HOME/.config/deploy_ops/operator.token")"
deploy-ops gate config-check --config config/controller.toml
deploy-ops gate serve --config config/controller.toml
```

Replace the sample repository IDs, workflow pin, cloud identities, and Slack settings before using a real workflow. `config-check` validates shape, not account access.

Keep the live configuration outside repositories that callers can edit, with file and database permissions limited to the controller operator. The operator token authorizes approvals and delegation, so keep it out of build and caller environments, repository secrets, chat, and cloud roles.

The service listens on `127.0.0.1:8787`. For remote Actions jobs and Slack, expose it through a TLS reverse proxy and add only that hostname to `allowed_hosts`. Set request limits and log retention at the proxy, and don't log request bodies or authorization headers.

For Docker Compose, set `database = "/state/deploy_ops.db"`, then run `docker compose up --build`. This release supports one controller with a local SQLite volume.

## 3. Separate planning from execution

Each target registers a repository, immutable repository and owner IDs, a branch, a pinned reusable workflow, and two GitHub environments.

The planning environment gets a read identity plus only the state-backend permissions planning and locking need. The execution environment gets a separate, resource-scoped writer identity. Neither may administer IAM, grant roles, disable audit logs, change account ownership, or modify the controller.

The [cloud examples](../integrations/clouds) show federation resources. They haven't been applied or validated against live accounts. Use a separate target for personal AWS and neon1 AWS so they never share a writer role or work window.

## 4. Install the protected workflow

Publish the source privately and record its commit SHA. Replace `TOOLKIT_COMMIT_SHA` in [reusable-deploy.yml](../integrations/github/reusable-deploy.yml) with that reviewed revision.

Install the configured workflow as `.github/workflows/deploy.yml` in the authority repository. Commit it, then pin its new SHA in the controller configuration and in the application repository's [caller workflow](../integrations/github/caller.yml). The toolkit and workflow pins differ, since a commit can't contain its own hash.

Allow the application repository to use the private reusable workflow and composite action in GitHub's Actions access settings. Protect the authority repository and its dependency updates. The proposing agent must not push to its protected branch, change its rules, or approve its own changes.

The sample workflow runs only for private application repositories, since it moves a raw saved plan through Actions artifacts. Set `DEPLOY_OPS_URL` in both GitHub environments, named to match the registered target. Commit a reviewed `.terraform.lock.hcl` in each infrastructure directory. The wrapper uses `init -lockfile=readonly` and never upgrades providers.

## 5. Pin cloud trust to the workflow

Don't authorize a writer role with only a repository name or branch, or another job in that repository could skip DeployOps.

The [subject template](../integrations/github/subject-template.json) includes immutable IDs, the branch, environment context, and the reusable workflow. Applying it changes the repository's OIDC subject format and can break existing federation, so review the migration first.

Run `deploy-ops gate inspect-identity` in the pinned reusable workflow to see verified, non-secret claims. Configure AWS and Azure with the observed exact subject, not one guessed from old examples. GCP can check individual claims. When the workflow pin changes, update cloud trust and controller configuration deliberately.

## 6. Connect Slack

Create a Slack app from [manifest.yaml](../integrations/slack/manifest.yaml) with your HTTPS callback URL. Install it and invite it to the private review channel.

Set `DEPLOY_OPS_SLACK_SIGNING_SECRET` and `DEPLOY_OPS_SLACK_BOT_TOKEN` in the controller's secret store or environment. Set the workspace ID, app ID, channel ID, and allowed Slack user IDs in the administrator-owned configuration, and map each approver to a numeric GitHub user ID.

A pending request produces a plain-text review card listing every normalized change with its resource type and action, every finding, the manual-review flag, source claims, public artifact, tool, and policy bindings, and the decision reason. Registered isolated-IaC planning requests also show their public packet: the generated module and the lock, tool, and provider digests. Private broker and credential references stay outside it. Saved native plan and artifact bytes are referenced, not embedded.

Long reviews use bounded pages showing section and page extents and the request ID and digest. **Continue review** and **Previous review page** only read state. If the request or review content changed, or a page is unavailable, show the request again.

- **Approve this plan** appears only at the end of the review and is withheld when an execution-compatibility blocker exists. **Reject** stays on every pending page.
- Reaching a page doesn't prove a person read it. Signed decision buttons and operator CLI decisions keep their exact-digest contract.
- Approval still waits for a separate authorized claim and execution, and never starts a deployment from chat.
- Continuation and decisions need a valid Slack signature, recent timestamp, the expected app and workspace, and an allowed member.
- The renderer stays within Slack's [section limit](https://docs.slack.dev/reference/block-kit/blocks/section-block/), [message block limit](https://docs.slack.dev/reference/block-kit/blocks/), and [button limits](https://docs.slack.dev/reference/block-kit/block-elements/button-element/). Repository labels are plain text, and no mentions, links, or commands are interpreted from them.

```text
/deploy-ops pending
/deploy-ops show REQUEST_ID
/deploy-ops delegate neon1-aws-dev 6 3
/deploy-ops revoke DELEGATION_ID
/deploy-ops stop
/deploy-ops resume
```

`delegate` means six hours and at most three claimed executions. Resource types, action types, change counts, and estimate allowances still come from the protected target policy, so chat can't grant arbitrary permissions.

Slack notifications are best effort, and a failed delivery never grants approval. Use the operator CLI when Slack is down. Self-approval is rejected when the approver's GitHub ID matches the workflow initiator, so use a distinct GitHub App identity for agent-initiated work.

## 7. Enable a bounded work window

The sample target disables delegation. Enable it only after checking the writer role's resource scope, provider lock, source review, and failure behavior.

The default policy requires a monthly cost estimate. The reference workflow doesn't invent one, so it waits for a human unless your trusted workflow supplies Infracost JSON through `deploy-ops gate plan --cost-file PATH` or you turn the requirement off.

An estimate isn't a spending cap, and resource count limits don't cap task counts, runtime, egress, or usage-based charges. Keep role restrictions, quotas, monitoring, and billing alerts outside the caller's control.

```bash
export DEPLOY_OPS_URL=https://deploy-ops.example.com
deploy-ops gate delegate neon1-aws-dev --hours 6 --uses 3
deploy-ops gate delegations
deploy-ops gate pause
```

`pause` stops new authorization, revokes work windows, and invalidates active execution authority. `resume` doesn't restore old grants. Production targets can't enable delegation in this release.

## 8. Reconcile an interrupted execution

A runner heartbeat failure, expired lease, revoked delegation, or operator pause can leave an execution `uncertain` and its target locked. Inspect the actual cloud resources, the IaC state lock, and any operation the provider already accepted before resolving it.

```bash
deploy-ops gate list
deploy-ops gate status REQUEST_ID --operator
deploy-ops gate resolve REQUEST_ID --outcome failed \
  --note "Checked the cloud operation and state. No apply is still running"
deploy-ops gate audit --out audit-export.json
```
