# examples/terraform-data/main.tf - Exercise a local IaC plan without a cloud provider.


# Require a runtime that provides the built-in terraform_data resource
terraform {
  required_version = ">= 1.6.0"
}

# This fixture is not an AWS resource and is intentionally outside cloud policy allowlists
resource "terraform_data" "example" {
  input = "local plan fixture"
}
