# Artifact observations

Collect package inventory and optional vulnerability results for an exact reviewed static artifact.
The artifact may be waiting for approval or already delivered. Collection never runs a build, changes
the release, approves a deployment or changes security policy. Default local delivery still works
without these optional tools.

```bash
deploy-ops artifact options
deploy-ops --state /private/workspace/.deploy-ops artifact collect RELEASE_ID --component web --out /private/evidence/new-report
deploy-ops artifact inspect /private/evidence/new-report
```

The console provides **Artifact evidence** inside the selected static component's release detail.
Choose a new private directory, select observations and collect. **Inspect evidence directory** reopens
a completed report without tools. Paths, choices and the previous good report survive polling and failed
requests. Navigating elsewhere leaves the bounded collection running. Return to that release to inspect
its result. Reloading clears the tab's draft, but the completed directory remains available.

## Select observations

- **Package inventory:** Syft scans the frozen artifact directory and produces CycloneDX JSON.
  Software packages and file components are counted separately. No supported packages means no data,
  rather than a clean bill of health. Build dependencies absent from the output are outside this scope.
- **Vulnerability observation:** Trivy independently scans the same directory with its vulnerability
  analyzer. Findings, detected package count, target count and known advisory freshness remain separate.
  No findings does not establish complete analyzer coverage or safety.
- **Advisory database download:** off by default. Select it only with vulnerability observation to
  allow native DB retrieval. Trivy dependency requests, telemetry and check/version updates remain off;
  Syft online enrichment and update checks are disabled through a private configuration.

For example, explicitly allow advisory retrieval for this collection:

```bash
deploy-ops --state /private/workspace/.deploy-ops artifact collect RELEASE_ID --component web --out /private/evidence/new-scan --vulnerabilities --allow-network
```

Use `--no-inventory` to select only vulnerabilities. `--timeout-seconds` sets each native command's
10 to 300 second bound. Default 60. Collection performs a finite sequence of tool/version commands.
Its private cache starts empty, so an offline Trivy request can be unavailable without an advisory DB.
An unavailable tool, unsupported output, no data or stale/unknown database remains explicit.
There is no automatic download retry or tool installation.

## What is verified

The collector authenticates the retained prepared-envelope bytes and whole artifact extent, then checks
the static site's reviewed digest. Native tools read that frozen site, not a moving deployment pointer,
current source directory or browser-supplied scan path. One bounded file inventory before and after
collection retains the existing canonical tree ordering. Changed bytes prevent a completed report.
Limits are 100 MB and 10,000 entries for the retained artifact extent, with 10 MB per native report read.

The report identifies release/component, prepared hash, frozen subject hash, file/byte count, selected
profile, tool versions, times, exact native report hashes and safe summaries. Its subject hash uses the
existing DeployOps canonical relative-path/file-hash tree method. It is not the SHA-256 of an OCI image
or tar file. Current configuration can be invalid or unavailable: the CLI does not construct an Engine,
load grants or contact a provider to discover the retained artifact.

Collection success means a truthful observation directory was retained. It does **not** mean every
selected tool succeeded. Inspect each result. The command's zero exit is not a security-policy gate.
Authenticity, trusted signer and release policy are explicitly **not checked/evaluated**. No signature,
Cosign verification, in-toto conformance, SLSA level or artifact-trust enforcement is claimed by this
local observation contract. OCI subjects and enforced signer/scan policy remain separate additions.

## Retention and privacy

Output directories must be new and outside the workspace/state. Existing files are preserved.
Native reports can contain private package/file paths. All output stays in the chosen private directory
and nothing is uploaded automatically. A report's checksum detects changes, not an untrusted author.
For offline transfer, retain `report.json` and its listed native report files together. Tool homes,
advisory caches, generated tool configuration and diagnostic events are outside the verified extent.
Do not copy them into a public artifact by default. Interrupted output without `report.json` is incomplete.

Native tools use a private HOME/configuration and the existing minimal process environment without
operator, cloud or registry credentials. This is trusted local tool execution, not an OS sandbox for
hostile binaries. A relocated inspection copy may measure its own frozen artifact. It gains no target
redirection, writable restore, deployment or cleanup authority.

Maintained tools own the inventory and detection implementations: [Syft](https://oss.anchore.com/docs/guides/sbom/)
and [Trivy](https://trivy.dev/docs/latest/target/sbom/). Trivy warns that third-party SBOM inputs can
lose detection properties. This collector therefore scans the subject independently rather than treating
Syft-to-Trivy conversion as complete coverage. See [verification](verification.md) for tested versions.
