# Portable inspection

Save an inspection when a deployment worker may disappear. The summary keeps the recorded release
outcome, exact workspace digests, controller request identity, prepared artifact hash and supported
native operation facts. It can be read without the original source, cloud tools or credentials.

In the console, select a release and choose **Save offline inspection report** for a desktop report,
or **Save inspection JSON** for the portable data. Both use the same capture owner as the CLI:

```bash
deploy-ops --state /private/worker/.deploy-ops evidence capture RELEASE_ID --out inspection.json
deploy-ops evidence inspect inspection.json --html inspection.html
```

Open `inspection.html` locally. It has readable scope and outcome sections, expandable exact facts,
responsive layout and print styling. It contains no scripts, external resources or execution controls.
The JSON reader needs the matching installed DeployOps runtime; it does not initialize an Engine,
load a workspace, inspect credentials or contact a provider. Output files are owner-only and existing
files are preserved. Treat public resource identities as private operational information when sharing.

## What the summary means

The version 2 format records its toolkit version, capture time and SHA-256 over the normalized
allowlisted summary, excluding the `sha256` field. Optional `--run-id` and `--run-attempt` bind a
capture to a runner attempt; supply both. The release-record hash identifies the exact release data
read without exporting that data. Each included native journal has its exact file hash.
The CLI capture result also returns the exact output file's byte hash, separately from the summary digest.

Controller request digests identify canonical manifests. ECS native request digests identify the
frozen task request file; Container Apps and Cloud Run native request digests identify canonical request JSON.
These are different identities and remain separate. Artifact hashes identify prepared envelopes,
not proof that an image was deployed or is healthy.

Each component reports controller and artifact identities as present or unavailable. Native journals
are present, unavailable or not applicable to this format. Expected identities that are unavailable
make capture partial. An unknown native adapter contract is explicitly not applicable; a complete
capture covers only this inspection extent. It does not establish a complete deployment or backup.

ECS, Container Apps and Cloud Run journals retain exact recorded scope, image, durable phase, request identity
and selected last observation. ECS candidate revisions and truncated discovery are retained when
available. New journal writes record their time; an old record without a timestamp remains unknown.
An acknowledgement does not establish convergence. A missing journal says **no retained operation
record**: submission and current cloud outcome are unknown, even if the release is failed or cancelled.

Version 2 adds GCP project/service scope and Cloud Run's candidate/traffic phases. Version 1 remains
readable with its original exact extent and checksum; its narrower native-adapter scope is preserved.
An older reader rejects version 2 rather than silently dropping its new facts.

Source trees, raw databases/WAL, prepared request contents, task definitions, arbitrary provider
responses/messages, login homes, credentials, tokens, leases and Terraform state are omitted. Capture
reads one release snapshot and individual bounded journals. It does not claim a coherent database
and filesystem backup. [Restore qualification](migration.md) remains separate.

## Outcomes and authority

Deployment outcome, capture completeness and transport outcome are separate. Capturing or inspecting
valid evidence returns success even when its recorded deployment failed or remains uncertain.
`run`, `resume`, promotion and rollback retain their existing waiting/failure exits. A malformed,
unsupported, contradictory or changed inspection fails without repairing its input.

The summary checksum detects changed content; it does not authenticate the author. Imported facts
cannot approve, resume, reconcile or clean up anything. They do not revive a lease or rewrite an
approved envelope. Use the original operation owner for a separately requested online readback,
with exact current scope and grants revalidated. No source directories need to be fabricated.

The [GitHub recipe](../integrations/github/README.md) has a separate inspection artifact. Its capture,
upload result and returned artifact identity are recorded independently of the deployment exit.
That transport remains unqualified until run in the intended protected workflow.
