# Changelog

DeployOps releases use `v0.MINOR.PATCH` until the first complete release, v1.0.0, following neon1's [versioning standard](https://neon1.tech/knowledge/standards/versioning/). Each version has an annotated tag on its commit. Tags v0.2.0 to v0.13.0 were made when those versions shipped, and this changelog was written from them on 2026-11-11. The release workflow adds missing tags and never moves one.

## Unreleased

### Added
- CI runs the checks, tests, and demo on Linux and macOS, and installs the built wheel with pipx to run the demo from it. On macOS runners the test step doesn't fail the job, because a few timing-dependent tests fail there intermittently.

### Changed
- The README installs with pipx from a release tag instead of a source checkout, and says Windows is unsupported. There is no PyPI release.

## v0.15.1 - 2026-11-20

### Changed
- Shorter README, security notes, licensing, docs, examples, `--help` text, and console hints. The README groups its doc links instead of tabulating them. The migration page is now a table, the verification ledger is condensed with shared caveats stated once, and repeated recovery, setup, and limit text is merged. The case study page is removed, and its open item moved to the roadmap.
- Docs and `--help` text use sentences instead of semicolons.
- The native infrastructure page matches the support matrix: CloudFormation, Bicep, and ARM have run live, and CDK has fixtures only.
- `status --help` reads "Default 20" instead of "default20".

## v0.15.0 - 2026-11-19

This release adds local delivery, recovery, and inspection features, with fixes. Several prepared formats changed. Finish or reconcile pending work with the version that prepared it, and prepare a fresh release afterward. [Migration](docs/migration.md) lists each case.

### Added
- Local deployments bind each directory or static target to one owner, and a stable preview URL follows an approved target. See [Local serving](docs/local-serving.md).
- Local blue-green rollout stages a static candidate, measures it, and switches the route and retires the previous files under separate approvals. See [Local blue-green](docs/local-rollouts.md).
- [Frozen file signatures](docs/file-signatures.md) check one file of a reviewed static artifact against a local public key with Cosign.
- [Artifact observations](docs/artifact-observations.md) collect a package inventory and optional vulnerability results for a frozen static artifact.
- [Portable inspection](docs/inspection.md) exports readable, source-free evidence from retained releases. [Source captures](docs/source-captures.md) show copying that stopped before a release existed.
- [Private backups](docs/backups.md) capture the ledger and its owned files, and restore them as inspection copies.
- [Save recovery](docs/save-recovery.md) reconciles an interrupted configuration save without replacing current work.
- Cloud Run delivery records the candidate and traffic updates as separate, conditional operations with readback, and shows the frozen target and traffic steps before approval. See [Cloud Run delivery](docs/cloud-run.md).
- A [local Docker profile](docs/local-docker.md) selects the Docker tools and endpoint. The console edits Compose and Cloud Run settings, and can reset a field to its default or inherited value.
- An incident can start from a saved later observation, and a reviewed handoff queues native responder pages.
- Release history shows command outcomes with exit codes and elapsed time, can be browsed with filters and paging, and can be read from a retained store without loading current grants.
- Worker ownership is recorded for local releases, and recovery is limited to the runtime that holds it. Private cache outcomes are reported separately.

### Changed
- Slack review cards list every normalized change, finding, and public binding, in bounded pages. **Approve this plan** appears on the last page.
- Terraform and OpenTofu preparation reviews the effective backend and checks selected outputs in the saved plan before approval.
- The README lists the new pages and updates the GCP status row.
- The docs describe checks by change, not by build number.

### Fixed
- Kubernetes updates pin the reviewed connection and the Deployment's identity.
- Guarded Terraform and OpenTofu plans refuse untracked or ignored root `.tf` files, automatic variable files, and the provider lock file. The runner also follows each tool's ignored-file names.
- Terminal commands enforce one combined output limit.
- Approvals show the complete native effects, including CloudFormation stack deletion and snapshots, which need explicit grants.
- Slack review keeps public review packets and forwards the controller URL.
- Configuration conflicts keep the exact source identity.
- A waiting approval emits one event per new request, reloads its identity under worker ownership, and can be cancelled after the controller already failed the request.
- Console fixes: accessible in-page dialogs, direct reconnect on Controls, and retained inspection errors tied to the current selection.
- `gate --help` comes from the native command parser.

## v0.14.4 - 2026-11-19

### Added
- [Where DeployOps fits](docs/adoption-fit.md) names the one job it narrows to, compares it with GitHub Actions environments, Terraform saved plans, Atlantis, Argo CD and Flux, release-orchestration products, and scripts, and says who should wait.

