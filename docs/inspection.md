# Portable inspection

## Find retained releases

Use **Find releases** in Releases to search literal project names, component names or release IDs.
**Apply history filters** also accepts an exact recorded environment, component and release outcome.
Historical names remain searchable even after they leave the current workspace. **Older results** and
**Newer results** navigate bounded pages; **Refresh** rereads the current page, and **Newest matching
releases** starts again with later additions. Typed filters, unrelated drafts and selected release details
remain in place while history refreshes.

The CLI uses the same read-only query:

```bash
deploy-ops --state /private/delivery-state status --history
deploy-ops --state /private/delivery-state status --query web --environment staging --outcome failed
deploy-ops --state /private/delivery-state status --component web --limit 50
```

History flags return a page containing `releases`, normalized `filters`, matching `total`, `cursor` and
`next_cursor`. Pass `--cursor` with the same filters for the next page. `GET /api/releases/history` accepts
the same query fields and requires the existing operator authentication and origin/host checks. Page
sizes are 1 to 100, default 20. Search is literal; `%` and `_` do not act as wildcards. Default `status` and
`GET /api/releases` retain their newest-200 array response for compatibility. An exact ID still reads one
record. It cannot be combined with history filters. These reads do not load current configuration,
initialize a missing state directory, repair state, acquire execution authority or send notifications.

Pages pin a creation boundary so new releases do not shift an older page. Recorded outcomes can still
change. A cursor is not a frozen database snapshot, archival proof or execution capability. Filters and
the selected store bind the read position. Start from newest after changing filters, replacing/restoring
the store or performing database maintenance. Home metrics explicitly summarize recent rows. History
filters do not change active execution blockers or dependency receipt lookup.

## Save a portable summary

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
The JSON reader needs the matching installed DeployOps runtime. It does not initialize an Engine,
load a workspace, inspect credentials or contact a provider. Output files are owner-only and existing
files are preserved. Treat public resource identities as private operational information when sharing.

## What the summary means

The version 2 format records its toolkit version, capture time and SHA-256 over the normalized
allowlisted summary, excluding the `sha256` field. Optional `--run-id` and `--run-attempt` bind a
capture to a runner attempt. Supply both. The release-record hash identifies the exact release data
read without exporting that data. Each included native journal has its exact file hash.
The CLI capture result also returns the exact output file's byte hash, separately from the summary digest.

Controller request digests identify canonical manifests. ECS native request digests identify the
frozen task request file; Container Apps and Cloud Run native request digests identify canonical request JSON.
These are different identities and remain separate. Artifact hashes identify prepared envelopes,
not proof that an image was deployed or is healthy.

Each component reports controller and artifact identities as present or unavailable. Native journals
are present, unavailable or not applicable to this format. Expected identities that are unavailable
make capture partial. An unknown native adapter contract is explicitly not applicable. A complete
capture covers only this inspection extent. It does not establish a complete deployment or backup.

ECS, Container Apps and Cloud Run journals retain exact recorded scope, image, durable phase, request identity
and selected last observation. ECS candidate revisions and truncated discovery are retained when
available. New journal writes record their time. An old record without a timestamp remains unknown.
An acknowledgement does not establish convergence. A missing journal says **no retained operation
record**: submission and current cloud outcome are unknown, even if the release is failed or cancelled.

Version 2 adds GCP project/service scope and Cloud Run's candidate/traffic phases. Version 1 remains
readable with its original exact extent and checksum. Its narrower native-adapter scope is preserved.
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

The summary checksum detects changed content. It does not authenticate the author. Imported facts
cannot approve, resume, reconcile or clean up anything. They do not revive a lease or rewrite an
approved envelope. Use the original operation owner for a separately requested online readback,
with exact current scope and grants revalidated. No source directories need to be fabricated.

The [GitHub recipe](../integrations/github/README.md) has a separate inspection artifact. Its capture,
upload result and returned artifact identity are recorded independently of the deployment exit.
That transport remains unqualified until run in the intended protected workflow.
