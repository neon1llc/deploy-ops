# Examples

The [GCP receipt example](gcp-receipts/README.md) connects separate Terraform/OpenTofu and Cloud Run workers,
including later app-only releases. It includes a provider-locked infrastructure root, controller registration and
public output contract. Its native and controller fixtures are verified; live GCP qualification remains separate.

The JSON previews are synthetic fixtures, not exports from the user's accounts. `ecs-update.json` shows a small service update. `denied-iam.json` exercises an authority-change denial. `cost-estimate.json` contains an invented test value solely for exercising the estimator interface.

The `terraform-data` directory can produce a local saved plan without a cloud provider:

```bash
cd examples/terraform-data
tofu init
tofu plan -out=local.plan
tofu show -json local.plan > local-preview.json
```

The local `terraform_data` resource is deliberately outside the cloud normalizer's allowlists. Do not relabel it as an AWS resource to bypass that check. The offline application demo uses synthetic cloud plan data and makes no cloud calls.

The generic preview demonstrates only the manual approval contract. It does not imply that another deployment tool has an implemented or tested executor.
