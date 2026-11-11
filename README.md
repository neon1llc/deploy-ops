# DeployOps

**0.14.0 preview · by neon1 LLC**

Deploy infrastructure and applications from separate repositories. Choose an environment,
review exact changes, and track the result through one CLI, local browser console, and audit history.
Human approval and bounded delegation are part of delivery, not the whole product.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt -e .
deploy-ops demo ../deploy-ops-demo
```

The demo builds, deploys, promotes, and HTTP-checks a real local application. It needs no cloud account.

| Start here | Purpose |
|---|---|
| [Setup](docs/setup.md) | Run the CLI and browser console |
| [Workflows](docs/workflows.md) | Infrastructure, apps, promotion, and recovery |
| [Infrastructure receipts](docs/infrastructure-receipts.md) | Reuse typed infrastructure outputs across isolated app workers |
| [Isolated GCP](docs/isolated-gcp.md) | Review and run a fixed Cloud Run recipe in a dedicated VM |
| [Native infrastructure](docs/native-infrastructure.md) | Review CloudFormation/CDK changes and frozen ARM/Bicep templates |
| [Capabilities](docs/capabilities.md) | Implemented paths and remaining work |
| [Architecture](docs/architecture.md) | Components, trust boundaries, and diagrams |
| [Configuration](docs/configuration.md) | Repositories, environments, adapters, and outputs |
| [Research](docs/research/prior-art.md) | Star-sorted prior art, licensing, and reuse decisions |
| [Roadmap](docs/roadmap.md) | Small, complete next increments |
| [Verification](docs/verification.md) | What actually ran |
| [Static previews](docs/previews.md) | Isolated owner/expiry and reviewed retained-data cleanup |
| [Local incidents](docs/incidents.md) | Track response and escalation separately from notification delivery |
| [Paging](docs/paging.md) | Configure destinations, preview routing and track each remote action |
| [Support matrix](docs/support-matrix.md) | Checked OS, runtime and provider boundaries |
| [Security](SECURITY.md) | Limits that matter before cloud access |
| [Development](docs/development.md) | Commands, comments, and ownership |

CloudFormation passed a live private S3 lifecycle; ECS passed infrastructure-to-app delivery and an
app-only image update. Azure passed Bicep/ARM provisioning and an independent Container Apps image update;
owned cleanup completed after a provider deletion overrun. Other cloud paths have command-contract tests.
Identity preflight does not qualify a deployment. This is a working preview,
not a claim of universal repository compatibility or production readiness.

Licensed under [Apache-2.0](LICENSE), with [attribution notices](NOTICE). See [licensing](docs/licensing.md).
