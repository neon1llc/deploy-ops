# Frozen CDK assembly and independent ECS release

The Python starter synthesizes the same ECS workload as the
[CloudFormation example](../aws-cloudformation/README.md). It uses CDK's native `CfnInclude`
and `LegacyStackSynthesizer` so this asset-free example does not need a CDK toolkit stack,
cloud lookups, implicit roles, or an extra deployment engine.

Copy `infrastructure/` and the CloudFormation example's `application/` into separate repositories.
Use the CloudFormation workspace and replace its infrastructure adapter/options with:

```toml
[components.infrastructure]
repository = "infrastructure"
kind = "infrastructure"
adapter = "cdk"
outputs = ["Cluster", "Service", "Container", "ImageRepository"]

[components.infrastructure.options]
account_id = "${env.account}"
region = "${env.region}"
stack_name = "deploy-ops-example"
assembly = "cdk.out"
stack_artifact = "ApplicationStack"
capabilities = ["CAPABILITY_IAM"]

[components.infrastructure.options.parameters]
VpcId = "${env.vpc}"
SubnetIds = "${env.subnets}"
InitialImage = "${env.initial_image}"
```

Install the pinned requirements and run `python app.py` inside an isolated build environment,
with `DEPLOY_OPS_EXAMPLE_ACCOUNT` and `DEPLOY_OPS_EXAMPLE_REGION` set to the approved public target.
Do not mount cloud credentials, the user's home, or a Docker socket. Commit or transfer the complete
`cdk.out/` directory with its manifest, templates and metadata into the source snapshot used by DeployOps.
The trusted deployment worker reads those frozen bytes. It never reruns the application after approval.

The `typescript/` source uses the same assembly contract. Copy `stack.json` from the Python example
beside it, install its pinned dependencies, and synthesize with Node.js 24 in the same isolated boundary.
The TypeScript source uses native Node type stripping, with no source compiler hook in the deployment worker.

Both routes share the native change-set, output, failure, readback and owned-deletion behavior.
The separate application release consumes the stack outputs and deploys a prebuilt immutable image.
The infrastructure example creates billable ECS resources and IAM permissions. Its ECR repository and
logs are retained during deletion. Read the CloudFormation example's scope and cleanup notes before use.

For other assemblies, prebuilt file assets and deterministic zip directories are supported only with
explicit `asset_bucket` and slash-terminated `asset_prefix` options. Their exact destination, digest and
`cdk_file_asset` changes are reviewed before conditional S3 publication. Existing objects need matching
SHA-256 checksums. Assets remain in the shared bucket after stack deletion. The adapter does not delete
or bootstrap that bucket. Executable assets, Docker asset builds, implicit role assumptions, unresolved
lookups, nested stacks and stack-to-stack assembly dependencies need separate supported workflows.
