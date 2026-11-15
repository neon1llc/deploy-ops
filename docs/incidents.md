# Local incidents and escalation signals

An incident records who owns the response to a failed or uncertain release. It doesn't change the release outcome, repair a deployment, or show that a responder was paged. The owner is a label, not an authenticated identity.

## Open, acknowledge and resolve

Inspect the release first, then open one incident with an owner, a note, and a bounded escalation policy:

```bash
deploy-ops incidents open RELEASE_ID --owner operations \
  --note 'Investigate the failed readiness check' --interval-seconds 900 --limit 3
deploy-ops incidents list
```

Only failed or uncertain releases qualify. Repeating an open request returns the existing incident, and changing its owner or policy is refused. A resolved incident is never reopened. Existing failures aren't opened automatically.

Use the incident ID and version returned by `list` for decisions:

```bash
deploy-ops incidents acknowledge INCIDENT_ID --version CURRENT_VERSION --note 'Investigating the target'
deploy-ops incidents resolve INCIDENT_ID --version CURRENT_VERSION --note 'Cause inspected and corrected'
```

Acknowledgement stops further escalation. You can resolve from open or acknowledged, which also stops escalation. A stale version is refused, so read the latest state and retry. Record target recovery separately through release reconciliation. Notes stay in local event and audit records and aren't sent in notifications. Keep secrets out of notes and owner labels.

## Explicit escalation checks

```bash
deploy-ops incidents tick
```

There's no hidden scheduler. Run this from a scheduler you manage, or use **Check due escalations** in the console. Each check emits at most one overdue escalation per open incident and schedules the next from the check time. Intervals run from 60 to 86400 seconds, and the limit is 1 to 5. At the limit the incident stays open with no further escalation. Acknowledged or resolved incidents never escalate. Deadlines survive restart.

Escalation raises a level and queues a signal to the configured receiver. It doesn't pick another recipient, consult an on-call schedule, or guarantee a human response.

## Deliver and inspect notifications

```bash
deploy-ops notify --status
# Uses DEPLOY_OPS_NOTIFY_URL; credentials belong in the worker's secret environment.
deploy-ops notify --kind json
```

The JSON, Slack, and ntfy transports accept incident events. HTTP success means the destination accepted the request, not that someone acknowledged it. The outbox reports pending, HTTP-accepted, superseded, and exhausted records. It tracks one state per event, not per destination, so changing `--url` doesn't fan out. Use one receiver that owns downstream routing.

JSON incident notifications hold a complete snapshot with a stable event ID, incident ID, monotonic version, owner, status, escalation level and limit, interval, and timestamps. A receiver must:

1. Deduplicate `event_id` and upsert by `incident_id`, even if its first snapshot is acknowledged or resolved.
2. Apply only a newer `version`, since an older in-flight request can arrive after resolution.
3. Keep transport acceptance distinct from authenticated human acknowledgement.

Delivery is at least once, with up to ten attempts per event. After exhaustion, inspect the receiver and local evidence. Retry reset isn't implemented. A crash after HTTP acceptance but before recording can duplicate an event ID. The endpoint must be HTTPS, redirects aren't followed, and HMAC and bearer credentials come from the worker's environment. In-flight requests can't be recalled.

## Boundaries and storage

Incidents are an additive table in the workspace database, and state changes, audit entries, and outbox records commit together. Back up workspace state before upgrading.

[Named paging destinations](paging.md) add PagerDuty and GoAlert requests with per-destination evidence. Local acknowledgement stays local. Authenticated incoming acknowledgement, remote readback, and live paging qualification are unfinished. Keep the console on loopback and never expose it as a webhook callback.
