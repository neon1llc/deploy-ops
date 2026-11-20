# Verify a frozen file signature

Check one file from a reviewed static artifact against a modern Sigstore bundle and a local public key
you chose independently. The shared CLI/API/console verifier uses Cosign. It can run before application
approval and does not build, deploy, approve or change a release.

This profile verifies the selected file's content under that key. Other files, directory layout, a
publisher's identity, transparency, signing time, revocation and release policy remain separate. A valid
signature on one file does not make a whole application or image trusted.

## Choose the file and key

```bash
deploy-ops artifact files RELEASE_ID --component web --query package
deploy-ops artifact key-info /trusted/release.pub
```

The file search reads the authenticated frozen inventory, not current source or arbitrary host files.
It supports `--offset` and `--limit` up to 100. In the console, open **Selected-file signature**, then
**Find a frozen file**. Search results name their query and populate the selected relative filename.

Supply a local PEM `PUBLIC KEY` file and a modern Sigstore bundle. Private keys, certificates, cloud-key
URIs and legacy detached-signature formats are outside this profile. Obtain the trusted key independently
of the artifact or bundle. Reading its fingerprint does not establish who should be trusted.

An optional expected SHA-256 pin checks the exact public-key file bytes, including formatting. This is
a file digest, not a certificate or SPKI fingerprint. The console can read a selected key's fingerprint
into an empty pin. It preserves an authored pin and reports a difference rather than replacing it.

## Verify and keep the result

```bash
deploy-ops artifact verify RELEASE_ID --component web --file downloads/package.tar.gz \
  --key /trusted/release.pub --key-sha256 EXPECTED_PUBLIC_KEY_FILE_SHA256 \
  --bundle /evidence/package.sigstore.json --out /evidence/new-file-check
```

Cosign stable v3.1 or later within v3 is the supported command contract. V3.1.4 was natively qualified.
Use `artifact signature-options` to check command-free availability and limits. DeployOps does not
install Cosign or modify host trust. Follow the [upstream installation instructions](https://docs.sigstore.dev/cosign/system_config/installation/)
for your own selected installation.

The verifier authenticates the whole frozen artifact inventory, matches the selected file against its
entry and copies those exact bounded bytes into a new private evidence directory. Cosign receives that
copy, a copied public key and bundle. The original frozen artifact is checked again afterward. Existing
output directories and workspace/state destinations are refused. The directory contains `subject.blob`,
`public-key.pem`, `bundle.sigstore.json` and `report.json`; native diagnostic events and temporary tool
home are private and outside the verified report extent. Nothing is uploaded automatically.

Limits are 100 MB/10,000 entries for the whole artifact, 100 MB for the selected file, 10 MB for the
bundle and 16 KiB for the public key. `--timeout-seconds` is 10 to 300, default 60, for each native command;
it does not bound every filesystem read or the sum of commands.

The [pinned Cosign verification contract](https://github.com/sigstore/cosign/blob/v3.1.4/doc/cosign_verify-blob.md)
supports a local-key bundle path. This integration explicitly leaves transparency and signed timestamps
unchecked. No keyless/OIDC, KMS, registry or remote-key path is selected. The deprecated `--offline`
flag alone can still retrieve TUF trust material, so it is not used as a network-isolation claim. See
the [upstream options](https://github.com/sigstore/cosign/blob/v3.1.4/cmd/cosign/cli/options/verify.go).
This is not an OS-level network sandbox.

CLI exit meanings for `artifact verify`:

| Exit | Meaning |
| --- | --- |
| 0 | Cosign established a signature for these exact selected bytes and key. No whole-artifact or release-policy admission follows. |
| 1 | Native verification did not establish that result. A bad signature and another native verification failure are not distinguished as a definitive cryptographic diagnosis. |
| 3 | The selected native tool/version was unavailable or unsupported. |
| 2 | Input, retained evidence or output admission failed. Inspect the refusal and any incomplete private directory. |

Successful report retention is separate from signature status. The console shows the selected file,
key digest, native result/version, observation time and scope. Changed inputs survive polling and late
responses. A previous retained report stays visible after a transport/refusal error. Signature collection
does not consume or change a waiting application approval.

## Reopen without native tools

```bash
deploy-ops artifact signature-inspect /evidence/new-file-check
```

Inspection validates report format 1, its explicit checksum and all three bound input files. It opens
no recorded release paths, Engine, cloud credentials or native verifier. Its exit 0 means report-byte
integrity, even if the recorded signature result is `not_verified` or `unavailable`. An editable/rehashed
report is not proof that native verification ran, a trusted signer assertion or continuation authority.
The API/CLI response names that limited check `integrity_verified`; the signature result is separately
recorded in `report.status`.

The console's **Inspect an existing signature report** needs only its directory. Current key/bundle/file
fields do not block that read. Native verification, file search and CLI retained inspection do not need
valid current workspace TOML or new execution registration. Relocated frozen inspection copies can be
measured without gaining any target execution authority.

This is an explicit observation profile. Managed signer trust, whole-tree/OCI evidence, freshness and
promotion-policy enforcement remain unfinished work. A missing or inconclusive result cannot supply
those decisions. Ordinary [artifact observations](artifact-observations.md) keep their separate inventory,
vulnerability and database coverage meanings.
