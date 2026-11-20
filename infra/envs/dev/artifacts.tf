# Lambda packages. Built outside Terraform (infra/AGENTS.md, Deploy), uploaded here when their hash
# changes. Through S3 because the control plane's zip is close to Lambda's 50 MB direct upload.

locals {
  control_plane_zip = "${local.repo}/control-plane/build/distributions/control-plane-lambda.zip"
  gateway_zip       = "${local.repo}/tool-gateway/build/gateway-lambda.zip"
}

resource "random_id" "artifacts" {
  byte_length = 3
}

resource "aws_s3_bucket" "artifacts" {
  bucket        = "${local.name}-artifacts-${random_id.artifacts.hex}"
  force_destroy = true # only build outputs; the experiment is deleted as a whole
}

resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket                  = aws_s3_bucket.artifacts.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  rule {
    id     = "old-packages"
    status = "Enabled"
    filter {}
    expiration {
      days = 30
    }
  }
}

resource "aws_s3_object" "control_plane" {
  bucket = aws_s3_bucket.artifacts.id
  key    = "control-plane/${filemd5(local.control_plane_zip)}.zip"
  source = local.control_plane_zip
}

resource "aws_s3_object" "gateway" {
  bucket = aws_s3_bucket.artifacts.id
  key    = "tool-gateway/${filemd5(local.gateway_zip)}.zip"
  source = local.gateway_zip
}
