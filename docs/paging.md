# Named paging destinations

DeployOps routes local deployment incidents to named PagerDuty, GoAlert, incident.io, Better Stack, Grafana Cloud IRM, or ilert integrations. Ordinary notifications go to JSON webhooks, Slack, or ntfy. Each destination keeps its own delivery evidence, and you can have several instances of one provider. Provider acceptance doesn't prove a person received a page, acknowledged an incident, or fixed a deployment.

The native adapters are qualified with local protocol fixtures only. No live provider page has been sent. Hosted accounts and provider channels may need paid plans or incur SMS or voice charges. DeployOps installs no subscriptions or background services.

## Configure, preview and activate

The console's **Paging destinations and routing** panel has destination fields, ordered routes, defaults, and a route preview. Edits stay in memory until **Save paging draft** succeeds. Saving validates but doesn't activate. **Activate saved revision** makes that revision effective for new events. Configuration lives in the workspace database, apart from deployment approvals. Repository code can't install destinations. Back up workspace state before an upgrade.

CLI, using [paging.toml](../examples/paging/paging.toml):

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

Each destination chooses a provider and worker environment references beginning with `DEPLOY_OPS_PAGING_`. JSON, Slack, and ntfy use `endpoint_env`, so webhook URLs containing tokens stay outside exported configuration. Their optional bearer token and HMAC references are `token_env` and `signing_env`.

