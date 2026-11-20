# Cloud Run application delivery

DeployOps updates one existing service through the Cloud Run v2 API. Infrastructure provisioning, IAM, public access, image building, and registry publication stay with their existing owners. Select `cloud-run` in a GCP environment and provide `project_id`, `region`, `name`, and an immutable `image@sha256:…`. Release preparation, exact approval, and explicit execution work as for other application adapters, and current local cloud or federated authority must cover the target.

Supported: a completed, healthy service with one container and one untagged 100% route. Split or tagged traffic, function or build configuration, missing native identities, and unfinished provider reconciliation need a separate workflow. The service name must leave room for the release-bound revision suffix. There is no automatic creation, IAM change, image build, or public-access change.

One bounded native qualification completed candidate creation, conditional traffic switching, image and route readback, and a reviewed no-op on a private disposable service. It didn't test application HTTP or establish broader readiness. See [verification](verification.md).

## Review before approval

Cloud Run components expose **Native adapter settings** in workspace controls for public project, region, service, immutable image, or image repository. **Pin** writes only that environment override into the draft, including a pin equal to the component value. **Use inherited** removes only the selected override. Review the diff, validate, and Save. CLI `config inspect`, `config edit`, `config reset`, and the configuration API use the same fields and rules.

Whole environment and dependency references stay unresolved until preparation. An image may stay omitted when the workflow supplies it with a configured image repository at preparation. Inspection and unrelated edits don't author optional defaults or discard unexposed advanced options. These controls edit an existing component. Guided creation, credential changes, and live provider discovery are separate, and editing or saving never executes a deployment.

Fresh releases keep `cloud_run_review` in the component record returned by CLI `run` and `status` and the release API. The console shows the frozen service and image before approval. Expand **Reviewed Cloud Run target, revisions and traffic steps** for the service UID, baseline, candidate, native request digest, planned steps, and 100% route. A no-op plans verification without a provider write. The projection holds no service template, environment, or credentials. These are preparation facts, not current provider state, application health, or new authority. Older records lack this disclosure, so prepare fresh evidence to get it. Read current state through **Read provider status**.

## Two separate native writes

Preparation reads the service UID, concurrency etag, desired and observed routing, template identity, and serving revision image. It freezes a release-bound candidate name and native request digest. Raw environment values and provider responses stay out of that journal.

Execution rechecks the baseline, records candidate intent, then sends a conditional PATCH that creates the candidate while pinning traffic to the baseline revision. It keeps the returned operation name before polling. Before the second write it reads the service and revision again: the immutable image, readiness, completed reconciliation, preserved configuration, and baseline route must match. A fresh etag conditions the separate traffic PATCH, whose intent and operation are kept independently. Success needs native completion and observed traffic on the candidate. A same-image no-op needs an observed matching serving revision and unchanged template.

The candidate's own revision establishes readiness, because `latestReadyRevision` may still name the serving baseline while the candidate has no traffic. Default-false protobuf booleans may be omitted from native JSON. Positive generation and terminal-readiness facts are still required.

The adapter uses the supervised gcloud credential provider or current federated token and the fixed verified `run.googleapis.com` origin. Tokens stay private. Native writes are never retried. Requests have bounded responses and timeouts and recheck execution authority before dispatch. Remote operations can outlive a local interruption or deadline.

## Inspect an interrupted operation

```bash
deploy-ops --project /path/deploy-ops.toml provider inspect RELEASE_ID web
```

The console's **Read provider status** does the same. Both read the service, candidate revision, and acknowledged operations. A healthy predecessor, missing candidate, wrong image, changed UID or configuration, superseded revision, pending operation, failed operation, and changed routing stay distinct. A lost acknowledgement keeps submission uncertainty. Inspection never reruns candidate creation or switches traffic, grants authority, or marks the controller successful. Inspect current provider state before preparing a new operation.

Private [backups](backups.md) include the operation journal. Version 2 [portable inspections](inspection.md) include Cloud Run scope, native request digest, candidate and baseline, durable phase, and last observation, without source, raw provider payloads, or credentials. Neither imported form authorizes execution. Legacy Cloud Run preparations lack this contract, so finish them under their matching runtime or reconcile and prepare fresh evidence without rewriting old approved envelopes.

References: [conditional Service update](https://docs.cloud.google.com/run/docs/reference/rest/v2/projects.locations.services/patch), [service reconciliation fields](https://docs.cloud.google.com/run/docs/reference/rest/v2/projects.locations.services), [immutable revision resource](https://docs.cloud.google.com/run/docs/reference/rest/v2/projects.locations.services.revisions), and [long-running operation resource](https://docs.cloud.google.com/run/docs/reference/rest/v2/projects.locations.operations).
