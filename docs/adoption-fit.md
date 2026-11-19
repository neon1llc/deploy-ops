# Where DeployOps fits

DeployOps is a local developer preview, not production-supported. This page says what it does that other tools don't do out of the box, what it only repeats, and who should wait.

## The job it narrows to

One job, done from a laptop or a self-hosted runner:

> Deploy infrastructure and an application from **separate repositories**, with each release bound to the **exact reviewed artifact**, and keep a local record of who approved what.

Three behaviors carry that job:

- **Exact approvals.** An approval covers one artifact digest, source snapshot, target policy, and environment. A changed artifact or workspace can't reuse it.
- **Typed hand-off between repositories.** The infrastructure release publishes public outputs. The application release reads them, so an app-only release skips infrastructure.
- **A release record you can read.** Every run, approval, promotion, and rollback lands in a local audit history that the CLI and console share.

Everything else in the repository, including paging, the console's appearance settings, and the prior-art catalog, supports those three.

## What you can already do without it

| If you have | You can already | What DeployOps adds | What it lacks |
| --- | --- | --- | --- |
| **GitHub Actions** with [environments](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments) | Require up to six reviewers, add a wait timer, restrict branches, keep deployment history. Some protection rules need a public repository or a paid plan | Approval tied to an artifact digest and a change set, not to a workflow run; the same flow on your machine with no CI | Hosted runners, team-wide visibility, years of production use |
| **Terraform or OpenTofu** with saved plans | Review `plan -out`, apply the same plan file | Binding the infrastructure release's outputs to the app release that consumes them | Provider coverage beyond what its fixtures check |
| **Atlantis** | Plans on pull requests, locking, approvals before apply | Works without a server or pull-request flow | Pull-request integration, locking across a team |
| **Argo CD or Flux** | Desired-state reconciliation for Kubernetes | Nothing yet. It can invoke them through a manual local workflow, and native adapters aren't finished. Don't use it in place of one | Continuous reconciliation, drift repair |
| **Octopus, Harness, Spinnaker** | Release orchestration, approvals, canary analysis, enterprise audit | A lighter local path | Nearly everything an organization buying those tools needs |
| **AWS CodeDeploy** or a cloud's native deploy service | Deployment groups and rollback inside one cloud | One CLI across providers, in principle | Depth on any single provider |
| **A Makefile or shell script** | Run build, test, and deploy steps | Approvals, the audit record, and promotion without rebuilding | Simplicity |

If your team already runs reviewed deploys in GitHub Actions and the audit trail there is enough, DeployOps gives you little. The comparison to make is whether you need approval bound to the exact artifact, and whether you want to run it locally.

## Who should try it

- An engineer who wants to see an exact-approval, receipt-based delivery flow run end to end on a clean machine. The demo needs no cloud account.
- A small team that deploys infrastructure and applications from separate repositories to a development or staging environment, and accepts a preview.

## Who should wait

- Anyone who needs production support, an audited security posture, or Windows. The [support matrix](support-matrix.md) shows what ran. [SECURITY.md](../SECURITY.md) lists the boundaries that don't exist.
- Anyone who needs rollback they can rely on in a cloud. Local and kind rollbacks have tests. Cloud rollback is unqualified.
- Anyone who needs canary or blue/green releases, SBOM enforcement, or live Terraform or OpenTofu apply on AWS. These are not implemented or not live-checked.

## What is current and what is planned

[Capabilities](capabilities.md) separates implemented behavior from live evidence. [Roadmap](roadmap.md) holds what is planned. Treat anything on the roadmap as a statement of intent. A feature appears in the README only after it has run.
