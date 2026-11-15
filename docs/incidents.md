# Local incidents and escalation signals

An incident records who owns the response to a failed or uncertain release. It doesn't change the release outcome, repair a deployment, or show that an external responder was paged. The CLI and loopback console share the same incident records, audit trail, and notification outbox. The owner is a descriptive team or person label, not an authenticated identity.

## Open, acknowledge and resolve

Inspect the release first, then open one incident with an owner, a note and a bounded escalation policy:

```bash
deploy-ops incidents open RELEASE_ID --owner operations \
  --note 'Investigate the failed readiness check' --interval-seconds 900 --limit 3
deploy-ops incidents list
```

Only failed or uncertain releases are eligible. Repeating the same open request returns the existing incident, and changing its owner or policy is refused. A resolved incident is never reopened implicitly. Existing failures aren't opened automatically.

Use the incident ID and version returned by `list` for decisions:

```bash
deploy-ops incidents acknowledge INCIDENT_ID --version CURRENT_VERSION --note 'Investigating the target'
deploy-ops incidents resolve INCIDENT_ID --version CURRENT_VERSION --note 'Cause inspected and corrected'
```

Acknowledgement stops further escalation. You can resolve from open or acknowledged, and resolution also stops escalation. A stale version is refused so a concurrent change can't be overwritten. Read the latest state and retry. Record target recovery separately with the normal release reconciliation workflow. Notes stay in local event and audit records and aren't sent in notifications. Don't put secrets in notes or owner labels.

In the browser, select a failed or uncertain release and choose **Track a local incident**, or open **Local incidents**. Select an incident to acknowledge or resolve it with a note. Deployment, incident, and notification transport status are shown separately.

## Explicit escalation checks

```bash
deploy-ops incidents tick
```

There's no hidden scheduler. Run this command from a scheduler you manage, or use **Check due escalations** in the console. Each check emits at most one overdue escalation per open incident and schedules the next from the check time, so a long interruption causes no catch-up burst. Intervals run from 60 to 86400 seconds, and the escalation limit is 1 to 5. At the limit the incident stays open and visible with no further escalation. Acknowledged or resolved incidents never escalate. Deadlines survive restart.

Escalation raises a level and queues a signal to the configured receiver. It doesn't pick another recipient, consult an on-call schedule, or guarantee a human response.

## Deliver and inspect notifications

```bash
deploy-ops notify --status
# Uses DEPLOY_OPS_NOTIFY_URL; credentials belong in the worker's secret environment.
deploy-ops notify --kind json
```

The JSON, Slack, and ntfy transports accept incident events. HTTP success means the destination accepted the request, not that someone acknowledged the incident. The outbox reports pending, HTTP-accepted, superseded, and exhausted records. It tracks one delivery state per event, not per destination, so changing `--url` doesn't fan out. Use one receiver that owns downstream routing.

JSON incident notifications contain a complete snapshot, stable event ID, incident ID, monotonic version,
owner, status, escalation level/limit, interval and timestamps. A receiver must:

1. Deduplicate `event_id` and upsert by `incident_id`, even if its first snapshot is acknowledged/resolved.
2. Apply only a newer `version`; an older in-flight request can arrive after resolution.
3. Keep transport acceptance distinct from authenticated human acknowledgement.

Obsolete unsent incident snapshots are suppressed before sending. In-flight requests can't be recalled. Slack and ntfy messages show the incident ID, version, and state for a human reader and don't implement a remote incident state machine. The endpoint must be HTTPS, and redirects aren't followed. Optional HMAC and bearer credentials come from the notification worker's environment, never from repository source or the browser.

Delivery is at least once, with up to ten attempts reserved per event, including attempts interrupted before their outcome was recorded. After exhaustion, inspect the receiver and local evidence. Automatic retry reset isn't implemented. A crash after HTTP acceptance but before recording it can produce a duplicate with the same event ID.

## Boundaries and storage

Incidents are an additive table in the workspace database. State changes, audit entries, and outbox records commit together. They're separate from whole-release snapshots so a worker can't overwrite an acknowledgement with an older release copy. Back up workspace state before upgrading, because older versions don't understand incident operations.

[Named paging destinations](paging.md) add PagerDuty and GoAlert requests with per-destination evidence. Local acknowledgement stays local. Authenticated incoming acknowledgement, remote status readback, and live paging qualification are unfinished. Responder schedules stay with the provider. Keep the console on loopback and don't expose it as a webhook callback service.
