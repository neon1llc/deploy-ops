# Infrastructure input

Create the local cluster and apply `resources.yaml` explicitly before delivery. The directory adapter records
a local dependency marker; it does not provision or own Kubernetes resources. The Kubernetes adapter updates
an existing Deployment and does not assume ownership of namespaces, services, volumes or cluster cleanup.
