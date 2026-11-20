# Local Compose delivery

Use a disposable Docker engine you control, with Docker Engine and Compose already installed and available to the local worker. The example creates only the `deployops-local-example` Compose project, binds the app to loopback port 18080, and mounts no host directories or Docker socket. The Python image is pinned to the `python:3.13-alpine` manifest digest. Review image updates before changing it, since a digest pin doesn't prove vulnerability status.

Copy this whole directory to a new location, then run from the copy:

```bash
deploy-ops plan development
deploy-ops run development
```

The release waits for approval. Use the console, or read the digest with `deploy-ops status` and approve each waiting component before resuming:

```bash
deploy-ops approve RELEASE_ID foundation --digest REQUEST_DIGEST
deploy-ops resume RELEASE_ID
# Inspect the next request, then approve the application:
deploy-ops approve RELEASE_ID web --digest REQUEST_DIGEST
deploy-ops resume RELEASE_ID
curl --fail http://127.0.0.1:18080
```

Change the page text in `application/compose.yaml`, then prepare an app-only update:

```bash
deploy-ops run development --component web --only
```

Approve and resume it. The app changes and the infrastructure receipt is reused, and Compose waits for the container health check. Working-tree inputs are enabled on purpose for this trusted local example. Use committed inputs for normal delivery.

When finished, stop only this example's project:

```bash
docker compose -p deployops-local-example -f application/compose.yaml down
```

Keep the DeployOps ledger and prepared files for inspection. This doesn't cover cloud identity, container build or publish, promotion, or database rollback. Compose and the Python image are external, under their own licenses, and not bundled.
