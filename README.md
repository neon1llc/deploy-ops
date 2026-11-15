# DeployOps

**0.14.1 · local developer preview, not production-supported · by neon1 LLC**

DeployOps deploys infrastructure and applications from separate repositories. You choose an environment, review exact changes, and track the result through one CLI, a local browser console, and an audit history. It's for developers who want reviewed, approved releases they can run on their own machine.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt -e .
deploy-ops demo ../deploy-ops-demo
```

The demo builds, deploys, promotes, and HTTP-checks a real local application. No cloud account needed.

| Start here | Purpose |
|---|---|
| [Setup](docs/setup.md) | Run the CLI and browser console |
| [Workflows](docs/workflows.md) | Infrastructure, apps, promotion, and recovery |
| [Infrastructure receipts](docs/infrastructure-receipts.md) | Reuse typed infrastructure outputs in isolated app workers |
| [Isolated GCP](docs/isolated-gcp.md) | Review and run a fixed Cloud Run recipe in a dedicated VM |
| [Native infrastructure](docs/native-infrastructure.md) | Review CloudFormation, CDK, ARM, and Bicep changes |
| [Capabilities](docs/capabilities.md) | What works and what's left |
| [Architecture](docs/architecture.md) | Components, trust boundaries, and diagrams |
| [Configuration](docs/configuration.md) | Repositories, environments, adapters, and outputs |
| [Research](docs/research/prior-art.md) | Prior art, licensing, and reuse decisions |
| [Roadmap](docs/roadmap.md) | Next increments |
| [Verification](docs/verification.md) | What actually ran |
| [Static previews](docs/previews.md) | Owners, expiry, and reviewed cleanup |
| [Local incidents](docs/incidents.md) | Track response and escalation |
| [Paging](docs/paging.md) | Destinations, routing, and remote actions |
| [Support matrix](docs/support-matrix.md) | Checked OS, runtime, and provider limits |
| [Security](SECURITY.md) | Limits to know before cloud access |
| [Development](docs/development.md) | Commands, comments, and ownership (contributing) |

## Status

DeployOps is a local developer preview, not production-supported. The local path runs end to end on a clean machine. Cloud support is partial:

| Target | What has run | Not yet qualified or supported |
|---|---|---|
| Local | Build, deploy, app-only release, promotion, and HTTP check run in the demo and in CI. Rollback has local tests. Docker Compose and a kind Kubernetes cluster were also checked | Windows |
| AWS | Live CloudFormation create, update, and owned deletion of a private S3 bucket, and live ECS delivery to an existing Fargate service, including an app-only image update | Public HTTP check, image publication, a live CDK stack, and live Terraform or OpenTofu apply |
| Azure | Live Bicep and ARM provisioning and a live Container Apps image update with revision health and logs | Ingress HTTP check and DeployOps-driven deletion of ARM resources |
| GCP | Cloud Run and the isolated GCP recipe pass command fixtures and offline validation only | Any live deployment |

The [support matrix](docs/support-matrix.md) lists exact versions, regions, and limits. An identity preflight alone doesn't qualify a deployment.

The table follows neon1's four [release levels](https://github.com/neon1llc/neon1-corpus/blob/main/corpus/standards/versioning.md#release-levels): Implemented, Verified, Released, and Qualified. "What has run" is Verified evidence. Nothing here is Qualified until it has real use and a tested recovery.

Licensed under [Apache-2.0](LICENSE), with [attribution notices](NOTICE). See [licensing](docs/licensing.md).
