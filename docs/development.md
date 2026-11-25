# Development

```bash
python3 -m venv .venv
source .venv/bin/activate
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

Python is the core because orchestration mostly waits on tools and network I/O. The console uses plain JavaScript and CSS with no build system.

Node.js 24 is used for the JavaScript UI contract tests. `make check` skips those contracts when Node.js is absent. Use Node.js 24 or newer for local checks because older runtimes may not provide the browser APIs modeled by the fixtures.

- An adapter declares what it can preview, verify, and restore. A generic command wrapper must not imply Terraform-style saved-plan guarantees. Prefer explicit provider options over a lowest-common-denominator cloud API.
- Add tests at security, state, and data-integrity boundaries. A new integration shows one complete successful path and one consequential failure before gaining more options.
- Keep fake commands, local execution, and live provider checks distinct in the evidence record.

## Source comments and layout

- Put a short comment above each function, method, class, and meaningful group of declarations or operations, stating its purpose, relationship, or constraint in one or two lines. Internal comments are simple thoughts without terminal periods. Don't narrate each assignment. Keep useful docstrings, help text, and native documentation contracts.
- Start source files with a sentence-case description ending in a period. Keep a shebang first, then one blank line. Leave two blank lines between a detached header and source, and between Python definitions or major groups, and one blank line between groups inside a function.
- Follow native formatters in other languages, and group Dockerfiles, workflows, configuration, and examples the same way. For formats without comments, use valid description or metadata fields. Generated artifacts keep their native schema and compiler metadata.
