variable "aws_profile" {
  description = "AWS CLI profile (credentials stay in ~/.aws)."
  type        = string
  default     = "default"

  validation {
    condition     = var.aws_profile != "prod"
    error_message = "This project never deploys with the prod profile."
  }
}

variable "region" {
  description = "AWS region for the state bucket."
  type        = string
  default     = "us-east-1"
}

variable "project" {
  description = "Value of the project tag on every resource; the project budget filters on it."
  type        = string
  default     = "agent-platform"
}

variable "alert_email" {
  description = "Address that receives budget alerts. Set in terraform.tfvars (git-ignored)."
  type        = string
  sensitive   = true
}

variable "project_monthly_limit_usd" {
  description = "Monthly budget for resources tagged project = var.project."
  type        = number
  default     = 10
}

variable "account_monthly_limit_usd" {
  description = "Monthly budget for the whole account (a safety net; other resources run in it)."
  type        = number
  default     = 90
}
