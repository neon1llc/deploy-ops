# Named paging destinations

DeployOps routes local deployment incidents to named PagerDuty or GoAlert integrations and ordinary
notifications to JSON webhooks, Slack or ntfy. Each destination has independent delivery evidence.
Multiple instances of the same provider are supported. Provider acceptance does not prove that a person
received a page, acknowledged an incident or fixed a deployment.

The native adapters are qualified with local protocol fixtures. No live PagerDuty or GoAlert page has
been sent as part of this implementation. incident.io, Better Stack, Grafana Cloud IRM and ilert remain
planned native adapters. Optional Apprise support remains planned. Hosted accounts and provider channels
may require paid plans or incur SMS/voice charges. No subscriptions or background services are installed.

## Configure, preview and activate

The authenticated console's **Paging destinations and routing** panel provides destination fields,
ordered routes, default destinations and a route preview. Field edits remain in memory until **Save
paging draft** succeeds. Saving validates but does not activate. **Activate saved revision** makes that
exact revision effective for new events. The console preserves drafts after failed requests. Loading
saved settings explicitly replaces the current draft.

Equivalent CLI operations use [paging.toml](../examples/paging/paging.toml):

```bash
# Inspect the current revision before saving a TOML draft
deploy-ops paging config
deploy-ops paging save examples/paging/paging.toml --revision 0

# Preview the authored route without reading credentials or contacting a provider
deploy-ops paging preview --file examples/paging/paging.toml \
  --environment production --event incident.opened --severity error

# Activate only the saved revision you reviewed
deploy-ops paging activate --revision 1
```

Configuration uses the same typed model for TOML import, CLI and console. It is stored in the existing
workspace database, independently of deployment approvals. It does not change the deployment workspace
digest. Repository code cannot install notification destinations. Back up the workspace state before an
upgrade. The additive delivery tables preserve old incident and notification records.

Each destination chooses a provider and worker environment references beginning with
`DEPLOY_OPS_PAGING_`. PagerDuty requires `key_env` and uses its fixed US Events API endpoint. GoAlert
requires `key_env` and a nonsecret HTTPS endpoint ending in `/api/v2/generic/incoming`. JSON, Slack and
ntfy use `endpoint_env`, so webhook URLs containing tokens stay outside exported configuration. Their
optional bearer token and HMAC references are `token_env` and `signing_env`.

Set referenced credentials in the separately managed worker environment. Never enter secret values in
console fields, TOML, source repositories or command arguments. The console does not reveal credential
values. Native bindings retain a hash of the initial integration credential and refuse subsequent sends
if it changes. Keep the original integration available until its bindings are resolved. Configuration
changes and disabling a destination affect new events. Existing bindings retain their original endpoint,
settings and credential reference for lifecycle actions.

GoAlert and notification endpoints can use explicitly approved private CIDRs in `allowed_networks`.
The sender checks every resolved address, pins the connected address, verifies TLS against the original
hostname and refuses redirects and metadata destinations. Private hosting still needs a trusted
certificate. There is no global TLS bypass or arbitrary HTTP mode.

## Routing and worker behavior

Routes match exact project, environment, component, event and severity values. Empty match lists match
all values. The first matching route wins and fans out to its destination list, deduplicating repeated
names. With no match, `default_destinations` applies. Native destinations create pages only for
`incident.opened`. Ordinary release success remains a notification, without creating an incident.

The route preview explains selected, disabled, quiet and unsupported destinations. Decisions and their
configuration revision remain in delivery evidence even when no request is sent. `quiet_until` requires
an ISO date/time with a timezone offset and a finite end. It suppresses new routed events until that
instant. Recurring quiet-hour schedules are not implemented. Critical incidents follow the configured
policy, without a hidden exception.

An optional route `fallback` selects a different destination after a definite final transport failure.
It does not run after an unknown outcome. There is no fallback chain. It is distinct from transport
retry and from provider-managed responder escalation. Local escalation ticks do not repeatedly trigger
native pages. PagerDuty and GoAlert retain responsibility for their own responder schedules.

```bash
# Inspect state without sending
deploy-ops paging status

# Send a bounded batch, or separately run the foreground worker
deploy-ops paging drain --limit 50
deploy-ops paging worker
```

The worker runs local incident timers and durable delivery drains. Stop it with the normal process
interrupt. A process manager or container can restart it, but DeployOps does not install one. A sleeping
or stopped host cannot deliver new events. Provider-managed escalation may continue after accepted
handoff. The console reports worker heartbeat and each obligation's attempts and next due time.

One worker claims each operation transactionally before network I/O. Operations are ordered per native
binding. A stopped in-flight attempt becomes unknown after its claim expires, not silently unsent.
Backoff and attempt caps survive restart. Throttling honors bounded `Retry-After`. Connection failure
before sending can retry automatically. A lost response or ambiguous provider error remains unknown.
There is no exactly-once claim. Both native providers can create a new alert if an old trigger is replayed
after remote closure.

## Respond to one destination

Local incident acknowledgement remains local. It does not acknowledge every paging destination or approve
a deployment. Use the selected native binding's current ID and version:

```bash
# Request one remote action using the originating integration and deduplication key
deploy-ops paging acknowledge BINDING_ID --version CURRENT_VERSION
deploy-ops paging resolve BINDING_ID --version CURRENT_VERSION

# Accept duplicate risk explicitly when retrying an unknown or failed native action
deploy-ops paging retry BINDING_ID --version CURRENT_VERSION --allow-duplicate
```

[PagerDuty Events API v2](https://docs.pagerduty.com/developer/send-alert-event) supports trigger,
acknowledge and resolve. [GoAlert's generic API](https://goalert.me/docs/alerting/alerting-from-generic-api/)
supports create/update and close, and returns unsupported for acknowledgement. Accepted actions are recorded separately from
remote state, which remains unverified. Neither selected integration provides readback or authenticated
incoming callbacks in this increment. Do not expose the local console as a callback endpoint.

Resolving a local incident supersedes unsent native triggers. It does not claim that an accepted or
unknown remote incident was closed. Inspect and resolve its retained bindings separately. An already
in-flight request cannot be recalled, and local serialization cannot guarantee asynchronous provider
processing order. The console retains this uncertainty. A new failed release gets a distinct incident
identity rather than reopening an old occurrence.

A synthetic test requires an explicit destination and active revision. Saving or previewing never sends:

```bash
# This can contact a responder when a worker drains it
deploy-ops paging test operations --revision 1
```

The provider summary identifies the test. Inspect its binding and resolve it afterward. A running worker
may dispatch immediately after the queue operation.

The legacy `deploy-ops notify` command remains compatible with its JSON, Slack and ntfy receiver and
existing queue. It does not replay accepted history into newly added destinations. Run only the delivery
paths you intend to use, since deliberately configuring both can notify both receivers.
