terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  # The bucket name comes from backend.hcl (git-ignored; written from bootstrap's output):
  # terraform init -backend-config=backend.hcl
  backend "s3" {
    key          = "envs/dev/terraform.tfstate"
    region       = "us-east-1"
    profile      = "default"
    encrypt      = true
    use_lockfile = true
  }
}
