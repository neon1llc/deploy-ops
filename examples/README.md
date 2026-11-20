# Examples

The [GCP receipt example](gcp-receipts/README.md) connects separate Terraform/OpenTofu and Cloud Run workers, including later app-only releases. It has a provider-locked infrastructure root, controller registration, and a public output contract. Its native and controller fixtures are verified. Live GCP qualification is separate.

The JSON previews are synthetic fixtures, not exports from your accounts. `ecs-update.json` is a small service update, `denied-iam.json` exercises an authority-change denial, and `cost-estimate.json` holds an invented value that only exercises the estimator.

The `terraform-data` directory can produce a local saved plan without a cloud provider:

```bash
cd examples/terraform-data
tofu init
tofu plan -out=local.plan
tofu show -json local.plan > local-preview.json
```

The local `terraform_data` resource is outside the cloud normalizer's allowlists on purpose. Don't relabel it as an AWS resource to bypass that check. The offline application demo uses synthetic cloud plan data and makes no cloud calls.

The generic preview shows only the manual approval contract. It doesn't mean another deployment tool has a tested executor.
