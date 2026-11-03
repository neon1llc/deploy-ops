# Local Kubernetes delivery

This example updates two existing Deployments in a disposable local kind cluster. Install Docker,
kind and kubectl first. The checked combination is kind 0.31.0, Kubernetes 1.35.0 and kubectl 1.35.0.
Use a new copy of this directory and a cluster name that does not already exist.

Create the explicit prerequisite resources with a dedicated kubeconfig:

```bash
export KUBECONFIG="$PWD/local.kubeconfig"
export DEPLOY_OPS_KUBECONFIG_FILE="$KUBECONFIG"
kind create cluster --name deployops-local --kubeconfig "$KUBECONFIG" \
  --image kindest/node:v1.35.0@sha256:452d707d4862f52530247495d180205e029056831160e22870e37e3f6c1ac31f
chmod 600 "$KUBECONFIG"
kubectl --context kind-deployops-local apply -f infrastructure/resources.yaml
kubectl --context kind-deployops-local -n deployops-local rollout status deployment/web-development
kubectl --context kind-deployops-local -n deployops-local rollout status deployment/web-staging
```

Keep `local.kubeconfig` private and outside source control. The resources contain only this example's
namespace, two Deployments and two Services. Each application has bounded CPU/memory, a nonroot user,
a read-only root filesystem, a memory-backed scratch directory and an HTTP readiness check. Neither
application mounts host directories or the Docker socket. The directory adapter records a local foundation
marker; it does not create or manage the cluster. Review the prerequisite commands separately.

The initial Deployments use the pinned Python 3.12 image. The workspace selects a pinned Python 3.13 image.
Prepare the update:

```bash
deploy-ops plan development
deploy-ops run development --component web
```

Use the console, or inspect the waiting request with `deploy-ops status RELEASE_ID`. Approve each exact
request and resume; the first release needs separate foundation and application approvals:

```bash
deploy-ops approve RELEASE_ID foundation --digest REQUEST_DIGEST
deploy-ops resume RELEASE_ID
# Inspect the newly prepared application request before approving it:
deploy-ops approve RELEASE_ID web --digest REQUEST_DIGEST
deploy-ops resume RELEASE_ID
```

An app-only release can select another immutable image without reapplying the foundation:

```bash
deploy-ops run development --component web --only \
  --image python@sha256:e6dae589c1c05976188fd3bd019afd091445fc533ed2ea29001beddc7368a9c8
```

Approve and resume that release. To promote the first release's Python 3.13 image, establish staging's
foundation with `deploy-ops run staging --component foundation`, approve it and resume it. Then run:

```bash
deploy-ops promote FIRST_RELEASE_ID staging --component web
```

Promotion requires a fresh exact approval. `deploy-ops rollback FIRST_RELEASE_ID development --component web`
also prepares a newly approved image update; it does not undo database changes. The adapter checks the
Deployment resource version before patching, waits for rollout and verifies the selected image.

Inspect HTTP separately. Run the following in a second terminal with the same dedicated kubeconfig, then
visit `http://127.0.0.1:18081` or use `curl --fail`:

```bash
kubectl --kubeconfig /absolute/path/to/local.kubeconfig --context kind-deployops-local \
  -n deployops-local port-forward service/web-development 18081:8080 --address 127.0.0.1
```

Stop port forwarding when finished. Delete only the disposable cluster you created:

```bash
kind delete cluster --name deployops-local
```

Retain the DeployOps ledger and prepared evidence. Remove the dedicated kubeconfig when no longer needed.
Working-tree input is enabled for this local example; use committed repositories for normal delivery.
These checks do not qualify managed cloud clusters, image build/publish, admission policies, high availability
or application data recovery. kind, Kubernetes and the Python images are external tools under their own
licenses; DeployOps does not redistribute them.
