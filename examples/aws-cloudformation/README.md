# CloudFormation infrastructure and ECS application

This example connects a native CloudFormation stack to an independently built ECS application.
It is an example and fixture-checked configuration, not a live-qualified AWS recipe.

Copy `infrastructure/` and `application/` into separate Git repositories beside this workspace file.
Commit both repositories. Fill in the account, region, existing VPC and public subnets, and immutable
image digests in `deploy-ops.toml`. Build and publish the image in a separate isolated job.
The Dockerfile requires `--build-arg PYTHON_IMAGE=python@sha256:<approved-base-digest>`.
Never put cloud writer credentials or the host's credential directories into that build.

The stack creates an ECR repository, ECS cluster and service, a 0.25-vCPU/0.5-GiB Fargate task,
task execution role, log group, and security group. It uses existing public subnet routes, assigns
a public IP for outbound image pulls, and allows no inbound traffic. It creates no load balancer
or NAT gateway. The initial image must implement the sample's Python health check.
The example declares `CAPABILITY_IAM` because its execution role creates permissions.
Use an explicitly approved test scope and cost allowance before preparing it.

Create and advance an infrastructure release first. Preparation creates a remote change set. Review its resource effects and request digest, approve it, and advance again. The release exports `Cluster`, `Service`, `Container`, and `ImageRepository`. Publish the app image to that registry in the independent build job, then create an application-only release with dependencies excluded. Its saved infrastructure outputs feed the ECS adapter without an infrastructure apply. Inspect the service rollout and container health before treating it as useful evidence.

For cleanup, set the infrastructure operation to `destroy` and create a new reviewed release. DeployOps requires its saved creation record and the matching stack ownership tag, then binds the stack state, template, and resource inventory. It never disables termination protection or forces a failed deletion. The template retains ECR images and the log group, which can keep costing money, so remove them separately within the test's cleanup authority.

An abandoned change set can be discarded independently. The empty stack shell stays owned and must be reviewed for deletion. Inspect an interrupted preview using its saved request token. An interrupted execution needs provider readback before another release.
