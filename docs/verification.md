# Verification

**Checked:** 2026-11-03 · **Candidate:** v0.4.0 preview

## Local execution

A fresh network install of the pinned requirements passed on macOS with Python 3.12.10.
`make check` passed source, configuration, documentation-link and whitespace checks and all 109 tests.
The tests cover approval and claim boundaries, concurrency, identity validation, changed evidence,
source snapshots, promotion, rollback, process cleanup, notification retries and console access.
A complete delivery and app-only release also ran from two committed Git repositories while uncommitted
application changes remained excluded.
Provider commands and federation responses in these tests are fixtures.

A fresh local demo deployed infrastructure, delivered the application independently, and promoted its
unchanged artifact to staging. The promoted page returned HTTP 200 with the expected content.
The same demo, schema and catalog commands passed from an independently installed wheel.

Focused review reproduced and repaired unreviewed files under previously excluded artifact directories,
cancellation after approval, and descendants surviving their command's exit. Regression checks exercise
those failures. Prepared evidence now includes every file, while working-tree exclusions apply only
before source copying. Local runtime state is also excluded from the container build context.
Promotion and rollback authenticate the original prepared envelope before reading its source or artifact
metadata. Rewriting both an artifact and its metadata is refused before a new approval can be requested.

## Browser and packaging

A headless Chrome 156.0.8078.80 session exercised the actual loopback console: invalid-token refusal,
keyboard unlock, configuration validation and save, workflow preview, two exact approvals, deployment,
pending cancellation, bounded delegation, app-only delivery, event history and lock.
Promotion and rollback preserved the original static artifact through new approvals. Missing dependency
receipts were explained without offering an executable control, and polling retained keyboard focus. Browser storage
contained no credentials. The 390-pixel layout had no horizontal overflow. JavaScript syntax also passed.

Appearance checks exercised all six presets, system preference changes, retained explicit choices, density,
corner radius, unsaved workspace drafts, invalid and low-contrast import refusal, cross-tab updates,
appearance-only export and storage, reset, and blocked-storage fallback. The narrow layout was inspected
visually. Appearance settings use their own storage key and do not contain operator credentials.

Cancellation during preparation produced an uncertain outcome. After inspecting events and the unchanged
deployment pointer, explicit reconciliation allowed further work. That result does not prove that a
cancelled cloud operation has stopped.

Both the source distribution and wheel built successfully. Their contents retained Apache-2.0, NOTICE,
the prior MIT notice and the bundled theme MIT notice. The installed wheel includes the console, local starter and catalog resources.
The source distribution includes shared test fixtures, check commands, configuration examples and user
documentation so the documented checks can run from the extracted archive.
Pinned requirements are version snapshots, not an independently audited dependency lock or SBOM.

## Remaining qualification

Live AWS, Azure, GCP, Slack, Kubernetes, Docker and Terraform/OpenTofu journeys remain unqualified.
Installed command availability does not establish account access or working integrations. No live cloud
resources, subscriptions or access policies were changed during these checks. GitHub Actions results
must be inspected separately from local execution.

This focused review is not a production security audit, penetration test, sustained reliability test or
proof of safety against a compromised runner. Durable verified infrastructure-output exchange between
isolated cloud workflows remains unfinished. See [security](../SECURITY.md) and
[capabilities](capabilities.md) for current boundaries.

## Reproduce

```bash
python -m pip install -r requirements-dev.txt -e .
make check
deploy-ops demo /tmp/deploy-ops-verification
deploy-ops gate config-check --config config/controller.example.toml
deploy-ops doctor
node --check src/deploy_ops/ui/app.js
```

Use a new demo directory each time. The command refuses to overwrite an existing workspace.
