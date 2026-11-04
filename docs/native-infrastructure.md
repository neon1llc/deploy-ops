# Native infrastructure

DeployOps supports bounded CloudFormation, AWS CDK, ARM and Bicep paths alongside Terraform/OpenTofu.
Each uses the shared release, approval and public-output model. Their provider semantics remain distinct.
The native adapters have local contract checks. No complete live AWS or Azure deployment is qualified yet.

| Input | Preview and execution | Supported boundary |
|---|---|---|
| Terraform/OpenTofu source | Saved binary plan, exact engine/provider lock and backend checks | Existing isolated planning/execution workflow |
| CloudFormation JSON or YAML | Create a native change set, review its exact ID, then execute it | One stack, explicit IAM capabilities and execution role, selected public outputs |
| CDK cloud assembly | Freeze selected template/assets, then use the CloudFormation change set | Explicit account/region, assembly schema major 1 through 55, file/zip assets |
| ARM JSON | Freeze template/parameters, review what-if, repeat prediction before submission | Incremental deployment into one existing resource group |
| Compiled Bicep JSON | Same ARM lifecycle, retaining native compiler metadata | Compile in a separate build stage; never rerun source after approval |

Use the [configuration reference](configuration.md) for fields. Native infrastructure forms show effective
component/environment values and write deliberate overrides into the TOML draft. Credentials and access
grants remain outside these forms. Unsupported combinations fail before execution.

## CloudFormation

Use AWS CLI v2 and configure the expected `account_id`, `region`, `stack_name` and repository-relative
`template`. JSON needs no extra parser. YAML templates require PyYAML and support reviewed native
intrinsic tags. Inline templates are limited to 51,200 bytes. Public `parameters`, `tags`, an explicitly
selected `role_arn` and IAM `capabilities` are optional. The adapter does not add IAM capabilities to make
a rejected deployment pass.

`deploy-ops plan` only describes the release graph. Preparing a release through `run` creates a remote
CloudFormation change set and, for a new stack, an empty review stack. **Preparation therefore requires
cloud mutation authority**, even before resource execution is approved. Keep that distinction in a test
batch's scope and cleanup plan.

Approval binds the change-set identifier, frozen template, parameters, target, tool version and observed
stack state. Apply rejects changed evidence, executes that change set once and waits for the expected
stack outcome with its operation token. No-change previews return the selected existing outputs without
executing a change set. Replacements and deletions remain visible for review. A failed update, rollback
or timeout is not reported as successful delivery.

Macros, transforms, custom resources and nested stacks are outside this route. Do not put secret values
in workspace parameters or select secret-bearing outputs. Automatic checks are limited and do not prove
that an arbitrary template output is public.

To destroy a stack, set `operation = "destroy"` and prepare a new reviewed release. The adapter requires
both retained DeployOps creation evidence and the matching provider ownership tag and stack ID. It binds
the current resource inventory, template and state before deletion. It never disables termination
protection or forces deletion. `DeletionPolicy` retention and snapshots remain visible and can continue
to incur charges. Destruction is not available for an arbitrary pre-existing stack.

The [CloudFormation example](../examples/aws-cloudformation/README.md) creates an ECS workload and exports
coordinates for a separate application release. Its IAM permissions, public egress, billable Fargate task
and retained logs/images require a reviewed scope and cleanup plan.

## AWS CDK

Synthesize in a separate isolated builder, then commit or otherwise freeze the complete cloud assembly
with its inputs. Set `assembly` to that repository-relative directory and `stack_artifact` to the selected
stack's manifest identifier. Its account, region and stack name must match the configured target.
DeployOps never invokes `cdk deploy`, hotswap, the source application or an asset build after approval.

The adapter supports already-built files and deterministic zip packaging. Each asset needs one explicit
destination matching the configured `asset_bucket` and slash-terminated `asset_prefix`. The approval
includes these `cdk_file_asset` writes. Publication checks the expected bucket owner, uses a conditional
create, and verifies SHA-256. An existing object with different bytes is refused.

Docker/executable assets, unresolved context, dependent stacks, implicit role assumptions and unsupported
assembly instructions are refused. Deploy dependent stacks separately. Existing bootstrap version metadata
can be checked, but no bootstrap stack, bucket, registry or IAM role is created or changed automatically.
The [CDK example](../examples/aws-cdk/README.md) uses the same asset-free ECS template and the native
`LegacyStackSynthesizer`. Its Python and TypeScript variants both synthesized successfully with
`aws-cdk-lib` 2.275.0 and Node.js 24.19.0 under local filesystem/network restrictions. This qualifies
those sample assemblies locally, not every construct library or a live AWS deployment.

Published assets and shared bootstrap resources are retained. Deleting a CloudFormation stack or discarding
its change set does not remove them. Use a separately reviewed ownership inventory for asset cleanup.

## ARM and Bicep

Use Azure CLI 2.76.0 or later with an authenticated public-cloud subscription and tenant. Configure
`subscription_id`, `tenant_id`, `resource_group` and a local JSON `template`. The Bicep path additionally
requires native compiler metadata in that JSON. Metadata is not an attestation. Both paths bind the
frozen file digests, public parameters, tool version and authenticated principal.

What-if predicts changes and is not a saved executable plan. The adapter requests full provider validation,
retains a digest of the complete prediction and repeats it immediately before deployment. Changed,
incomplete, ignored, unsupported or unresolved analysis is refused. Even an unchanged prediction cannot
prevent a concurrent external resource change after revalidation.

The adapter supports incremental deployments into one existing resource group and public scalar parameters. Secure parameters,
deployment extensions, nested deployments, scripts, cross-scope resources and role changes are refused.
The operation name receives a release-specific suffix. A local operation record is saved before submission,
and existing deployment names are never overwritten by this path.

The adapter waits for Azure's deployment result and returns selected public scalar outputs. This is
infrastructure outcome evidence. The dependent application's runtime adapter checks its revision health.
After interruption, inspect the named deployment instead of replaying its creation. There is no automatic
rollback or resource destruction. Deleting an ARM deployment record deletes history, not resources.

The [ARM example](../examples/azure-arm/README.md) and [Bicep example](../examples/azure-bicep/README.md)
feed a separate Container Apps release. The Bicep sample has been compiled locally with v0.41.2.
It creates no public ingress, managed environment, identity or registry, but its application replica
can still incur charges. Review the examples' placeholders, prerequisites and cleanup limits before use.

## Inspect and recover

The console and CLI use the same provider operations:

```sh
deploy-ops provider inspect RELEASE COMPONENT
deploy-ops provider discard RELEASE COMPONENT
```

Inspect reads provider outcomes without replaying a write. CloudFormation/CDK discard removes only the
exact unexecuted change set. Its stack, published assets and retained resources remain. ARM/Bicep has no
discard action. Provider inspection records an observation; it does not retroactively change the release
outcome or remove the need for an explicit recovery decision.

If preparation failed before an approval envelope was saved, preserve the operation journal and inspect
the recorded provider identity or request token before creating a fresh release. A cancelled local process
does not guarantee that the provider stopped. Keep uncertain outcomes uncertain until readback establishes
what happened.
