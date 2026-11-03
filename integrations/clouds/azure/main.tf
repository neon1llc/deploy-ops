# integrations/clouds/azure/main.tf - Bind one reviewed subject to an existing scoped Entra application.

terraform {
  required_version = ">= 1.6.0"
  required_providers {
    azuread = {
      source  = "hashicorp/azuread"
      version = ">= 3.0, < 4.0"
    }
  }
}

variable "application_resource_id" {
  type        = string
  description = "Existing application resource ID in /applications/OBJECT_ID form."
}

variable "name" {
  type        = string
  description = "Name this federation for a specific target and planning or execution stage."
}

variable "github_subject" {
  type        = string
  description = "Exact verified sub claim including the pinned reusable workflow and environment."
  validation {
    condition     = strcontains(var.github_subject, "job_workflow_ref:") && strcontains(var.github_subject, "environment:")
    error_message = "Use a customized subject containing the environment and pinned reusable workflow."
  }
}

# Configure resource-group-scoped RBAC separately. This module grants no Azure role
resource "azuread_application_federated_identity_credential" "workflow" {
  application_id = var.application_resource_id
  display_name   = var.name
  description    = "Reviewed DeployOps workflow identity"
  audiences      = ["api://AzureADTokenExchange"]
  issuer         = "https://token.actions.githubusercontent.com"
  subject        = var.github_subject
}
