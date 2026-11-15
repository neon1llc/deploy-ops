# Local Compose delivery

Use a disposable Docker engine you control. Docker Engine and Compose must already be installed and available to the local worker. This example creates only the `deployops-local-example` Compose project, binds the app to loopback port 18080, and mounts no host directories or Docker socket into the app. The Python image is pinned to the observed `python:3.13-alpine` manifest digest. Review image updates before changing it. A digest pin doesn't prove vulnerability status.

Copy this whole directory to a new location, then run from the copy:

```bash
deploy-ops plan development
deploy-ops run development
```

The release waits for exact approval. Use the console, or read the request digest with `deploy-ops status`
and approve each waiting component before resuming:

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

Approve that exact request and resume it. The app changes and the successful infrastructure receipt is reused. Compose waits for the configured container health check. Working-tree inputs are enabled on purpose because this is a trusted local example. Use committed repository inputs for normal delivery.

When finished, stop only this example's project:

```bash
docker compose -p deployops-local-example -f application/compose.yaml down
```

Keep the DeployOps ledger and prepared files for inspection. This example doesn't cover cloud identity, container build or publish, Compose artifact promotion, or database rollback. Compose runs as an external engine under its own license, and DeployOps doesn't bundle it or the Python image.
