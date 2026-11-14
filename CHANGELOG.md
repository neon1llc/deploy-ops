# Changelog

DeployOps releases use `v0.MINOR.PATCH` until the first complete release, v1.0.0, following neon1's [versioning standard](https://neon1.tech/knowledge/standards/versioning/). Each version has an annotated tag on the commit it names. Tags v0.2.0 to v0.13.0 were made when those versions shipped; this changelog was written from them on 2026-11-11. The release workflow adds missing tags and never moves one.

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
- The DeployOps baseline: a local infrastructure and application delivery preview. The first tagged version is v0.2.0; there was no v0.1.0.

