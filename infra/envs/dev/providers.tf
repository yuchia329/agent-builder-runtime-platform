provider "aws" {
  profile = var.aws_profile
  region  = var.region

  default_tags {
    tags = {
      project     = var.project
      environment = "dev"
    }
  }
}
