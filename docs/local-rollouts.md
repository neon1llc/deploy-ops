# Local blue-green rollout

Stage a static candidate while the baseline remains active, measure readiness, approve a separate route
switch, check the actual new route, retain previous files and retire them only through another reviewed
operation. The CLI and console share the existing release, static adapter and approval controller.
This initial rollout supports regular local static targets. It does not route cloud/container traffic,
run browser scripts, verify signatures, implement weighted canaries or restore application data.

## Stage a candidate

Deploy the first baseline using normal direct activation. After that succeeds, set the static component's
activation choice in the guided options or TOML, then validate and save normally:

```toml
[components.web.options]
directory = "dist"
activation = "separate"
```

Keep any existing destination setting in that table. `direct` remains the default. An independent static
app needs no infrastructure component. If the app has dependencies, changing workspace configuration
requires their fresh matching receipts; include dependencies rather than reusing a receipt from the
previous workspace digest. Saving configuration runs no deployment.

Run the normal preparation, exact approval and explicit execution flow for the candidate. Its deployment
copies reviewed files to its own revision directory without replacing the baseline's active pointer.
`outputs.activation = "candidate"` describes successful staging, not successful routing. The fixed revision
preview can serve the candidate for inspection. An active-target URL opened from the baseline keeps
serving the baseline. Isolated expiring previews use their existing direct activation and cleanup.

## Measure, approve, then switch

```bash
deploy-ops rollout prepare CANDIDATE_RELEASE --component web
deploy-ops rollout approve CANDIDATE_RELEASE --digest EXACT_SWITCH_REQUEST_DIGEST
deploy-ops rollout switch CANDIDATE_RELEASE
deploy-ops rollout inspect CANDIDATE_RELEASE
```

Preparation authenticates both physical target ownership and the exact baseline/candidate artifact
identities. A temporary process-owned loopback listener must return HTTP 200 with the exact reviewed
`/index.html` bytes. This is a measured static HTTP check, not endpoint uptime, JavaScript behavior,
package safety or trusted-signer evidence. No-data or failed checks produce no approvable switch.

The separate manual request binds the baseline, candidate, prepared digest and readiness observation.
Approval holds the route unchanged. Execution rechecks that exact scope and measures the candidate
again before claiming current target authority. Readiness older than five minutes refuses the claim;
withdraw/reconcile the unexecuted request and prepare fresh evidence. Delegation cannot approve switches.

Execution journals before replacing the pointer, records the observed replacement, then measures HTTP
through the shared active-target listener. Successful completion and the measured post-check remain
separate fields. Previously accepted responses may finish from the old revision. See
[local serving](local-serving.md) for fixed versus active-target URLs and foreground ownership.

The console exposes the same **Measure candidate and prepare switch**, **Approve exact route switch**
and **Execute approved route switch** controls. **Inspect actual route and retained capacity** performs
readback without replay. Current request/timing failure leaves retained operation evidence visible.

## Keep or deliberately retire previous capacity

The switch never removes the baseline. It stays available through its fixed revision preview until
explicit retirement. To refresh the measured active-route check and review deletion:

```bash
deploy-ops rollout check CANDIDATE_RELEASE
deploy-ops rollout prepare-retirement CANDIDATE_RELEASE --component web
deploy-ops rollout approve-retirement CANDIDATE_RELEASE --digest EXACT_RETIREMENT_REQUEST_DIGEST
deploy-ops rollout retire CANDIDATE_RELEASE
```

Retirement requires matching active candidate bytes, the exact current route and a post-check no older
than five minutes. It rechecks the full reviewed deletion/retention inventory and measures the active
route again before claiming fresh manual deletion authority. Only unchanged generated files from the
previous revision are removed. Modified or unrecognized files and directories remain, and the revision
root remains as a retained location. Missing files are explicitly listed, not counted as deleted.
Source artifacts, approval/audit history and the active candidate stay intact. `retired-retained` means
data remains. A successful deployment, TTL or elapsed time never grants deletion authority.

## Withdraw or inspect interruption

Withdraw an unclaimed exact request without changing routes or files:

```bash
deploy-ops rollout cancel CANDIDATE_RELEASE --digest EXACT_SWITCH_REQUEST_DIGEST
deploy-ops rollout cancel-retirement CANDIDATE_RELEASE --digest EXACT_RETIREMENT_REQUEST_DIGEST
```

After an admitted effect, failure or loss of completion stays uncertain. No automatic rollback, retry
or retirement follows. Inspect the actual pointer and both revisions, then use the authoritative worker
status and an explicit note:

```bash
deploy-ops rollout recovery CANDIDATE_RELEASE
deploy-ops rollout reconcile CANDIDATE_RELEASE --note "Inspected actual route and retained files after stopping the original operation"
```

Recovery requires a verified stopped/dead owner with matching host, state and controller admission.
Unknown or live ownership blocks reconciliation. A written route may be recorded as `switched-inspected`
after explicit inspection and a measured check; the prior controller request is resolved as failed,
without fabricating successful completion. This grants no switch replay or deletion. Partial retirement
requires inspection and a newly prepared inventory/approval for any further deletion. Configuration Save
is interlocked while a rollout or retirement remains unresolved.

## Retained inspection and backup

`rollout inspect` and `rollout recovery` open existing state without current TOML, grants or an Engine.
Inspection hashes a versioned public snapshot of actual route/capacity observations, controller outcome
and measured post-check. It exports no source, raw state, paths, credential homes or leases. The console
offers **Save rollout inspection JSON** for this separate scope:

```bash
deploy-ops rollout inspect CANDIDATE_RELEASE > rollout-inspection.json
deploy-ops rollout read rollout-inspection.json
```

The offline reader validates the exact allowlisted extent and checksum without accessing recorded state,
URLs, tools or authority. The checksum provides integrity of that extent, not authenticity or current
health. Existing deployment inspection formats 1/2 remain unchanged and refuse separate-activation
releases rather than presenting candidate staging as complete routing. A combined offline HTML report
for these new rollout phases is not supplied.

Coherent backups include exact per-operation rollout plans and both retained capacities. A relocated
restore remains an inspection copy: historical absolute target paths are not rewritten or followed
outside that selected state. Copied PIDs, paths and digests confer no continuation authority. No listeners
are adopted after restart; start any foreground serving session explicitly.
