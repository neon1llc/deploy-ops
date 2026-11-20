# Artifact observations

Collect package inventory and optional vulnerability results for an exact reviewed static artifact, whether it is waiting for approval or already delivered. Collection never runs a build, changes the release, approves a deployment, or changes security policy. Default local delivery works without these optional tools.

```bash
deploy-ops artifact options
deploy-ops --state /private/workspace/.deploy-ops artifact collect RELEASE_ID --component web --out /private/evidence/new-report
deploy-ops artifact inspect /private/evidence/new-report
```

The console has **Artifact evidence** in the selected static component's release detail. Choose a new private directory, select observations, and collect. **Inspect evidence directory** reopens a completed report without tools. Paths, choices, and the previous good report survive polling and failed requests. Collection keeps running if you navigate away, so return to that release to see the result. Reloading clears the tab's draft, but the completed directory remains.

## Select observations

- **Package inventory:** Syft scans the frozen artifact directory and produces CycloneDX JSON. Software packages and file components are counted separately. No supported packages means no data, not a clean bill of health. Build dependencies absent from the output are out of scope.
- **Vulnerability observation:** Trivy independently scans the same directory. Findings, detected package count, target count, and advisory freshness stay separate. No findings doesn't establish complete analyzer coverage or safety.
- **Advisory database download:** off by default. Select it only with vulnerability observation to allow native DB retrieval. Trivy dependency requests, telemetry, and check and version updates stay off. Syft online enrichment and update checks are disabled through a private configuration.

To allow advisory retrieval for one collection:

```bash
deploy-ops --state /private/workspace/.deploy-ops artifact collect RELEASE_ID --component web --out /private/evidence/new-scan --vulnerabilities --allow-network
```

Use `--no-inventory` to select only vulnerabilities. `--timeout-seconds` sets each native command's bound, 10 to 300 seconds, default 60. The private cache starts empty, so an offline Trivy request can be unavailable without an advisory DB. An unavailable tool, unsupported output, no data, or stale or unknown database stays explicit. There is no automatic download retry or tool installation.

## What is verified

The collector authenticates the retained prepared-envelope bytes and whole artifact extent, then checks the static site's reviewed digest. Native tools read that frozen site, not a moving pointer, the current source, or a browser-supplied path. A bounded file inventory before and after collection keeps the canonical tree ordering, and changed bytes prevent a completed report. Limits are 100 MB and 10,000 entries for the artifact extent and 10 MB per native report read.

The report identifies the release and component, prepared hash, frozen subject hash, file and byte count, profile, tool versions, times, native report hashes, and safe summaries. The subject hash uses DeployOps' canonical relative-path and file-hash tree method, not the SHA-256 of an OCI image or tar file. Current configuration may be invalid or unavailable, because the CLI builds no Engine, loads no grants, and contacts no provider.

Collection success means an observation directory was retained, not that every selected tool succeeded. Inspect each result. A zero exit is not a security-policy gate. Authenticity, trusted signer, and release policy are **not checked**. No signature, Cosign verification, in-toto conformance, SLSA level, or artifact-trust enforcement is claimed. OCI subjects and enforced signer or scan policy are separate additions.

## Retention and privacy

Output directories must be new and outside the workspace and state. Existing files are kept. Native reports can contain private package and file paths. All output stays in the chosen directory and nothing is uploaded. A report's checksum detects changes, not an untrusted author. For offline transfer, keep `report.json` and its listed native report files together. Tool homes, advisory caches, generated tool configuration, and diagnostic events are outside the verified extent, so don't copy them into a public artifact by default. Output without `report.json` is incomplete.

Native tools use a private HOME and configuration and the minimal process environment without operator, cloud, or registry credentials. This is trusted local tool execution, not an OS sandbox for hostile binaries. A relocated inspection copy may measure its own frozen artifact but gains no target redirection, writable restore, deployment, or cleanup authority.

[Syft](https://oss.anchore.com/docs/guides/sbom/) and [Trivy](https://trivy.dev/docs/latest/target/sbom/) own the inventory and detection implementations. Trivy warns that third-party SBOM inputs can lose detection properties, so this collector scans the subject independently rather than treating Syft-to-Trivy conversion as complete coverage. See [verification](verification.md) for tested versions.
