# DeployOps

**0.3.0 preview · by neon1 LLC**

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
| [Capabilities](docs/capabilities.md) | Implemented paths and remaining work |
| [Architecture](docs/architecture.md) | Components, trust boundaries, and diagrams |
| [Configuration](docs/configuration.md) | Repositories, environments, adapters, and outputs |
| [Research](docs/research/prior-art.md) | Star-sorted prior art, licensing, and reuse decisions |
| [Roadmap](docs/roadmap.md) | Small, complete next increments |
| [Verification](docs/verification.md) | What actually ran |
| [Security](SECURITY.md) | Limits that matter before cloud access |
| [Development](docs/development.md) | Commands, comments, and ownership |

Cloud integrations have command-contract tests, not live account validation. This is a working preview,
not a claim of universal repository compatibility or production readiness.

Licensed under [Apache-2.0](LICENSE), with [attribution notices](NOTICE). See [licensing](docs/licensing.md).
