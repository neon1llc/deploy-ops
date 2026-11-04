# examples/gcp-receipts/infrastructure/main.tf - Create the service foundation for separate Cloud Run releases.
# Supply existing access, APIs, state bucket, runtime identity and registry image before planning.


# Pin one provider source and version for both protected native engine jobs
terraform {
  required_version = ">= 1.10.0, < 1.13.0"
  required_providers {
    google = {
      source  = "registry.opentofu.org/hashicorp/google"
      version = "6.50.0"
    }
  }
  backend "gcs" {
    bucket = "example-state-bucket"
    prefix = "development/cloud-run"
  }
}

# Let the protected runner supply its short-lived identity through native environment variables
provider "google" {
  project = var.project
  region  = var.region
}

# Keep public destination values explicit and consistent with the controller registration
variable "project" {
  type        = string
  description = "Existing GCP project matching the registered execution identity."
}

variable "region" {
  type        = string
  description = "Cloud Run and Artifact Registry region matching the receipt contract."
  default     = "us-central1"
}

variable "name" {
  type        = string
  description = "Cloud Run service name within the bounded qualification scope."
  default     = "web"
}

# Reuse existing identities and image storage without granting authority in this plan
variable "runtime_service_account" {
  type        = string
  description = "Existing service account authorized to run the application."
}

variable "image_repository" {
  type        = string
  description = "Existing regional Artifact Registry image path without a tag or digest."
}

variable "initial_image_digest" {
  type        = string
  description = "Immutable sha256 digest already available in the declared image repository."
  validation {
    condition     = can(regex("^sha256:[0-9a-f]{64}$", var.initial_image_digest))
    error_message = "Supply an immutable sha256 image digest."
  }
}

# Bound idle capacity while leaving access policy to the preconfigured operator boundary
resource "google_cloud_run_v2_service" "web" {
  name                = var.name
  location            = var.region
  deletion_protection = true
  ingress             = "INGRESS_TRAFFIC_INTERNAL_ONLY"

  template {
    service_account = var.runtime_service_account
    scaling {
      min_instance_count = 0
      max_instance_count = 1
    }
    containers {
      image = "${var.image_repository}@${var.initial_image_digest}"
      resources {
        limits = {
          cpu    = "1"
          memory = "256Mi"
        }
      }
    }
  }

  # The application worker owns subsequent image revisions and traffic changes
  lifecycle {
    ignore_changes = [template[0].containers[0].image, traffic, client, client_version]
  }
}

# Export only the typed public destination values consumed by the application receipt
output "project" {
  value       = google_cloud_run_v2_service.web.project
  description = "Observed service project."
}

output "region" {
  value       = google_cloud_run_v2_service.web.location
  description = "Observed service region."
}

output "name" {
  value       = google_cloud_run_v2_service.web.name
  description = "Observed service name."
}

output "image_repository" {
  value       = var.image_repository
  description = "Operator-selected image repository for subsequent app releases."
}
