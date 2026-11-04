# Local incidents and escalation signals

An incident records responsibility and response to a failed or uncertain release. It does not change the
release outcome, repair a deployment or establish that an external responder was paged. The local CLI and
authenticated loopback console use the same durable incident records, audit trail and notification outbox.
The owner is a descriptive team/person label, not an authenticated remote identity.

## Open, acknowledge and resolve

Inspect the release first, then open one incident with an owner, a note and a bounded escalation policy:

```bash
deploy-ops incidents open RELEASE_ID --owner operations \
  --note 'Investigate the failed readiness check' --interval-seconds 900 --limit 3
deploy-ops incidents list
```

Only failed or uncertain releases are eligible. Repeating the same open request returns the existing
incident; changing its owner or policy is refused. A resolved incident is never implicitly reopened.
Opening is explicit: existing failures are not scanned and opened automatically.

Use the incident ID and version returned by `list` for decisions:

```bash
deploy-ops incidents acknowledge INCIDENT_ID --version CURRENT_VERSION --note 'Investigating the target'
deploy-ops incidents resolve INCIDENT_ID --version CURRENT_VERSION --note 'Cause inspected and corrected'
```

Acknowledgement stops further escalation. Resolution is allowed from either open or acknowledged, and
also stops escalation. A stale version is refused so a concurrent escalation or decision cannot be silently
overwritten. Read the latest state before trying again. Record target recovery separately with the normal
release reconciliation workflow. Notes stay in local event/audit records and are not sent in notifications;
do not put secrets into notes or owner labels.

In the browser, select a failed/uncertain release and choose **Track a local incident**, or open **Local
incidents**. The forms retain drafts during polling. Select an incident to acknowledge or resolve it with a
note. Deployment status, incident status and notification transport status are displayed separately.

## Explicit escalation checks

```bash
deploy-ops incidents tick
```

There is no hidden scheduler. Run this command from a scheduler you manage, or use **Check due escalations**
in the console. Each check emits at most one overdue escalation per open incident and schedules the next
from the check time. A long interruption does not create a catch-up burst. Intervals range from 60 to 86400
seconds; the escalation limit is 1 to 5. At the limit the incident stays open and visible, with no further
scheduled escalation. Acknowledged/resolved incidents never escalate. Concurrent checks serialize in the
existing database, and deadlines survive restart.

Escalation raises a level and queues a signal to the configured receiver. It does not select another
recipient, consult an on-call schedule, or guarantee a human response.

## Deliver and inspect notifications

```bash
deploy-ops notify --status
# Uses DEPLOY_OPS_NOTIFY_URL; credentials belong in the worker's secret environment.
deploy-ops notify --kind json
```

The existing JSON, Slack and ntfy transports accept incident events. HTTP success means the destination
accepted the request; it does not acknowledge the incident. The outbox reports pending, HTTP-accepted,
superseded and exhausted records. It tracks one delivery state per event, not one per destination: changing
`--url` does not implement fan-out. Use one receiver that owns downstream routing.

JSON incident notifications contain a complete snapshot, stable event ID, incident ID, monotonic version,
owner, status, escalation level/limit, interval and timestamps. A receiver must:

1. Deduplicate `event_id` and upsert by `incident_id`, even if its first snapshot is acknowledged/resolved.
2. Apply only a newer `version`; an older in-flight request can arrive after resolution.
3. Keep transport acceptance distinct from authenticated human acknowledgement.

Obsolete unsent incident snapshots are suppressed before sending. Already in-flight requests cannot be
recalled. Slack/ntfy messages include the incident ID, version and state for a human reader; they do not
implement a remote incident state machine. The notification endpoint must be HTTPS; redirects are not
followed. Optional existing HMAC and bearer credentials are supplied through the notification worker's
environment, never by repository source or the browser.

Delivery remains at least once. Up to ten attempts are reserved transactionally per event, including
attempts interrupted before their outcome is recorded. Concurrent failures cannot undo an accepted state.
After exhaustion, inspect the receiver and local evidence; automatic retry reset is not implemented.
A crash after HTTP acceptance but before recording it can produce a duplicate with the same event ID.

## Boundaries and storage

Incidents are an additive table in the existing workspace database. State changes, audit entries and outbox
records commit together. They are separate from whole-release snapshots so a worker cannot overwrite an
operator acknowledgement with an older release copy. Existing databases gain no automatic incidents.
Back up workspace state before upgrading; an older version does not understand incident operations.

The local incident path retains bounded escalation signals. [Named paging destinations](paging.md) add
PagerDuty and GoAlert requests with separate per-destination evidence. Local acknowledgement remains
local. Authenticated incoming acknowledgement, remote status readback and live paging qualification
remain unfinished. Native responder schedules stay with the provider. The local operator console must remain on loopback. Do not expose it as a webhook
callback service.
