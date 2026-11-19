# Made once, before any environment: the bucket that holds every environment's Terraform state,
# and the two budgets. Budgets live here, not in an environment, so destroying an environment
# leaves the alerts on.

resource "random_id" "state_bucket" {
  byte_length = 3
}

resource "aws_s3_bucket" "state" {
  # The suffix keeps the name globally unique without putting the account id in it.
  bucket = "${var.project}-tfstate-${random_id.state_bucket.hex}"

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_ownership_controls" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket = aws_s3_bucket.state.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    # SSE-S3: free, unlike a customer-managed KMS key.
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    id     = "expire-old-state-versions"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days           = 90
      newer_noncurrent_versions = 10
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  depends_on = [aws_s3_bucket_versioning.state]
}

data "aws_iam_policy_document" "state" {
  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.state.arn,
      "${aws_s3_bucket.state.arn}/*",
    ]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "state" {
  bucket = aws_s3_bucket.state.id
  policy = data.aws_iam_policy_document.state.json

  depends_on = [aws_s3_bucket_public_access_block.state]
}

locals {
  # Email at 50% and 100% of actual spend, and when the month's forecast passes 100%.
  budget_alerts = [
    { type = "ACTUAL", threshold = 50 },
    { type = "ACTUAL", threshold = 100 },
    { type = "FORECASTED", threshold = 100 },
  ]
}

resource "aws_budgets_budget" "project" {
  name         = "${var.project}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.project_monthly_limit_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  # Counts only once the project tag is activated as a cost allocation tag (Billing console or
  # `aws ce update-cost-allocation-tags-status`), and only from that day on.
  cost_filter {
    name = "TagKeyValue"
    # Format "user:<tag key>$<tag value>"; "$${" would be an escape in HCL, hence format().
    values = [format("user:project$%s", var.project)]
  }

  dynamic "notification" {
    for_each = local.budget_alerts
    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value.threshold
      threshold_type             = "PERCENTAGE"
      notification_type          = notification.value.type
      subscriber_email_addresses = [var.alert_email]
    }
  }
}

resource "aws_budgets_budget" "account" {
  name         = "account-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.account_monthly_limit_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  dynamic "notification" {
    for_each = local.budget_alerts
    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value.threshold
      threshold_type             = "PERCENTAGE"
      notification_type          = notification.value.type
      subscriber_email_addresses = [var.alert_email]
    }
  }
}
