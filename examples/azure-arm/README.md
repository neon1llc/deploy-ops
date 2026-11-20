# Azure ARM infrastructure and application delivery

This example creates a private Container App in an **existing** managed environment and exports the app name, resource group, and subscription for an independent `container-apps` release. It's a configuration example with local contract checks, not a live-qualified Azure deployment.

Copy the infrastructure and application directories into separate committed repositories and update their workspace paths. Supply a real public image digest, your approved subscription and tenant, an existing resource group, and a managed environment in the selected region. The example enables no public ingress and creates no identity, role assignment, registry, or managed environment. It has placeholder identifiers and images that must be replaced.

The sample uses one replica with 0.25 CPU and 0.5 GiB memory and can incur charges while it exists. Approve a bounded test and cleanup scope before running cloud operations.

With an authenticated Azure context, select `staging` in the console or run `deploy-ops run staging`. Preparation freezes the local template and parameters, runs what-if, and requests manual review. Execution repeats what-if and rejects changed results before submitting a uniquely named incremental deployment. An application-only release reuses the retained outputs without reapplying the template.

Azure CLI 2.76.0 or later is required, and the tenant, subscription, and principal must stay the same between review and execution. What-if needs the same permissions as deployment and can report unresolved or unsupported analysis, which this adapter refuses rather than treat as a no-op. What-if is a prediction, so changes can still occur before Azure accepts the deployment.

After a failed or interrupted write, inspect the recorded deployment identity and resource outcomes before recovering the release. There is no automatic retry, rollback, or destroy. `az deployment group delete` deletes deployment history, **not the resources**, so clean up with a separately reviewed resource inventory.

Only public scalar outputs are exported, and typing an output as `string` doesn't make it non-secret. Keep secrets out of parameters and outputs. Secure parameters, nested deployments, deployment scripts, cross-scope resources, and role changes need a separate qualified path.

See the [Azure CLI deployment lifecycle](https://learn.microsoft.com/en-us/cli/azure/deployment/group) and the limitations of [ARM what-if](https://learn.microsoft.com/en-us/azure/azure-resource-manager/templates/deploy-what-if).
