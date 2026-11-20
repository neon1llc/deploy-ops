# Local static serving

Serve deployed static files on a separate loopback origin. The foreground CLI or console owns the listener, and stopping it closes its listeners without deleting deployed revisions. Application content gets no console token, cookies, or cross-origin API access.

## Fixed revision or active target

The default URL stays on the selected successful revision, even after another release becomes active:

```bash
deploy-ops preview RELEASE_ID --component web --port 8000
```

For a regular local target with reviewed physical ownership, use one URL that follows successful approved activations of the same project, environment, and component:

```bash
deploy-ops preview RELEASE_ID --component web --active-target --port 8001
```

In the console, use **Serve this static revision** or **Serve the active target** in the component detail. Both offer a separate content link and **Stop serving**. The active-target session shows the observed release and artifact digest. Repeated starts for the same physical target return the existing session, including from a later active release, and a requested alternate port doesn't move an owned listener. Stop that session first.

Preparing or approving a new release doesn't switch the target. Explicit execution activates the new revision, and the target URL then follows its successful completion. An in-flight response may finish from the previous revision. The fixed revision URL keeps its old bytes. Neither mode deploys, approves, repairs a pointer, or retires files.

The API uses authenticated `POST /api/serving/start` with `release`, `component`, optional `port`, and `mode` equal to `revision` (default) or `target`. `GET /api/serving` lists only this console's listeners. Stop with `POST /api/serving/stop` and the session's `id`.

## Unavailable evidence and restart

The target listener authenticates the reviewed physical target and logical owner, current pointer, successful component output, and latest recorded activation. Missing, uncertain, foreign, modified, or externally reverted evidence returns HTTP 410, without falling back to an older release. Review that release's retained state before a new deployment. Restoring the exact valid evidence can make a later read work, but the listener recovers nothing itself.

Each newly selected revision's bounded inventory must match the reviewed digest. At most two inventories are cached per session. Every requested file is freshly read and hashed, so changes to an unrelated cached file surface when it is requested, not through continuous health checks. Status reports evidence and lifetime, not workload health or uptime. There is no directory listing, symlink serving, or arbitrary file path input.

`--retained` opens existing state without current configuration or execution registration and can combine with `--active-target` for original-location inspection. A relocated backup keeps its recorded original paths and gains no target continuation authority. Isolated expiring previews and legacy targets without reviewed ownership support fixed revision serving only.

A new console or CLI process adopts no prior listener, PID, or port, so start a new foreground session after restart. Serving grants no route or retirement authority. For candidate readiness, a separately approved route switch, a measured post-check, and deliberate previous-capacity retirement, use the [local rollout workflow](local-rollouts.md). No container or cloud routing is implied.
