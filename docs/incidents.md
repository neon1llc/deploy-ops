# Local incidents and escalation signals

An incident records who owns the response to a failed or uncertain release, or to an exact saved later observation of drift or unavailable evidence. It doesn't change the release outcome, repair a deployment, or show that a responder was paged. The owner is a label, not an authenticated identity.

## Open, acknowledge, and resolve

```bash
deploy-ops incidents open RELEASE_ID --owner operations \
  --note 'Investigate the failed readiness check' --interval-seconds 900 --limit 3
deploy-ops incidents list
```

Without an observation reference, only failed or uncertain releases qualify. Repeating an open request returns the existing incident, and changing its source, owner, or policy is refused. A resolved incident is never reopened. Existing failures aren't opened automatically.

Use the incident ID and version from `list` for decisions:

```bash
deploy-ops incidents acknowledge INCIDENT_ID --version CURRENT_VERSION --note 'Investigating the target'
deploy-ops incidents resolve INCIDENT_ID --version CURRENT_VERSION --note 'Cause inspected and corrected'
```

Acknowledgement stops further escalation, and so does resolving from open or acknowledged. A stale version is refused, so read the latest state and retry. Record target recovery separately through release reconciliation. Notes stay in local event and audit records and aren't sent in notifications. Keep secrets out of notes and owner labels.

## Track a later observation and hand off to responders

A successful deployment stays successful when a later observation finds changed or missing files. Run `deploy-ops observe RELEASE_ID`, then use its exact `reference.sequence` and `reference.digest`:

```bash
deploy-ops incidents open RELEASE_ID --owner operations --note 'Investigate later drift' \
  --observation-sequence SAVED_SEQUENCE --observation-digest SAVED_DIGEST
deploy-ops incidents handoff-preview INCIDENT_ID
# Review the active revision, route digest, quiet state and prior routing decisions first.
deploy-ops incidents handoff INCIDENT_ID --version CURRENT_VERSION \
  --route-digest REVIEWED_ROUTE_DIGEST --note 'Queue the reviewed responder route'
```

The reference must identify a saved adverse observation of this release. `unknown` means evidence is unavailable or unsupported, not that the application is unhealthy. Observations cover local files only. One incident per release is supported, and a resolved episode is kept and can't be replaced by another observation or reopened.

In the console, choose **Track this recorded observation** in release details, fill the owner and note, and open the local incident. This queues no native responder pages, and ordinary notification routing stays independent. To page, select the incident, choose **Preview responder route**, review routing and recorded suppression, then use **Queue reviewed responder handoff** with a note and confirmation. Previewing never contacts a destination. Handoff queues work that a running worker may send immediately. It doesn't acknowledge a responder, repair a deployment, or resolve anything.

The handoff digest binds the incident version and active route, including quiet eligibility, so changes need a fresh preview. A quiet period expiring never replays its suppressed opening. Handoff is a deliberate new event using the `incident.opened` routing filters. Existing native bindings or a previous handoff block another one. Use their readback, retry, or resolution actions instead, especially when an earlier outcome is unknown. An earlier ordinary notification may already have reached someone. No recall or duplicate-free delivery is claimed.

## Escalation checks

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

The JSON, Slack, and ntfy transports accept incident events. HTTP success means the destination accepted the request, not that someone acknowledged it. The outbox reports pending, HTTP-accepted, superseded, and exhausted records, with one state per event, not per destination, so changing `--url` doesn't fan out. Use one receiver that owns downstream routing.

JSON incident notifications hold a complete snapshot with a stable event ID, incident ID, monotonic version, owner, status, escalation level and limit, interval, and timestamps. A receiver must:

1. Deduplicate `event_id` and upsert by `incident_id`, even if its first snapshot is acknowledged or resolved.
2. Apply only a newer `version`, since an older in-flight request can arrive after resolution.
3. Keep transport acceptance distinct from authenticated human acknowledgement.

Delivery is at least once, with up to ten attempts per event. After exhaustion, inspect the receiver and local evidence. Retry reset isn't implemented. A crash after HTTP acceptance but before recording can duplicate an event ID. The endpoint must be HTTPS, redirects aren't followed, and HMAC and bearer credentials come from the worker's environment. In-flight requests can't be recalled.

## Storage and boundaries

Incidents are an additive table in the workspace database, and state changes, audit entries, and outbox records commit together. Back up workspace state before upgrading.

[Named paging destinations](paging.md) add per-destination evidence. Local acknowledgement stays local. Authenticated incoming acknowledgement, remote readback, and live paging qualification are unfinished. Keep the console on loopback and never expose it as a webhook callback.
