# Native infrastructure

DeployOps supports bounded CloudFormation, AWS CDK, ARM, and Bicep paths alongside Terraform and OpenTofu. Each uses the shared release, approval, and public-output model. The native adapters have local contract checks only. No complete live AWS or Azure deployment is qualified.

| Input | Preview and execution | Supported boundary |
|---|---|---|
| Terraform/OpenTofu source | Saved binary plan, exact engine/provider lock and backend checks | Existing isolated planning/execution workflow |
| CloudFormation JSON or YAML | Create a native change set, review its exact ID, then execute it | One stack, explicit IAM capabilities and execution role, selected public outputs |
| CDK cloud assembly | Freeze selected template/assets, then use the CloudFormation change set | Explicit account/region, assembly schema major 1 through 55, file/zip assets |
| ARM JSON | Freeze template/parameters, review what-if, repeat prediction before submission | Incremental deployment into one existing resource group |
| Compiled Bicep JSON | Same ARM lifecycle, retaining native compiler metadata | Compile in a separate build stage; never rerun source after approval |

See the [configuration reference](configuration.md) for fields. Credentials and access grants stay outside the forms. Unsupported combinations fail before execution.

## CloudFormation

Use AWS CLI v2 and set `account_id`, `region`, `stack_name`, and a repository-relative `template`. YAML templates need PyYAML. Inline templates are limited to 51,200 bytes. Public `parameters`, `tags`, a `role_arn`, and IAM `capabilities` are optional, and the adapter never adds capabilities to make a rejected deployment pass.

`deploy-ops plan` only describes the release graph. Preparing with `run` creates a remote CloudFormation change set and, for a new stack, an empty review stack, so **preparation needs cloud mutation authority** before execution is approved. Include that in a test's scope and cleanup plan.

Approval binds the change-set ID, frozen template, parameters, target, tool version, and observed stack state. Apply rejects changed evidence, executes that change set once, and waits for the expected outcome. No-change previews return the existing outputs. A failed update, rollback, or timeout isn't reported as delivery.

Macros, transforms, custom resources, and nested stacks are unsupported. Don't put secrets in parameters or select secret-bearing outputs. Automatic checks can't prove an arbitrary output is public.

To destroy a stack, set `operation = "destroy"` and prepare a new reviewed release. It needs retained DeployOps creation evidence and the matching ownership tag and stack ID, and binds the current inventory, template, and state. It never disables termination protection or forces deletion. `DeletionPolicy` retention and snapshots stay and can keep incurring charges. Pre-existing stacks can't be destroyed.

## AWS CDK

Synthesize in a separate isolated builder, then freeze the complete cloud assembly. Set `assembly` to that repository-relative directory and `stack_artifact` to the stack's manifest ID. Account, region, and stack name must match the target. DeployOps never runs `cdk deploy`, hotswap, the source application, or an asset build after approval.

Already-built files and deterministic zip packaging are supported. Each asset needs one destination matching `asset_bucket` and a slash-terminated `asset_prefix`. The approval includes these `cdk_file_asset` writes. Publication checks the bucket owner, uses a conditional create, and verifies SHA-256. An existing object with different bytes is refused.

Command events name allowlisted operations such as `azure.deployment.group.what-if` and the exit code, never arguments or raw errors. Docker and executable assets, unresolved context, dependent stacks, implicit role assumptions, and unsupported assembly instructions are refused. No bootstrap stack, bucket, registry, or IAM role is created or changed, and published assets and bootstrap resources survive stack deletion. The [CDK example](../examples/aws-cdk/README.md) uses an asset-free ECS template and `LegacyStackSynthesizer`. Its Python and TypeScript variants synthesized with `aws-cdk-lib` 2.275.0 and Node.js 24.19.0 under local restrictions. That qualifies those samples locally, not every construct library or a live AWS deployment.

## ARM and Bicep

Use Azure CLI 2.76.0 or later with an authenticated public-cloud subscription and tenant. Set `subscription_id`, `tenant_id`, `resource_group`, and a local JSON `template`. Bicep also needs native compiler metadata in that JSON, which isn't an attestation. Both bind the frozen file digests, public parameters, tool version, and authenticated principal.

What-if predicts changes and isn't a saved executable plan. The adapter keeps a digest of the complete prediction and repeats it just before deployment. Changed, incomplete, ignored, unsupported, or unresolved analysis is refused. An unchanged prediction still can't prevent a concurrent external change.

Only incremental deployments into one existing resource group with public scalar parameters are supported. Secure parameters, deployment extensions, nested deployments, scripts, cross-scope resources, and role changes are refused. A local operation record is saved before submission, and existing deployment names are never overwritten.

The adapter waits for Azure's result and returns selected public scalar outputs. After an interruption, inspect the named deployment instead of replaying creation. There is no automatic rollback or resource destruction, and deleting an ARM deployment record deletes history, not resources.

If preparation fails before reviewed evidence is saved and no local submission record exists, provider status reports `no-submission-recorded` with the deployment target. This doesn't verify remote state or clear the release's uncertainty. Inspect the target and fix preparation before explicit recovery and a fresh release. Automatic retry stays off.

The [ARM](../examples/azure-arm/README.md) and [Bicep](../examples/azure-bicep/README.md) examples feed a separate Container Apps release. The Bicep sample compiled locally with v0.41.2. Its application replica can still incur charges, so review the prerequisites and cleanup limits.

## Inspect and recover

```sh
deploy-ops provider inspect RELEASE COMPONENT
deploy-ops provider discard RELEASE COMPONENT
```

Inspect reads provider outcomes without replaying a write. For CloudFormation and CDK, discard removes only the exact unexecuted change set, and the stack, published assets, and retained resources remain. ARM and Bicep have no discard action. Inspection records an observation. It doesn't change the release outcome or remove the need for an explicit recovery decision.

If preparation failed before an approval envelope was saved, keep the operation journal and inspect the provider identity or request token before creating a fresh release. A cancelled local process doesn't guarantee the provider stopped.
