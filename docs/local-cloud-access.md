# Local cloud qualification

The local operator can use an existing AWS IAM-user profile, GCP service-account credential, or Azure service-principal certificate for explicitly scoped, nonproduction runs. Federated CI workers keep using the remote OIDC controller, which has different identity and trust boundaries.

Local access needs an operator-owned TOML grant outside the workspace and source repositories. Set `DEPLOY_OPS_CLOUD_ACCESS_FILE` to its absolute path, and make it and the credential files owner-only (`chmod 600`). The grant holds references, expected public identity, allowed adapters, exact resource types and actions, a change limit, and an expiry. It binds the workspace digest from `deploy-ops config inspect`, so changing configuration disables the old grant.

```toml
schema_version = 1
workspace_digest = "REPLACE_WITH_REVIEWED_WORKSPACE_SHA256"
expires_at = 0 # Replace with the approved expiry as Unix seconds
authorization = "Reference the approved resource, cost, lifetime and cleanup envelope"

[environments.staging]
provider = "aws"
adapters = ["cloudformation", "ecs"]
resource_types = ["AWS::ECS::Service", "application_revision"]
actions = ["create", "update", "read", "no-op"]
max_changes = 2

[environments.staging.identity]
account_id = "123456789012"
principal_arn = "arn:aws:iam::123456789012:user/deployment-test"
region = "us-east-1"
profile = "deployment-test"
credentials_file = "/secure/aws-credentials"
config_file = "/secure/aws-config"
```

This example is inactive on purpose. An approval reference isn't spending approval, and the change limit isn't a billing cap. Supply the exact approved resource types and operations. CloudFormation creates a remote change set during preparation, so the grant must allow that. Deletion needs a grant allowing it and a separate reviewed destroy release. CDK file publication also needs `cdk_file_asset` in the grant and explicit `asset_bucket` and `asset_prefix` options. No bootstrap or role creation is automatic.

AWS uses the selected profile and credential and config files explicitly. It rejects profile helpers, role chaining, and custom endpoints, and verifies the exact account and IAM-user ARN before preparation and execution. It accepts a persistent key pair, not temporary session credentials.

For GCP, use `provider = "gcp"`, `adapters = ["cloud-run"]` and this identity section:

```toml
[environments.staging.identity]
project_id = "example-project"
service_account = "deployment-test@example-project.iam.gserviceaccount.com"
region = "us-central1"
credentials_file = "/secure/service-account.json"
```

The file must identify that service account and project and use Google's standard token endpoint. `GOOGLE_APPLICATION_CREDENTIALS` and `CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE` both reference it. Missing APIs, billing linkage, and runtime permissions need separate qualification, and none is enabled or linked automatically.

For Azure, use `provider = "azure"`, `adapters = ["arm", "bicep", "container-apps"]` and this identity section:

```toml
[environments.staging.identity]
cloud = "AzureCloud"
tenant_id = "22222222-2222-2222-2222-222222222222"
client_id = "33333333-3333-3333-3333-333333333333"
subscription_id = "11111111-1111-1111-1111-111111111111"
resource_group = "deployment-test"
certificate_file = "/secure/deployment-test.pem"
```

The service principal, certificate, and resource group must already exist, with only the permissions needed on the approved group. DeployOps creates no identities, assigns no roles, and registers no providers. The certificate file holds its private key and public certificate in PEM format, as [Azure CLI certificate authentication](https://learn.microsoft.com/en-us/cli/azure/authenticate-azure-cli-service-principal) requires, and must be owner-only and outside the workspace and source repositories. The worker passes only its path to `az login --certificate`. Certificate validity and the shorter grant expiry are separate limits.

Azure CLI uses a private configuration directory under the worker's temporary home, selects `AzureCloud`, disables dynamic extension installation, and checks the subscription, tenant, client ID, and resource-group ID before preview or execution. Install tools and extensions separately. Workspace `subscription_id` and `resource_group` must match the grant, and ARM and Bicep `tenant_id` must too. Sovereign clouds, human login reuse, and client-secret authentication are unsupported. Removing deployment history doesn't remove resources.

Local cloud grants allow CloudFormation, frozen CDK assemblies, ECS, Cloud Run, ARM/Bicep, and Container Apps. They don't allow arbitrary workflows, Terraform or OpenTofu programs, build commands, or synthesis commands. Those need the isolated planning and execution workflow, with builds in a separate credentialless environment that supplies frozen templates, assemblies, or digest-pinned images.

Local grants never enable delegation or production. Every credentialed command checks the active grant. Revocation or expiry stops further commands but can't undo work a provider already accepted, so inspect uncertain results before retrying. Credential references stay out of workspace forms and manifests. Declare only public outputs, and review source first, since a plaintext template can hold sensitive material.

This is a trusted local worker. File permissions, identity checks, and a clean subprocess environment don't isolate hostile same-user processes or restrict an administrator key at IAM level. The grant doesn't enforce account-wide spend, remote deadlines, or automatic cleanup. Use an isolated worker and provider-side controls for those.
