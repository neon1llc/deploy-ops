# Local blue-green rollout

Stage a static candidate while the baseline stays active, measure readiness, approve a separate route switch, check the new route, keep the previous files, and retire them only through another reviewed operation. The CLI and console share the existing release, static adapter, and approval controller.

This supports regular local static targets. It doesn't route cloud or container traffic, run browser scripts, verify signatures, do weighted canaries, or restore application data.

## Stage a candidate

Deploy the first baseline with normal direct activation. Then set the static component's activation in the guided options or TOML, and validate and save normally:

```toml
[components.web.options]
directory = "dist"
activation = "separate"
```

Keep any existing destination setting in that table. `direct` is the default. An independent static app needs no infrastructure component. If the app has dependencies, a configuration change needs their fresh matching receipts, not one from the previous workspace digest. Saving runs no deployment.

Run the normal preparation, exact approval, and explicit execution for the candidate. It copies reviewed files to its own revision directory without replacing the baseline's active pointer. `outputs.activation = "candidate"` means staging succeeded, not routing. The fixed revision preview can serve the candidate for inspection, while an active-target URL opened from the baseline keeps serving the baseline. Isolated expiring previews keep their direct activation and cleanup.

## Measure, approve, then switch

```bash
deploy-ops rollout prepare CANDIDATE_RELEASE --component web
deploy-ops rollout approve CANDIDATE_RELEASE --digest EXACT_SWITCH_REQUEST_DIGEST
deploy-ops rollout switch CANDIDATE_RELEASE
deploy-ops rollout inspect CANDIDATE_RELEASE
```

Preparation authenticates physical target ownership and the exact baseline and candidate artifacts. A temporary loopback listener must return HTTP 200 with the exact reviewed `/index.html` bytes. This is a measured static HTTP check, not endpoint uptime, JavaScript behavior, package safety, or signer evidence. A failed or empty check produces no approvable switch.

The manual request binds the baseline, candidate, prepared digest, and readiness observation. Approval holds the route unchanged. Execution rechecks that scope and measures the candidate again before claiming target authority. Readiness older than five minutes refuses the claim, so withdraw or reconcile the request and prepare fresh evidence. Delegation can't approve switches.

Execution journals before replacing the pointer, records the replacement, then measures HTTP through the active-target listener. Completion and the post-check are separate fields. Responses accepted earlier may finish from the old revision. See [local serving](local-serving.md).

The console has the same **Measure candidate and prepare switch**, **Approve exact route switch**, and **Execute approved route switch** controls. **Inspect actual route and retained capacity** reads back without replay.

## Keep or retire previous capacity

The switch never removes the baseline. It stays available through its fixed revision preview until explicit retirement:

```bash
deploy-ops rollout check CANDIDATE_RELEASE
deploy-ops rollout prepare-retirement CANDIDATE_RELEASE --component web
deploy-ops rollout approve-retirement CANDIDATE_RELEASE --digest EXACT_RETIREMENT_REQUEST_DIGEST
deploy-ops rollout retire CANDIDATE_RELEASE
```

Retirement needs matching active candidate bytes, the current route, and a post-check under five minutes old. It rechecks the reviewed deletion and retention inventory and measures the active route again before claiming manual deletion authority. Only unchanged generated files from the previous revision are removed. Modified or unrecognized files and directories remain, and the revision root stays as a retained location. Missing files are listed, not counted as deleted. Source artifacts, approval and audit history, and the active candidate stay intact. `retired-retained` means data remains. A successful deployment, TTL, or elapsed time never grants deletion authority.

## Withdraw or recover

Withdraw an unclaimed exact request without changing routes or files:

```bash
deploy-ops rollout cancel CANDIDATE_RELEASE --digest EXACT_SWITCH_REQUEST_DIGEST
deploy-ops rollout cancel-retirement CANDIDATE_RELEASE --digest EXACT_RETIREMENT_REQUEST_DIGEST
```

After an admitted effect, a failure or lost completion stays uncertain, with no automatic rollback, retry, or retirement. Inspect the pointer and both revisions, then reconcile with an explicit note:

```bash
deploy-ops rollout recovery CANDIDATE_RELEASE
deploy-ops rollout reconcile CANDIDATE_RELEASE --note "Inspected actual route and retained files after stopping the original operation"
```

Recovery needs a verified dead owner with matching host, state, and controller admission. Unknown or live ownership blocks it. A written route may be recorded as `switched-inspected` after inspection and a measured check. The prior controller request resolves as failed, without fabricating success. This grants no switch replay or deletion. Partial retirement needs inspection and a newly prepared inventory and approval for any further deletion. Configuration Save is blocked while a rollout or retirement is unresolved.

## Inspection and backup

`rollout inspect` and `rollout recovery` open existing state without current TOML, grants, or an Engine. Inspection hashes a versioned public snapshot of route and capacity observations, controller outcome, and the post-check. It exports no source, raw state, paths, credential homes, or leases. The console offers **Save rollout inspection JSON**:

```bash
deploy-ops rollout inspect CANDIDATE_RELEASE > rollout-inspection.json
deploy-ops rollout read rollout-inspection.json
```

The offline reader validates the allowlisted extent and checksum without accessing state, URLs, tools, or authority. The checksum shows integrity, not authenticity or current health. Deployment inspection formats 1 and 2 refuse separate-activation releases rather than present staging as complete routing. There is no combined offline HTML report for these phases.

Coherent backups include per-operation rollout plans and both retained capacities. A relocated restore stays an inspection copy: absolute target paths aren't rewritten or followed outside that state. Copied PIDs, paths, and digests grant no continuation authority. No listeners are adopted after restart, so start any foreground serving session explicitly.
