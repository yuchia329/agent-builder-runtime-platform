terraform {
  # 1.10 or newer: the S3 backend's own lock file (use_lockfile) in envs/.
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # Bootstrap keeps its own small state locally (git-ignored): it creates the bucket that holds
  # every other state, so it cannot live in it.
}

provider "aws" {
  profile = var.aws_profile
  region  = var.region

  default_tags {
    tags = {
      project = var.project
    }
  }
}