| Provider | Required settings | Notes |
|---|---|---|
| PagerDuty | `key_env` | Fixed US Events API endpoint. Supports trigger, acknowledge, and resolve ([API](https://docs.pagerduty.com/developer/send-alert-event)) |
| GoAlert | `key_env`, nonsecret HTTPS endpoint ending in `/api/v2/generic/incoming` | Create and close only. Acknowledge is unsupported ([API](https://goalert.me/docs/alerting/alerting-from-generic-api/)) |
| incident.io | `key_env` (**HTTP alert source secret**), `alert_source_id` | Fixed HTTPS endpoint. Firing and resolved events share one deduplication key. Configure parsing for `title`, `status`, `deduplication_key`, and metadata, then routes and escalation, in incident.io. Resolving an alert doesn't close an incident ([contract](https://docs.incident.io/openapi/tags/alert-events-v2.json)) |
| Better Stack | `key_env` (Incidents API token), `requester_email`, existing `policy_id` | Prefer a team-scoped token. A global token also needs `team_name`. The policy owns responders and may place calls or messages. DeployOps sets `team_wait: null` and never creates or edits policies ([create](https://betterstack.com/docs/uptime/api/create-a-new-incident/), [token scope](https://betterstack.com/docs/uptime/api/getting-started-with-uptime-api/)) |
| Grafana Cloud IRM | `endpoint_env` (full secret **Formatted Webhook** URL), optional `token_env` | URL form is `https://oncall-prod-<region>.grafana.net/integrations/v1/formatted_webhook/<secret>/`. A bearer token needs `grafana-irm-app.alert-groups:write`. Raw and incident-creation webhooks, custom domains, and OnCall OSS are unsupported ([contract](https://grafana.com/docs/grafana-cloud/observe-and-act/respond-to-incidents/integrations/custom-integrations/incoming-webhooks/oncall-webhooks/)) |
| ilert | `key_env` (**API alert source integration key**, not an account token) | Trigger, acknowledge, and resolve map to `ALERT`, `ACCEPT`, and `RESOLVE` at its fixed Events endpoint. Each binding keeps one lowercase `alertKey`. HTTP 202 means accepted only ([Events API](https://docs.ilert.com/developer-docs/rest-api/api-reference/events)) |

Provider limits:

- incident.io, Grafana Cloud IRM, and ilert have no acknowledgement or status readback on the selected route.
- Better Stack keeps the returned incident ID. A lost creation response without an ID can't be reconciled and never triggers an automatic duplicate create.
- Before setting Grafana `template_mapping_confirmed = true`, configure the integration to group by `{{ payload.alert_uid }}`, resolve when `{{ payload.state == 'ok' }}`, enable autoresolve, and pick the responder route. DeployOps sends `alerting` and `ok` with the same `alert_uid`. The setting confirms your setup and inspects nothing remote ([templates](https://grafana.com/docs/grafana-cloud/observe-and-act/respond-to-incidents/escalation-and-routing/customize-alert-templates/)).
- ilert `priority_mode = "source"` keeps its configured priority. `"severity"` sends `HIGH` for critical and error and `LOW` for warning and info, which ilert source settings can override. Local severity also goes in `customDetails`.

Set referenced credentials in the worker environment, never in console fields, TOML, repositories, or command arguments. Native bindings keep a hash of the initial credential and refuse later sends if it changes, so keep the original available until its bindings are resolved. Configuration changes and disabling a destination affect new events only.

GoAlert and notification endpoints can allow private CIDRs in `allowed_networks`. The sender checks every resolved address, verifies TLS against the original hostname, and refuses redirects and metadata destinations. There is no TLS bypass.

## Routing and worker behavior

Routes match exact project, environment, component, event, and severity values, and empty lists match all. The first matching route wins and fans out to its destinations. With no match, `default_destinations` applies. Native destinations page only for `incident.opened`, and release success stays a notification.

The route preview explains selected, disabled, quiet, and unsupported destinations. `quiet_until` takes an ISO date and time with a timezone offset and suppresses new routed events until then. Recurring quiet hours aren't implemented. Critical incidents follow the configured policy.

An optional route `fallback` picks a different destination after a definite final transport failure, never after an unknown outcome. There is no fallback chain.

```bash
# Inspect state without sending
deploy-ops paging status

# Send a bounded batch, or separately run the foreground worker
deploy-ops paging drain --limit 50
deploy-ops paging worker
```

The worker runs local incident timers and durable delivery drains. A process manager or container can restart it, but DeployOps installs none. A stopped host can't deliver new events. The console shows worker heartbeat and each obligation's attempts and next due time.

A worker claims each operation before network I/O, in order per binding, and honors bounded `Retry-After`. A stopped in-flight attempt becomes unknown after its claim expires. Backoff and attempt caps survive restart. A lost response or ambiguous provider error stays unknown. There is no exactly-once claim, so replaying an old trigger can create another alert, and retrying a Better Stack creation after an unknown outcome can duplicate an open incident.

## Respond to one destination

Local incident acknowledgement stays local. It doesn't acknowledge paging destinations or approve a deployment. Use the native binding's current ID and version:

```bash
# Request one remote action using the originating integration and deduplication key
deploy-ops paging acknowledge BINDING_ID --version CURRENT_VERSION
deploy-ops paging resolve BINDING_ID --version CURRENT_VERSION

# Accept duplicate risk explicitly when retrying an unknown or failed native action
deploy-ops paging retry BINDING_ID --version CURRENT_VERSION --allow-duplicate

# Read one known Better Stack incident without sending another transition
deploy-ops paging readback BINDING_ID --version CURRENT_VERSION
```

Accepted actions are recorded apart from remote state, which stays unverified for every provider except Better Stack. It supports [acknowledgement](https://betterstack.com/docs/uptime/api/acknowledge-an-ongoing-incident/), [resolution](https://betterstack.com/docs/uptime/api/resolve-an-ongoing-incident/), and explicit [status reads](https://betterstack.com/docs/uptime/api/list-a-single-incident/), attributed to the DeployOps API, not a human. A failed read keeps the last observation. None of this changes local incident state or deployment approvals. No authenticated incoming callbacks exist, so don't expose the console as one.

Resolving a local incident supersedes unsent native triggers but doesn't close an accepted or unknown remote incident, so resolve retained bindings separately. An in-flight request can't be recalled.

A synthetic test requires an explicit destination and active revision. Saving or previewing never sends:

```bash
# This can contact a responder when a worker drains it
deploy-ops paging test operations --revision 1
```

The provider summary identifies the test. Resolve its binding afterward. A running worker may dispatch immediately.

The legacy `deploy-ops notify` command keeps its JSON, Slack, and ntfy receiver and queue, and doesn't replay history into new destinations. Configuring both paths can notify both receivers.
