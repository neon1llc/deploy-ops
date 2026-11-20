# Verify a frozen file signature

Check one file from a reviewed static artifact against a modern Sigstore bundle and a local public key you chose independently. The shared CLI, API, and console verifier uses Cosign. It can run before application approval and doesn't build, deploy, approve, or change a release.

This verifies the selected file's content under that key. Other files, directory layout, publisher identity, transparency, signing time, revocation, and release policy are separate. A valid signature on one file doesn't make a whole application or image trusted.

## Choose the file and key

```bash
deploy-ops artifact files RELEASE_ID --component web --query package
deploy-ops artifact key-info /trusted/release.pub
```

File search reads the authenticated frozen inventory, not current source or arbitrary host files. It supports `--offset` and `--limit` up to 100. In the console, open **Selected-file signature**, then **Find a frozen file**. Results name their query and fill the selected relative filename.

Supply a local PEM `PUBLIC KEY` file and a modern Sigstore bundle. Private keys, certificates, cloud-key URIs, and legacy detached signatures are outside this profile. Get the trusted key independently of the artifact and bundle. Reading its fingerprint doesn't establish who to trust.

An optional expected SHA-256 pin checks the exact public-key file bytes, including formatting. It's a file digest, not a certificate or SPKI fingerprint. The console can read a key's fingerprint into an empty pin, keeps an authored pin, and reports a difference instead of replacing it.

## Verify and keep the result

```bash
deploy-ops artifact verify RELEASE_ID --component web --file downloads/package.tar.gz \
  --key /trusted/release.pub --key-sha256 EXPECTED_PUBLIC_KEY_FILE_SHA256 \
  --bundle /evidence/package.sigstore.json --out /evidence/new-file-check
```

Cosign stable v3.1 or later within v3 is supported, and v3.1.4 was natively qualified. `artifact signature-options` checks availability and limits without running anything. DeployOps doesn't install Cosign or modify host trust. Follow the [upstream installation instructions](https://docs.sigstore.dev/cosign/system_config/installation/).

The verifier authenticates the whole frozen inventory, matches the selected file against its entry, and copies those exact bounded bytes into a new private evidence directory. Cosign receives that copy, a copied public key, and the bundle. The original artifact is checked again afterward. Existing output directories and workspace or state destinations are refused. The directory holds `subject.blob`, `public-key.pem`, `bundle.sigstore.json`, and `report.json`. Native diagnostic events and the temporary tool home stay private and outside the report extent. Nothing is uploaded.

Limits: 100 MB and 10,000 entries for the artifact, 100 MB for the selected file, 10 MB for the bundle, 16 KiB for the public key. `--timeout-seconds` is 10 to 300, default 60, per native command. It doesn't bound every filesystem read or the sum of commands.

The [pinned Cosign verification contract](https://github.com/sigstore/cosign/blob/v3.1.4/doc/cosign_verify-blob.md) supports a local-key bundle path. Transparency and signed timestamps are left unchecked, and no keyless, OIDC, KMS, registry, or remote-key path is selected. The deprecated `--offline` flag alone can still retrieve TUF trust material, so it isn't a network-isolation claim ([upstream options](https://github.com/sigstore/cosign/blob/v3.1.4/cmd/cosign/cli/options/verify.go)). This is not an OS-level network sandbox.

Exit codes for `artifact verify`:

| Exit | Meaning |
| --- | --- |
| 0 | Cosign established a signature for these exact selected bytes and key. No whole-artifact or release-policy admission follows. |
| 1 | Native verification did not establish that. A bad signature and another native failure aren't distinguished as a cryptographic diagnosis. |
| 2 | Input, retained evidence, or output admission failed. Inspect the refusal and any incomplete private directory. |
| 3 | The native tool or version was unavailable or unsupported. |

Report retention is separate from signature status. The console shows the selected file, key digest, native result and version, observation time, and scope. Changed inputs survive polling and late responses, and a previous report stays visible after a transport or refusal error. Collection doesn't consume or change a waiting application approval.

## Reopen without native tools

```bash
deploy-ops artifact signature-inspect /evidence/new-file-check
```

Inspection validates report format 1, its checksum, and all three bound input files. It opens no release paths, Engine, cloud credentials, or native verifier. Exit 0 means report-byte integrity, even if the recorded result is `not_verified` or `unavailable`. An edited and rehashed report isn't proof that native verification ran, a trusted signer assertion, or continuation authority. The response calls this check `integrity_verified`, with the signature result separately in `report.status`.

The console's **Inspect an existing signature report** needs only its directory. Current key, bundle, and file fields don't block that read. Verification, file search, and CLI inspection don't need valid current workspace TOML or new execution registration. Relocated frozen copies can be measured without gaining execution authority.

Managed signer trust, whole-tree and OCI evidence, freshness, and promotion-policy enforcement remain unfinished, and a missing or inconclusive result can't supply them. Ordinary [artifact observations](artifact-observations.md) keep their separate inventory, vulnerability, and database coverage.
