# Cloud Run application delivery

DeployOps updates one existing service through the Cloud Run v2 API. Infrastructure provisioning,
IAM, public access, image building and registry publication stay with their existing owners. Select
`cloud-run` in a GCP environment and provide `project_id`, `region`, `name` and an immutable
`image@sha256:…`. Use the same release preparation, exact approval and explicit execution as other
application adapters. Current local cloud or federated authority must cover that target.

This contract supports a completed, healthy service with one container and one untagged 100% route.
Split/tagged traffic, function/build configuration, missing native identities and unfinished provider
reconciliation require a separate workflow. A service name must leave room for the release-bound
revision suffix. There is no automatic creation, IAM change, image build or public-access change.
One bounded native qualification completed candidate creation, conditional traffic switching, exact
image/route readback and a separately reviewed no-op on a private disposable service. It did not test
application HTTP or establish broader provider readiness. See [verification](verification.md).

## Review before approval

Fresh releases retain `cloud_run_review` in the same component record returned by CLI `run`/`status`
and the authenticated release API. The console shows the frozen service and image before approval;
expand **Reviewed Cloud Run target, revisions and traffic steps** for the service UID, baseline,
candidate, native request digest, planned steps and 100% route. A no-op explicitly plans verification
without a provider write. This projection contains no service template, environment or credentials.

These are preparation facts, not current provider observations, application health or new authority.
Approval and execution remain separate. Older records without this disclosure remain unchanged;
prepare fresh evidence to obtain it. Read current state through **Read provider status**.

## Two separate native writes

Preparation reads the physical service UID, concurrency etag, desired and observed routing, preserved
template identity and actual serving revision image. It freezes a release-bound candidate name and
native request digest. Raw environment values and provider responses do not enter that journal.

Execution rechecks the baseline. It durably records candidate intent before a conditional PATCH that
creates the candidate while pinning traffic to the exact baseline revision. It retains the returned
native operation name before polling. Before the second write, it reads the service and revision again:
the actual immutable image, readiness, completed reconciliation, preserved configuration and baseline
route must match. A fresh etag conditions the separate traffic PATCH. Its intent and operation are
retained independently. Success requires native completion and observed traffic to the exact candidate.
A same-image no-op requires an actually observed matching serving revision and unchanged template.
The candidate's own revision establishes readiness: `latestReadyRevision` may still identify the
serving baseline while the candidate has no traffic. Default-false protobuf booleans may be omitted
from native JSON; positive generation and terminal-readiness facts are still required.

The implementation uses the existing supervised gcloud credential provider or current federated token
and the fixed verified `run.googleapis.com` origin. Tokens stay private. Native writes have no automatic
retry; requests have bounded responses/timeouts and recheck current execution authority before dispatch.
Remote operations can outlive a local interruption or deadline.

## Inspect an interrupted operation

```bash
deploy-ops --project /path/deploy-ops.toml provider inspect RELEASE_ID web
```

The console exposes **Read provider status** on the same release detail. Both routes read the exact
service, candidate revision and acknowledged native operations. A healthy predecessor, missing candidate,
wrong image, changed UID/configuration, superseded revision, pending operation, failed operation and
changed routing remain distinct. A lost acknowledgement retains submission uncertainty. Inspection
never reruns candidate creation or switches traffic, grants fresh authority or marks the controller
successful. Inspect current provider state before preparing any new operation.

Private [backups](backups.md) include the supported operation journal. Version 2 [portable inspections](inspection.md)
include allowlisted Cloud Run scope, native request digest, candidate/baseline, durable phase and selected
last observation without source, raw provider payloads or credentials. Neither imported form authorizes
execution. Legacy Cloud Run preparations lack this native contract; finish under their matching runtime
or reconcile and prepare fresh evidence without rewriting old approved envelopes.

The design uses the official [conditional Service update](https://docs.cloud.google.com/run/docs/reference/rest/v2/projects.locations.services/patch),
[service reconciliation fields](https://docs.cloud.google.com/run/docs/reference/rest/v2/projects.locations.services),
[immutable revision resource](https://docs.cloud.google.com/run/docs/reference/rest/v2/projects.locations.services.revisions)
and [long-running operation resource](https://docs.cloud.google.com/run/docs/reference/rest/v2/projects.locations.operations).
Fixture coverage and live provider qualification are reported separately in [verification](verification.md).
