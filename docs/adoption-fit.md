# Where DeployOps fits

DeployOps is a local developer preview, not production-supported.

## The job

From a laptop or a self-hosted runner: deploy infrastructure and an application from **separate repositories**, bind each release to the **exact reviewed artifact**, and keep a local record of who approved what. Three behaviors carry it:

- **Exact approvals.** An approval covers one artifact digest, source snapshot, target policy, and environment. A changed artifact or workspace can't reuse it.
- **Typed hand-off between repositories.** The infrastructure release publishes public outputs. The application release reads them, so an app-only release skips infrastructure.
- **A release record you can read.** Every run, approval, promotion, and rollback lands in a local audit history that the CLI and console share.

Everything else, including paging, appearance settings, and the prior-art catalog, supports those three.

## What you can do without it

| If you have | You can already | What DeployOps adds | What it lacks |
| --- | --- | --- | --- |
| **GitHub Actions** with [environments](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments) | Require up to six reviewers, add a wait timer, restrict branches, keep deployment history. Some protection rules need a public repository or a paid plan | Approval tied to an artifact digest and a change set, not a workflow run. The same flow on your machine with no CI | Hosted runners, team-wide visibility, years of production use |
| **Terraform or OpenTofu** with saved plans | Review `plan -out`, apply the same plan file | Binding the infrastructure release's outputs to the app release that consumes them | Provider coverage beyond what its fixtures check |
| **Atlantis** | Plans on pull requests, locking, approvals before apply | Works without a server or pull-request flow | Pull-request integration, locking across a team |
| **Argo CD or Flux** | Desired-state reconciliation for Kubernetes | Nothing yet. A manual local workflow can invoke them, and native adapters aren't finished. Don't use it in place of one | Continuous reconciliation, drift repair |
| **Octopus, Harness, Spinnaker** | Release orchestration, approvals, canary analysis, enterprise audit | A lighter local path | Nearly everything an organization buying those tools needs |
| **AWS CodeDeploy** or a cloud's native deploy service | Deployment groups and rollback inside one cloud | One CLI across providers, in principle | Depth on any single provider |
| **A Makefile or shell script** | Run build, test, and deploy steps | Approvals, the audit record, and promotion without rebuilding | Simplicity |

If your team already runs reviewed deploys in GitHub Actions and that audit trail is enough, DeployOps gives you little. The test is whether you need approval bound to the exact artifact, and whether you want to run it locally.

## Who should try it

- An engineer who wants to see an exact-approval, receipt-based delivery flow run end to end on a clean machine. The demo needs no cloud account.
- A small team that deploys infrastructure and applications from separate repositories to a development or staging environment, and accepts a preview.

## Who should wait

- You need production support, an audited security posture, or Windows. The [support matrix](support-matrix.md) shows what ran. [SECURITY.md](../SECURITY.md) lists the boundaries that don't exist.
- You need rollback you can rely on in a cloud. Local and kind rollbacks have tests. Cloud rollback is unqualified.
- You need canary or blue/green releases in a cloud, SBOM enforcement, or live Terraform or OpenTofu apply on AWS. These aren't implemented or aren't live-checked.

[Capabilities](capabilities.md) separates implemented behavior from live evidence. The [roadmap](roadmap.md) is intent only. A feature reaches the README only after it has run.
