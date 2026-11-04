# Azure ARM infrastructure and application delivery

This example creates a private Container App in an **existing** managed environment. It exports
the app name, resource group and subscription for an independent `container-apps` release. It is
a configuration example with local contract checks, not a live-qualified Azure deployment.

Copy the infrastructure and application directories into separate committed repositories and update
their workspace paths. Supply a real public image digest, your approved subscription and tenant,
an existing resource group, and a managed environment in the selected region. The example enables
no public ingress and creates no identity, role assignment, registry or managed environment.

The sample uses one replica with 0.25 CPU and 0.5 GiB memory. It can incur charges while it exists.
Approve a bounded test and cleanup scope before running cloud operations. The example contains
placeholder identifiers and images that must be replaced.

With an authenticated Azure execution context, select `staging` in the console or use
`deploy-ops run staging`. Infrastructure preparation freezes the local template and parameters,
runs Azure what-if, and requests manual review. Execution repeats what-if and rejects changed
results before submitting a uniquely named incremental deployment. An application-only release
can reuse retained infrastructure outputs without reapplying the template.

Azure CLI 2.76.0 or later is required. The authenticated tenant, subscription and principal must
remain the same between review and execution. What-if has the same permission requirements as
deployment and can report unresolved or unsupported analysis. This adapter refuses incomplete
analysis rather than treating it as a no-op. What-if remains a prediction, so changes can occur
after revalidation and before Azure accepts the deployment.

After a failed or interrupted write, inspect the recorded deployment identity and resource outcomes
before recovering the release. There is no automatic retry or rollback. The adapter keeps resources
after execution and does not implement destroy. `az deployment group delete` deletes deployment
history, **not the resources**. Use a separately reviewed exact resource inventory for cleanup.

Only public scalar outputs are exported. Secure parameters, nested deployments, deployment scripts,
cross-scope resources and role changes need a separate qualified path. Marking an output as `string`
does not prove that its value is non-secret. Keep secrets out of this example's parameters and outputs.

The implementation uses the native [Azure CLI deployment lifecycle](https://learn.microsoft.com/en-us/cli/azure/deployment/group)
and preserves the limitations documented for [ARM what-if](https://learn.microsoft.com/en-us/azure/azure-resource-manager/templates/deploy-what-if).
