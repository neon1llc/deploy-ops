# Portable inspection

## Find retained releases

Use **Find releases** in Releases to search literal project names, component names, or release IDs. **Apply history filters** also takes an exact environment, component, and release outcome. Historical names stay searchable after they leave the workspace. **Older results**, **Newer results**, **Refresh**, and **Newest matching releases** navigate bounded pages. Typed filters, drafts, and selected details stay in place while history refreshes.

The CLI uses the same read-only query:

```bash
deploy-ops --state /private/delivery-state status --history
deploy-ops --state /private/delivery-state status --query web --environment staging --outcome failed
deploy-ops --state /private/delivery-state status --component web --limit 50
```

History flags return a page with `releases`, normalized `filters`, matching `total`, `cursor`, and `next_cursor`. Pass `--cursor` with the same filters for the next page. `GET /api/releases/history` takes the same fields and needs the usual operator authentication and origin and host checks. Page sizes run 1 to 100, default 20. Search is literal, so `%` and `_` aren't wildcards. Plain `status` and `GET /api/releases` still return the newest 200 as an array, and an exact ID still reads one record and can't combine with history filters. These reads load no current configuration, create no missing state directory, repair nothing, take no execution authority, and send no notifications.

Pages pin a creation boundary so new releases don't shift an older page, though recorded outcomes can still change. A cursor isn't a frozen snapshot, archival proof, or execution capability. Start from newest after changing filters, replacing or restoring the store, or database maintenance. Home metrics summarize recent rows only. History filters don't change execution blockers or dependency receipt lookup.

## Save a portable summary

Save an inspection when a deployment worker may disappear. The summary keeps the release outcome, workspace digests, controller request identity, prepared artifact hash, and supported native operation facts. It reads without the original source, cloud tools, or credentials.

In the console, select a release and choose **Save offline inspection report** for a desktop report or **Save inspection JSON** for the data. Both use the CLI's capture owner:

```bash
deploy-ops --state /private/worker/.deploy-ops evidence capture RELEASE_ID --out inspection.json
deploy-ops evidence inspect inspection.json --html inspection.html
```

Open `inspection.html` locally. It has readable scope and outcome sections, expandable exact facts, responsive layout, and print styling, with no scripts, external resources, or execution controls. The JSON reader needs the matching installed DeployOps runtime and doesn't initialize an Engine, load a workspace, inspect credentials, or contact a provider. Output files are owner-only and existing files are kept. Treat public resource identities as private operational information when sharing.

## What the summary means

Format version 2 records the toolkit version, capture time, and a SHA-256 over the normalized allowlisted summary, excluding the `sha256` field. Optional `--run-id` and `--run-attempt` bind a capture to a runner attempt, so supply both. The release-record hash identifies the data read without exporting it, and each native journal has its own file hash. The CLI result also returns the output file's byte hash, separate from the summary digest.

These identities stay separate:

- Controller request digests identify canonical manifests.
- ECS native request digests identify the frozen task request file. Container Apps and Cloud Run digests identify canonical request JSON.
- Artifact hashes identify prepared envelopes, not proof that an image was deployed or healthy.

Each component reports controller and artifact identities as present or unavailable. Native journals are present, unavailable, or not applicable. Unavailable expected identities make a capture partial, and an unknown native adapter contract is not applicable. A complete capture covers only this extent, not a complete deployment or backup.

ECS, Container Apps, and Cloud Run journals keep the recorded scope, image, durable phase, request identity, and selected last observation. ECS candidate revisions and truncated discovery are kept when available. New journal writes record their time, and older records stay unknown. An acknowledgement doesn't establish convergence. A missing journal says **no retained operation record**, so submission and current cloud outcome are unknown even if the release failed or was cancelled.

Version 2 adds GCP project and service scope and Cloud Run's candidate and traffic phases. Version 1 stays readable with its original extent and checksum. An older reader rejects version 2 rather than silently dropping facts.

Omitted: source trees, raw databases and WAL, prepared request contents, task definitions, arbitrary provider responses, login homes, credentials, tokens, leases, and Terraform state. Capture reads one release snapshot and individual bounded journals, and isn't a coherent database and filesystem backup. [Restore qualification](migration.md) is separate.

## Outcomes and authority

Deployment outcome, capture completeness, and transport outcome are separate. Capturing or inspecting valid evidence succeeds even when the recorded deployment failed or is uncertain. `run`, `resume`, promotion, and rollback keep their waiting and failure exits. A malformed, unsupported, contradictory, or changed inspection fails without repairing its input.

The checksum detects changed content, not the author. Imported facts can't approve, resume, reconcile, or clean up anything, revive a lease, or rewrite an approved envelope. Use the original operation owner for a separately requested online readback, with current scope and grants revalidated.

The [GitHub recipe](../integrations/github/README.md) has a separate inspection artifact. Its capture, upload result, and artifact identity are recorded apart from the deployment exit. That transport is unqualified until run in the intended protected workflow.
