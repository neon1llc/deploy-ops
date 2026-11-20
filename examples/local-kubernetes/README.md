# Local Kubernetes delivery

This example updates two existing Deployments in a disposable local kind cluster. Install Docker, kind, and kubectl first. The checked combination is kind 0.31.0, Kubernetes 1.35.0, and kubectl 1.35.0. That workload proof is historical v0.7 evidence. The current [connection and UID profile](../../docs/kubernetes.md) has separate native-client qualification and still needs real-cluster workload qualification. Use a new copy of this directory and a cluster name that doesn't exist.

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

Keep `local.kubeconfig` private and outside source control. The resources hold only this example's namespace, two Deployments, and two Services. Each application has bounded CPU and memory, a nonroot user, a read-only root filesystem, a memory-backed scratch directory, and an HTTP readiness check, and mounts no host directories or Docker socket. The directory adapter records a local foundation marker and doesn't create or manage the cluster. Review the prerequisite commands separately.

The initial Deployments use the pinned Python 3.12 image. The workspace selects a pinned Python 3.13 image.
Prepare the update:

```bash
deploy-ops plan development
deploy-ops run development --component web
```

Use the console, or inspect the waiting request with `deploy-ops status RELEASE_ID`. The first release needs separate foundation and application approvals:

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

Promotion needs a fresh exact approval. `deploy-ops rollback FIRST_RELEASE_ID development --component web` also prepares a newly approved image update and doesn't undo database changes. The adapter checks the connection, Deployment UID, resource version, and container before patching, waits for rollout, and verifies the same object and image.

Inspect HTTP separately. In a second terminal with the same dedicated kubeconfig, run the following, then visit `http://127.0.0.1:18081` or use `curl --fail`:

```bash
kubectl --kubeconfig /absolute/path/to/local.kubeconfig --context kind-deployops-local \
  -n deployops-local port-forward service/web-development 18081:8080 --address 127.0.0.1
```

Stop port forwarding when finished, then delete only the disposable cluster you created:

```bash
kind delete cluster --name deployops-local
```

Keep the DeployOps ledger and prepared evidence, and remove the dedicated kubeconfig when done. Working-tree input is enabled for this local example, so use committed repositories for normal delivery. These checks don't qualify managed clusters, image build and publish, admission policies, high availability, or data recovery. kind, Kubernetes, and the Python images are external tools under their own licenses, and DeployOps doesn't redistribute them.
