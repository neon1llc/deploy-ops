# DeployOps

**0.15.1 · local developer preview, not production-supported · by neon1 LLC**

DeployOps deploys infrastructure and applications from separate repositories. You pick an environment, review the exact changes, and track the result through one CLI, a local browser console, and an audit history. Each approval covers one exact artifact, so a changed artifact or workspace can't reuse it.

It's for developers who want reviewed, approved releases on their own machine. It isn't a CI/CD platform and doesn't replace GitHub Actions, Argo CD, or Atlantis. [Where it fits](docs/adoption-fit.md) compares them and says who should wait.

You need Python 3.11 or later.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt -e .
deploy-ops demo ../deploy-ops-demo
```

The demo builds, deploys, promotes, and HTTP-checks a real local application. No cloud account needed.

## Docs

- **Start:** [Where it fits](docs/adoption-fit.md), [Setup](docs/setup.md), [Workflows](docs/workflows.md)
- **Deliver:** [Native infrastructure](docs/native-infrastructure.md), [Cloud Run](docs/cloud-run.md), [Kubernetes](docs/kubernetes.md), [Local Docker](docs/local-docker.md), [Local serving](docs/local-serving.md), [Local blue-green](docs/local-rollouts.md), [Static previews](docs/previews.md), [Infrastructure receipts](docs/infrastructure-receipts.md), [Isolated GCP](docs/isolated-gcp.md)
- **Inspect and recover:** [Source captures](docs/source-captures.md), [Portable inspection](docs/inspection.md), [Artifact observations](docs/artifact-observations.md), [File signatures](docs/file-signatures.md), [Private backups](docs/backups.md), [Save recovery](docs/save-recovery.md), [Local incidents](docs/incidents.md), [Paging](docs/paging.md)
- **Reference:** [Configuration](docs/configuration.md), [Architecture](docs/architecture.md), [Capabilities](docs/capabilities.md), [Support matrix](docs/support-matrix.md), [Verification](docs/verification.md), [Security](SECURITY.md), [Research](docs/research/prior-art.md), [Roadmap](docs/roadmap.md), [Development](docs/development.md)

## Status

The local path runs end to end on a clean machine. Cloud support is partial.

| Target | What has run | Not yet qualified or supported |
|---|---|---|
| Local | Build, deploy, app-only release, promotion, and HTTP check in the demo and CI. Rollback has local tests. Docker Compose and a kind Kubernetes cluster were also checked | Windows |
| AWS | Live CloudFormation create, update, and owned deletion of a private S3 bucket. Live ECS delivery to an existing Fargate service, including an app-only image update | Public HTTP check, image publication, a live CDK stack, live Terraform or OpenTofu apply |
| Azure | Live Bicep and ARM provisioning. Live Container Apps image update with revision health and logs | Ingress HTTP check, DeployOps-driven deletion of ARM resources |
| GCP | Cloud Run candidate and traffic operations and the isolated GCP recipe pass command fixtures and offline validation. [Verification](docs/verification.md) records one bounded, nonproduction Cloud Run run that has not been reproduced independently | Any qualified live deployment, application HTTP, external ingress, image publication |

The [support matrix](docs/support-matrix.md) lists exact versions, regions, and limits. An identity preflight alone doesn't qualify a deployment. "What has run" is Verified evidence under neon1's [release levels](https://github.com/neon1llc/neon1-corpus/blob/main/corpus/standards/versioning.md#release-levels). Nothing is Qualified until it has real use and a tested recovery.

Licensed under [Apache-2.0](LICENSE), with [attribution notices](NOTICE). See [licensing](docs/licensing.md).
