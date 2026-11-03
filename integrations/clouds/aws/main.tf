# integrations/clouds/aws/main.tf - Trust one observed workflow subject without granting admin access.

terraform {
  required_version = ">= 1.6.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0, < 7.0"
    }
  }
}

variable "oidc_provider_arn" {
  type        = string
  description = "Existing GitHub OIDC provider in this account."
}

variable "github_subject" {
  type        = string
  description = "Exact verified sub claim for this environment and pinned reusable workflow."
  validation {
    condition     = strcontains(var.github_subject, "job_workflow_ref:") && strcontains(var.github_subject, "environment:")
    error_message = "Use a customized subject containing the environment and pinned reusable workflow."
  }
}

variable "role_name" {
  type        = string
  description = "Create separate planner and executor roles."
}

variable "permissions_json" {
  type        = string
  description = "Reviewed least-privilege policy. No administrator default is supplied."
  validation {
    condition     = can(jsondecode(var.permissions_json))
    error_message = "Provide a valid IAM policy document."
  }
}

# Cloud-side trust must prevent application jobs from bypassing the controller
resource "aws_iam_role" "workflow" {
  name                 = var.role_name
  max_session_duration = 3600
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = var.oidc_provider_arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
          "token.actions.githubusercontent.com:sub" = var.github_subject
        }
      }
    }]
  })
}

resource "aws_iam_role_policy" "scope" {
  name   = "reviewed-deployment-scope"
  role   = aws_iam_role.workflow.id
  policy = var.permissions_json
}

output "role_arn" {
  value = aws_iam_role.workflow.arn
}
