# Local cloud qualification

The local operator can use an existing AWS IAM-user profile or GCP service-account credential for
explicitly scoped, nonproduction runs. Federated CI workers continue to use the remote OIDC controller.
The two routes retain different identity and trust boundaries.

Local access requires an operator-owned TOML grant outside the workspace and every source repository.
Set `DEPLOY_OPS_CLOUD_ACCESS_FILE` to its absolute path. Make that file and credential files readable
only by their owner (`chmod 600`). The grant contains references, expected public identity, allowed
adapters, exact resource types/actions, a change limit and an expiry. It binds the whole workspace
digest, shown by `deploy-ops config inspect`. Changing configuration disables the old grant.

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

This example is intentionally inactive. A reference to an approval is not spending approval, and the
change limit is not a cloud billing cap. Supply the exact approved resource types and operations.
CloudFormation creates a remote change set during preparation, before execution approval, so the
batch must authorize that operation too. Deletion requires a grant allowing deletion and a separate
reviewed destroy release. CDK file publication also requires `cdk_file_asset` in the grant and explicit
`asset_bucket`/`asset_prefix` workspace options. No bootstrap or role creation is automatic.

AWS uses the selected profile and credential/config files explicitly. It rejects profile helpers,
role chaining and custom endpoint settings, discards competing inherited credentials, and verifies
the exact account and IAM-user ARN before preparation and execution. The current local route accepts
a persistent key pair, not temporary session credentials. Use OIDC for federated execution.

For GCP, use `provider = "gcp"`, `adapters = ["cloud-run"]` and this identity section:

```toml
[environments.staging.identity]
project_id = "example-project"
service_account = "deployment-test@example-project.iam.gserviceaccount.com"
region = "us-central1"
credentials_file = "/secure/service-account.json"
```

The selected file must identify that service account and project and use Google's standard token
endpoint. Both `GOOGLE_APPLICATION_CREDENTIALS` and `CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE` reference
that file. The execution process reads the active project through gcloud before deployment. Missing
APIs, billing linkage and runtime permissions still need separate qualification. No API is enabled
and no billing account is linked automatically. Native gcloud must be installed separately.

Local cloud grants allow CloudFormation, frozen CDK assemblies, ECS and Cloud Run. They do not allow
arbitrary workflows, Terraform/OpenTofu programs, build commands or synthesis commands. Those require
the existing isolated planning/execution workflow. Build in a separate credentialless environment
and provide frozen templates, assemblies or digest-pinned images. Azure currently uses its federated
route; this local grant does not introduce Azure credentials or roles.

The same controller records cloud target identity, manual approval, one-shot claim, expiry and
reconciliation. Local grants never enable delegation or production. Every credentialed command checks
the active grant. Revocation or expiry stops further commands but cannot undo work already accepted
by a provider. Keep an approved cleanup window and inspect uncertain results before retrying.

Credential references are kept out of workspace forms and release manifests. Native command output
is captured privately where it may contain provider values. The operator should declare only public
outputs. A plaintext cloud template can still contain sensitive material, so review source and
outputs before supplying them.

This remains a trusted local worker. Filesystem permissions, explicit identity checks and a clean
subprocess environment do not isolate hostile same-user processes or restrict an administrator key
at IAM level. The grant does not enforce account-wide spend, remote deadlines or automatic cleanup.
Use a separately isolated worker and provider-side controls where those guarantees are required.