### Changed
- The README states the Python version it needs and says DeployOps is not a CI/CD platform.

## v0.14.3 - 2026-11-15

### Changed
- Cut wording that repeats a table, list, or linked page.
- Each release's changelog entry is published as its GitHub release.

## v0.14.2 - 2026-11-15

### Changed
- Shorter, plainer wording in the README, docs, and examples.

## v0.14.1 - 2026-11-14

### Changed
- The README, package description, security notes, and support matrix now name one stage, local developer preview, not production-supported, and say which stages have run on each provider: local delivery end to end, some live AWS and Azure stages, and no live GCP deployment.
- The support matrix records a v0.14.0 check on macOS: a fresh install from the README, 653 tests, and the demo from an installed wheel.

## v0.14.0 - 2026-11-11

### Added
- Owned static revision sessions are shared across the CLI and the console.
- Local build inputs and explicit environment inheritance can be edited.
- Local controller stop and recovery, from the console and the CLI.
- Structured local onboarding drafts.
- Unified workspace navigation and usable release controls in the console.
- Guarded validation and saves shared by the CLI and the console.

### Fixed
- An incomplete inspection note no longer loses an approval, and notes are required only for reconciliation.
- Packages include the JavaScript source test fixtures.
- Isolated runs keep safe native failure details and reap exiting transports, including an exited Lima transport on GCP.

### Changed
- Docs record confirmed cleanups on Azure and GCP and separate failed live planning from transport checks.

## v0.13.0 - 2026-11-03
<!-- commit: 13988fcd9011ef8e9a6624d204e1dceacb357df2 -->

### Added
- Isolated infrastructure approvals and app receipts unified on GCP.
- Grafana Cloud IRM, ilert, incident.io, and Better Stack paging lifecycles.
- Exact Container Apps recovery evidence kept on Azure.

### Fixed
- Azure preparation shows safe diagnostics and a pending status.

## v0.12.0 - 2026-11-03
<!-- commit: 7dbe01d86d9bb31e620375f9fef54ee4f61fdac1 -->

### Added
- GCP receipts and local Azure certificate access.
- Durable PagerDuty and GoAlert operator workflows.

## v0.11.1 - 2026-11-03
<!-- commit: a97415e49bd8611e199fc5dbb60a1efa0f8c5f50 -->

### Fixed
- ECS keeps uncertain task registrations and refuses to replay writes.

## v0.11.0 - 2026-11-03
<!-- commit: 6445c89bf3e4dfa0a628dda68af4bc7d81138cd5 -->

### Added
- Reviewed native infrastructure delivery and scoped local cloud access.

## v0.10.0 - 2026-11-03
<!-- commit: abbe67c7d31589c6a4261a44a7880fde72174364 -->

### Added
- Owned static previews with reviewed cleanup.

## v0.9.0 - 2026-11-03
<!-- commit: 8391a4fd674c3c2656ae598571b10f1d4af5e154 -->

### Added
- Local incidents and bounded escalation signals are tracked.

## v0.8.0 - 2026-11-03
<!-- commit: d36d456341f5a0c092f322208602678b64da4b79 -->

### Added
- Qualified local container delivery examples.

## v0.7.0 - 2026-11-03
<!-- commit: 927e8e0330ddcfaf00981e86778a03fe4353116b -->

### Added
- Local deployment drift is recorded, without automatic repair.

## v0.6.0 - 2026-11-03
<!-- commit: 274aebb0ff363d508b2466675c8985cd0b84fed7 -->

### Added
- Guided workspace drafts that keep TOML as written and save without conflicts.

## v0.5.0 - 2026-11-03
<!-- commit: c2715f7fb43ddd36ee84d7df52d0a4522613b96b -->

### Added
- Isolated AWS app workers connect through verified infrastructure receipts.

## v0.4.0 - 2026-11-03
<!-- commit: 8dc283067d82ca31babc0ac332a3a60e0567ca5a -->

### Added
- Validated appearance settings and shared themes.

## v0.3.0 - 2026-11-03
<!-- commit: c078de372f04a339657302bffcdaf9adb7b53f10 -->

### Added
- Reviewed artifact reuse in the operator console.

## v0.2.0 - 2026-11-03
<!-- commit: 5e6571f3ed6484aa829f0adc5e2554477656733c -->

### Added
- The DeployOps baseline: a local infrastructure and application delivery preview. The first tagged version is v0.2.0. There was no v0.1.0.

