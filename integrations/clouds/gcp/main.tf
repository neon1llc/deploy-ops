# integrations/clouds/gcp/main.tf - Isolate each planning or execution identity in its own pool.

terraform {
  required_version = ">= 1.6.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 6.0, < 8.0"
    }
  }
}

variable "project_id" {
  type        = string
  description = "Project that owns the workload identity pool."
}

variable "pool_id" {
  type        = string
  description = "Use a distinct pool for each target and stage."
}

variable "repository_id" {
  type        = string
  description = "Immutable GitHub repository ID."
}

variable "owner_id" {
  type        = string
  description = "Immutable GitHub owner ID."
}

variable "environment" {
  type        = string
  description = "Exact planning or execution environment."
  validation {
    condition     = can(regex("^[A-Za-z0-9_-]{1,80}$", var.environment))
    error_message = "Use a short environment name with letters, digits, underscores, or hyphens."
  }
}

variable "workflow_ref" {
  type        = string
  description = "Reusable workflow path pinned to a full commit SHA."
  validation {
    condition     = can(regex("@[0-9a-f]{40}$", var.workflow_ref))
    error_message = "Pin the trusted workflow to an immutable commit."
  }
}

variable "ref" {
  type        = string
  default     = "refs/heads/main"
  description = "Permitted source branch."
}

variable "service_account_name" {
  type        = string
  description = "Existing scoped service account in projects/PROJECT/serviceAccounts/EMAIL form."
}

resource "google_iam_workload_identity_pool" "target" {
  project                   = var.project_id
  workload_identity_pool_id = var.pool_id
}

# A short subject avoids Google's subject-length limit for customized GitHub sub claims
resource "google_iam_workload_identity_pool_provider" "github" {
  project                            = var.project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.target.workload_identity_pool_id
  workload_identity_pool_provider_id = "github"
  attribute_mapping = {
    "google.subject"                = "assertion.repository_id + '-' + assertion.environment"
    "attribute.repository_id"       = "assertion.repository_id"
    "attribute.repository_owner_id" = "assertion.repository_owner_id"
  }
  attribute_condition = join(" && ", [
    "assertion.repository_id == '${var.repository_id}'",
    "assertion.repository_owner_id == '${var.owner_id}'",
    "assertion.environment == '${var.environment}'",
    "assertion.ref == '${var.ref}'",
    "assertion.job_workflow_ref == '${var.workflow_ref}'"
  ])
  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

# This permits impersonation only. Workload permissions remain on the existing service account
resource "google_service_account_iam_member" "workflow" {
  service_account_id = var.service_account_name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principal://${google_iam_workload_identity_pool.target.name}/subject/${var.repository_id}-${var.environment}"
}

output "workload_identity_provider" {
  value = google_iam_workload_identity_pool_provider.github.name
}
