# Kubernetes image delivery

DeployOps updates one existing Deployment through a dedicated scoped kubeconfig. It does not create a
cluster, namespace, Deployment, Service, RBAC binding or admission policy. Configure `context`,
`namespace`, `deployment`, `container` and an immutable `image@sha256` in the component options, and
supply `DEPLOY_OPS_KUBECONFIG_FILE` outside source control. Keep its authority limited to this workload.
The [local example](../examples/local-kubernetes/README.md) describes separate disposable prerequisites.

Preparation selects the native context once with `kubectl config view --minify --flatten --raw`.
The credential-bearing result stays in process memory and anonymous native stdin streams; it is not
written to prepared artifacts, journals, public output or backup. Native workload commands use that
same snapshot through `/dev/stdin`, so a changed on-disk alias cannot redirect later commands in the
operation. Current support requires POSIX and native HTTPS with TLS verification enabled. This
profile does not alter host trust, personal configuration or existing permissions.

The review binds the explicit context, cluster label, server, namespace, Deployment, container,
TLS server name, public CA digest or native-system-roots choice, proxy configuration digest and client
version. The context user is a label, not verified principal identity. Raw proxy values and credentials
are not review data. This binds selected client configuration, not a universal attestation of the
peer cluster, system root store, authentication plugin or remote infrastructure ownership.

The existing Deployment must expose its exact UID, resource version and selected container baseline.
Before execution, the newly selected connection must match the review. A single JSON Patch tests UID,
resource version, container name and previous image before replacing the image. A recreated object
with the same name/version/image therefore does not acquire old approval. Native rollout status and
final UID, image and observed-generation/replica readback remain separate checks. Same-image delivery
observes readiness without a patch. No command creates or adopts a missing object.

## Operate and recover

Use `deploy-ops run ENVIRONMENT --component COMPONENT --only` to prepare, inspect the selected connection
and UID in the desktop release view or CLI status, then approve the exact digest. Waiting returns exit3.
`deploy-ops approve RELEASE COMPONENT --digest DIGEST` holds approval; `deploy-ops resume RELEASE`
is a separate execution action. Current compatibility failures are also projected into console review;
a displayed approval does not override native admission checks.

The public `kubernetes-operation.json` records intent before the patch, acknowledgement separately and
bounded readback afterward. Once any operation record exists, apply refuses to resend. After a timeout,
interruption or lost response, use `deploy-ops provider inspect RELEASE COMPONENT` or **Read provider
status** in the console. Readback rechecks the selected connection before contacting a cluster. A
changed connection returns `connection-changed` without a cluster read; a different UID is
`object-replaced`. A converged observation after a lost acknowledgement does not invent controller
success, restore a lease or authorize replay. Follow the existing [recovery workflow](workflows.md).

Coherent private backup includes the public native journal and excludes worker homes. Restored copies
remain inspection-only. Deployment inspection format2 cannot represent this new connection/UID
operation and explicitly refuses it; use current native readback or coherent private backup instead.
Historical inspection formats retain their earlier meanings. New writers refuse old Kubernetes
mutation evidence before claim/native use; see [migration](migration.md).

## Qualification

The current source contract has actual kubectl v1.36.1 checks against an owned TLS API fixture for
pinned alias behavior, atomic UID refusal and one accepted patch with a lost acknowledgement followed
by read-only recovery. All fixture sockets and temporary TLS keys were removed. The earlier fixture
omitted watch-list initial bookmarks; the corrected fixture follows [Kubernetes API concepts](https://kubernetes.io/docs/reference/using-api/api-concepts/).
The native selection flags follow [kubectl config view](https://kubernetes.io/docs/reference/kubectl/generated/kubectl_config/kubectl_config_view/).
No upstream implementation was copied or bundled.

These checks exercise the real native client and HTTP/TLS protocol, not a Kubernetes controller,
application readiness probe, managed cluster, admission policy, actual RBAC or workload HTTP delivery.
The current session has no dedicated real-cluster reference and its local Docker daemon is unavailable;
no personal context was adopted and no daemon or cluster was started. Earlier v0.7 VM workload proof
remains historical and does not qualify this new contract. Current mounted browser, keyboard/layout,
assistive technology and human acceptance remain open. See [verification](verification.md).
