# Integrations

For native application delivery, use [the GitHub integration guide](../integrations/github/README.md). The rest of this page covers the independent IaC authorization path.

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

`prepare` stores raw preview data in an owner-only file, so delete it afterward. `plan` keeps only the binary plan and sanitized ticket.

Submission runs in the planning environment. Claim and execution run in the execution environment, in the same workflow run and attempt. The sequence above doesn't mean both stages belong in one job.

## Automatically loaded IaC files

Guarded `plan` and `execute` require the registered commit and unchanged tracked files. They refuse:

- Untracked or ignored root `.tf` and `.tf.json` files.
- Automatic variable files (`terraform.tfvars`, `terraform.tfvars.json`, `*.auto.tfvars`, `*.auto.tfvars.json`), including a hidden `.private.auto.tfvars`.
- The provider lock file, `.terraform.lock.hcl`.
- For OpenTofu, `.tofu` and `.tofu.json` files, which can override the matching Terraform file.
- Root native input symlinks and nonregular files.

Native configuration dotfiles and editor backups are ignored. The check reads filenames and Git membership, never variable values, and runs before credentials or native initialization. It changes no files. Review public configuration through the normal commit workflow or use a clean checkout, and keep secrets out of Git. Untracked notes, variable files this command doesn't load, saved plans, and native caches stay usable. Execution still applies the exact approved saved plan.

This root check doesn't attest local child modules outside the root, module downloads, files read by expressions or providers, external inputs, submodule contents, or concurrent changes after inspection. Use a controlled, isolated runner for those. Native file loading is documented by [Terraform](https://developer.hashicorp.com/terraform/language/files) and [OpenTofu](https://opentofu.org/docs/language/files/), and automatic variable names follow their [input variable contract](https://opentofu.org/docs/language/values/variables/).

## Generic manifest contract

The `manifest` engine lets a trusted pipeline present a normalized change list and an immutable artifact digest. It always needs human approval, even during a delegation window, and has no saved-plan or process-supervision guarantees.

[generic-preview.json](../examples/plans/generic-preview.json) is an accepted preview shape. `prepare --engine manifest` hashes the artifact you bind to it, then the usual submit, wait, and claim apply. The custom integration must recheck authority, verify the exact artifact, and report completion. The bundled `execute` refuses generic manifests, and the controller has no shell-command endpoint.

## Adding another engine

An adapter must own its real guarantees. Unsupported fields and action types must require review or fail closed. Don't label previews from different tools equivalent without testing the execution path. For Argo CD or Flux, a Git merge and a completed reconciliation are separate events.

## Credentials and logs

The cloud exchange functions are in [clouds.py](../src/deploy_ops/clouds.py). The authority service never needs your cloud administrator credentials. AWS role sessions include the request ID, and the ledger keeps GitHub run and actor IDs.

A distinct GitHub App or bot identity can initiate an automated workflow. This release consumes that GitHub provenance. It doesn't create the App, issue installation tokens, merge PRs, or govern every caller operation.
