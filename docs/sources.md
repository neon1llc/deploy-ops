# Implementation references

These primary references informed the protocol and integration choices. They are not evidence that this repository has been deployed or independently audited. Reviewed November 2, 2026.

| Topic | Primary reference |
| --- | --- |
| GitHub claims and subject customization | [OIDC reference](https://docs.github.com/en/actions/reference/security/oidc) |
| Pinned reusable workflow identity | [OIDC with reusable workflows](https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-with-reusable-workflows) |
| AWS federation | [AssumeRoleWithWebIdentity](https://docs.aws.amazon.com/STS/latest/APIReference/API_AssumeRoleWithWebIdentity.html) |
| AzureRM OIDC environment | [Provider reference](https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/index) |
| GCP deployment federation | [Deployment pipeline federation](https://docs.cloud.google.com/iam/docs/workload-identity-federation-with-deployment-pipelines) |
| GCP token exchange | [STS token API](https://docs.cloud.google.com/iam/docs/reference/sts/rest/v1/TopLevel/token) |
| GCP Terraform token environment | [Google provider reference](https://registry.terraform.io/providers/hashicorp/google/latest/docs/guides/provider_reference) |
| Saved plans and sensitivity | [Terraform plan](https://developer.hashicorp.com/terraform/cli/commands/plan) |
| Preview schema | [Terraform JSON format](https://developer.hashicorp.com/terraform/internals/json-format) |
| Slack request signatures | [Verify Slack requests](https://docs.slack.dev/authentication/verifying-requests-from-slack/) |

Action references in the included workflows use commit SHAs resolved through the GitHub connector. They are pins for this handoff, not a claim that the corresponding major versions are the newest available releases. Review dependency and action updates before adopting them.
