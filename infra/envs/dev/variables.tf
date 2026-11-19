variable "aws_profile" {
  description = "AWS CLI profile (credentials stay in ~/.aws). The backend block names it too."
  type        = string
  default     = "default"

  validation {
    condition     = var.aws_profile != "prod"
    error_message = "This project never deploys with the prod profile."
  }
}

variable "region" {
  description = "AWS region for every resource."
  type        = string
  default     = "us-east-1"
}

variable "project" {
  description = "Value of the project tag on every resource; the project budget filters on it."
  type        = string
  default     = "agent-platform"
}
