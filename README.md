# DeployOps

**0.15.0 · local developer preview, not production-supported · by neon1 LLC**

DeployOps deploys infrastructure and applications from separate repositories. You choose an environment, review exact changes, and track the result through one CLI, a local browser console, and an audit history. Each approval covers one exact artifact, so a changed artifact or workspace can't reuse it.

It's for developers who want reviewed, approved releases on their own machine. It isn't a CI/CD platform, and it doesn't replace GitHub Actions, Argo CD, or Atlantis. [Where it fits](docs/adoption-fit.md) compares them and says who should wait.

You need Python 3.11 or later.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt -e .
deploy-ops demo ../deploy-ops-demo
```

The demo builds, deploys, promotes, and HTTP-checks a real local application. No cloud account needed.

| Start here | Purpose |
|---|---|
| [Where it fits](docs/adoption-fit.md) | What it adds to existing tools, and who should wait |
| [Setup](docs/setup.md) | Run the CLI and browser console |
| [Workflows](docs/workflows.md) | Infrastructure, apps, promotion, recovery |
| [Source captures](docs/source-captures.md) | Private source copies and their progress |
| [Portable inspection](docs/inspection.md) | Readable evidence from retained releases |
| [Artifact observations](docs/artifact-observations.md) | Inventory and scan results for static files |
| [Private backups](docs/backups.md) | Local state backups and inspection copies |
| [Save recovery](docs/save-recovery.md) | Reconcile an interrupted configuration save |
| [Infrastructure receipts](docs/infrastructure-receipts.md) | Typed outputs for isolated app workers |
| [Isolated GCP](docs/isolated-gcp.md) | Fixed Cloud Run recipe in a dedicated VM |
| [Native infrastructure](docs/native-infrastructure.md) | CloudFormation, CDK, ARM, and Bicep |
| [Cloud Run delivery](docs/cloud-run.md) | Candidate and traffic operations, and recovery |
| [Kubernetes](docs/kubernetes.md) | Image updates to an existing Deployment |
| [Local Docker](docs/local-docker.md) | Select the Docker tools and endpoint |
| [Capabilities](docs/capabilities.md) | What works and what's left |
| [Architecture](docs/architecture.md) | Components and trust boundaries |
| [Configuration](docs/configuration.md) | Repositories, environments, adapters |
| [Research](docs/research/prior-art.md) | Prior art and reuse decisions |
| [Roadmap](docs/roadmap.md) | Next increments |
| [Verification](docs/verification.md) | What actually ran |
| [Static previews](docs/previews.md) | Owners, expiry, cleanup |
| [Local serving](docs/local-serving.md) | One URL that follows the active static target |
| [Local blue-green](docs/local-rollouts.md) | Candidate checks, a separate switch, and retirement |
| [Frozen file signatures](docs/file-signatures.md) | Check file bytes against a local public key |
| [Local incidents](docs/incidents.md) | Response and escalation |
| [Paging](docs/paging.md) | Destinations, routing, remote actions |
| [Support matrix](docs/support-matrix.md) | Checked versions and limits |
| [Security](SECURITY.md) | Limits before cloud access |
| [Development](docs/development.md) | Contributing |

## Status

The local path runs end to end on a clean machine. Cloud support is partial.

| Target | What has run | Not yet qualified or supported |
|---|---|---|
| Local | Build, deploy, app-only release, promotion, and HTTP check run in the demo and in CI. Rollback has local tests. Docker Compose and a kind Kubernetes cluster were also checked | Windows |
| AWS | Live CloudFormation create, update, and owned deletion of a private S3 bucket, and live ECS delivery to an existing Fargate service, including an app-only image update | Public HTTP check, image publication, a live CDK stack, and live Terraform or OpenTofu apply |
| Azure | Live Bicep and ARM provisioning and a live Container Apps image update with revision health and logs | Ingress HTTP check and DeployOps-driven deletion of ARM resources |
| GCP | Cloud Run candidate and traffic operations and the isolated GCP recipe pass command fixtures and offline validation. [Verification](docs/verification.md) records one bounded, nonproduction Cloud Run run that has not been reproduced independently | Any qualified live deployment, application HTTP, external ingress, and image publication |

The [support matrix](docs/support-matrix.md) lists exact versions, regions, and limits. An identity preflight alone doesn't qualify a deployment. "What has run" is Verified evidence under neon1's [release levels](https://github.com/neon1llc/neon1-corpus/blob/main/corpus/standards/versioning.md#release-levels). Nothing is Qualified until it has real use and a tested recovery.

Licensed under [Apache-2.0](LICENSE), with [attribution notices](NOTICE). See [licensing](docs/licensing.md).
