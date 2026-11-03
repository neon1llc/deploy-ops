# Integrations

For native application delivery, use [the GitHub integration guide](../integrations/github/README.md).
The table below describes the retained independent IaC authorization path.

## Supported paths

| Path | Current implementation | Evidence |
| --- | --- | --- |
| Terraform and OpenTofu | Saved-plan normalization, policy checks, guarded planning and execution | Local fixtures and protocol tests |
| AWS | STS web-identity exchange and IAM trust template | Mocked exchange, template inspection |
| Azure | AzureRM OIDC assertion environment and Entra federation template | Environment contract tests, template inspection |
| GCP | STS exchange, service account impersonation, isolated-pool trust template | Mocked exchanges, template inspection |
| Slack | Cards, signed buttons, work-window commands, stop and resume | Signed local interaction tests |
| Other IaC tools | Manual-only manifest approval contract | Core protocol tests |
| Argo CD and Flux | Desired-state integration design | No controller-specific code yet |

None of the cloud rows means a live deployment has been completed.

## Existing pipelines

A repository that already produces a saved Terraform or OpenTofu plan can use the client without replacing its planner. Run these commands inside the registered pinned reusable workflow, not an arbitrary job that happens to have `id-token: write`.

```bash
deploy-ops gate prepare --target neon1-aws-dev --engine opentofu \
  --plan-json plan.json --artifact plan.bin \
  --lock-file infra/aws/.terraform.lock.hcl --tool-version 1.10.0 \
  --out .deploy_ops/proposal.json
deploy-ops gate submit .deploy_ops/proposal.json --out .deploy_ops/ticket.json
deploy-ops gate wait --ticket .deploy_ops/ticket.json
deploy-ops gate claim --ticket .deploy_ops/ticket.json \
  --artifact plan.bin --out .deploy_ops/lease.json
deploy-ops gate execute --lease .deploy_ops/lease.json --artifact plan.bin
```

Submission runs in the registered planning environment. Claim and execution run in the registered execution environment, in the same workflow run and attempt. The example above shows the command sequence, not a recommendation to put both stages in one job.

`prepare` stores raw preview data in an owner-only file. Delete it when it is no longer needed. `plan` avoids retaining that JSON locally and writes only the binary plan and sanitized ticket. Both paths send the raw preview over HTTPS to the controller for normalization.

## Generic manifest contract

The `manifest` engine lets a trusted pipeline present a normalized change list and an immutable artifact digest. It always requires human approval, even during a delegation window. It does not gain the saved-plan or process-supervision guarantees of the Terraform runner.

[generic-preview.json](../examples/plans/generic-preview.json) is an accepted preview shape. Bind it to an artifact that the existing deployment tool can actually execute or reconcile. `prepare --engine manifest` hashes that artifact. The usual submit, wait, and claim operations then apply.

The custom integration must recheck authority, verify the exact artifact, perform its operation through a trusted workflow with scoped cloud identity, and report completion. The bundled `execute` command deliberately refuses generic manifests. There is no arbitrary shell-command execution endpoint on the controller.

## Adding another engine

Keep the adapter small and own its actual guarantees. Add a preview normalizer, an immutable execution binding where the tool supports one, and a supervised executor only when the entire path can be demonstrated. Unsupported fields and action types should require review or fail closed, not silently disappear.

Pulumi needs careful treatment of saved plans and the program that will execute. CloudFormation can bind to a specific change set rather than merely a template. Bicep what-if is a preview, not the same contract as applying a saved Terraform plan. Do not label those previews equivalent without testing the resulting execution path.

For Argo CD or Flux, authorize the desired-state revision consumed by the existing controller. A Git merge and a completed cluster reconciliation are separate events. Record both. Don't deploy directly around the existing reconciler or claim that authorizing a commit guarantees a healthy rollout.

## Credentials and logs

The cloud exchange functions are in [clouds.py](../src/deploy_ops/clouds.py). The authority service never needs your cloud administrator credentials. AWS role sessions include the request ID. The ledger also keeps GitHub run and actor IDs for investigation.

Complete cross-provider API-call collection is not included. Configure CloudTrail, Azure Activity Logs and relevant data-plane logs, or GCP Audit Logs independently. Correlation is provider-specific and should be verified in a live case study before claiming end-to-end cloud-call attribution.

A distinct GitHub App or other authenticated bot identity can initiate an agent's workflow. This release consumes that GitHub provenance. It does not create the App, issue its installation tokens, merge PRs, or govern every agent tool call.
