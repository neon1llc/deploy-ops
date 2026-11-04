# Development

Read [capabilities](capabilities.md) and [architecture](architecture.md) before changing a delivery path.
Keep infrastructure and application delivery connected through the shared core.

```bash
python -m pip install -r requirements-dev.txt -e .
make check
deploy-ops demo ../deploy-ops-check
deploy-ops schema
```

## Ownership

```text
src/deploy_ops/
  delivery/          Workspace model, release engine, state receipts, process supervision
    adapters/        Native local, IaC, cloud runtime, and trusted workflow implementations
  ui/                Thin browser interface, no second policy or deployment engine
  templates/         Copyable complete starters
  catalog.json       Curated integration and research metadata
  console.py         Authenticated local console and configuration editing
  api.py             Independent authority service
  service.py         Approval, lease, delegation, and reconciliation operations
  identity.py        GitHub OIDC verification
  policy.py          Server-owned authorization limits
  store.py           Transactions and hash-linked audit
  cli.py             Deployment CLI
  gate_cli.py        Separate authority and split-job IaC commands
integrations/        Explicit setup templates, not active credentials or subscriptions
docs/                Product, operational, research, and evidence records
tests/               Shared fixtures, complete paths, and meaningful negative cases
```

Python remains the core because orchestration is mainly waiting on tools and network I/O. It matches
neon1's existing work and the retained code. This is a design judgement, not a throughput benchmark.
Go would be worth reconsidering for a measured high-concurrency runner bottleneck, not for speculative speed.
TypeScript or a desktop shell can be added when the UI needs a richer interaction model. The current browser
console uses ordinary JavaScript and CSS without a separate build system.

Keep one meaning in one owner. An adapter must declare what it can actually preview, verify, and restore.
A generic command wrapper must not imply Terraform-style saved-plan guarantees. Prefer explicit provider
options over a fictional lowest-common-denominator cloud API.

Add tests at security, state, and data-integrity boundaries. A new integration should show one complete
successful path and a consequential failure before more options are added. Distinguish fake commands,
local execution, and live provider verification in the evidence record.

Put a short comment above each function, method, class and meaningful group of related declarations or
operations. Explain its purpose, relationship or constraint in one or two lines, rarely three. Internal
comments use simple thoughts without terminal periods. Do not narrate each assignment or repeat the name
immediately below. Preserve useful docstrings, help text and native documentation contracts.

Start source files with a sentence-case description ending in a period. Keep a shebang first, followed by
one blank line before the header. Leave two blank lines between a detached header and source, and between
Python definitions or major groups. Use one blank line between meaningful groups inside a function. Follow
native formatters and syntax requirements in other languages. Apply semantic grouping to Dockerfiles,
workflows, configuration and examples as well as application code. For formats without comments, use valid
description or metadata fields where supported rather than invalid syntax. Generated artifacts retain their
native schema and compiler metadata.
