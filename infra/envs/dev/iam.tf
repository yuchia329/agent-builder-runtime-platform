# One role per service, each allowed only what that service calls (docs/architecture.md,
# Configuration: "Used by" is the role). SSM SecureString values use the AWS managed key aws/ssm,
# which needs no KMS statement (ADR 0018).

data "aws_caller_identity" "current" {}

locals {
  account    = data.aws_caller_identity.current.account_id
  ssm_arn    = "arn:aws:ssm:${var.region}:${local.account}:parameter/${var.project}/${local.environment}"
  table_arns = { for k, t in aws_dynamodb_table.this : k => t.arn }
  # execute-api resource: <api>/<stage>/<method>/<path>
  internal_route = "${aws_apigatewayv2_api.main.execution_arn}/*/*/internal/*"
  mcp_route      = "${aws_apigatewayv2_api.main.execution_arn}/*/*/mcp"
  table_item_actions = [
    "dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem",
    "dynamodb:Query", "dynamodb:ConditionCheckItem",
  ]
}

data "aws_iam_policy_document" "lambda_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "ecs_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account]
    }
  }
}

# --- Control plane (Lambda) ---------------------------------------------------------------

resource "aws_iam_role" "control_plane" {
  name               = "${local.name}-control-plane"
  assume_role_policy = data.aws_iam_policy_document.lambda_trust.json
}

data "aws_iam_policy_document" "control_plane" {
  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.control_plane.arn}:*"]
  }
  statement {
    sid       = "Table"
    actions   = concat(local.table_item_actions, ["dynamodb:TransactWriteItems"])
    resources = [local.table_arns["control-plane"], "${local.table_arns["control-plane"]}/index/*"]
  }
  statement {
    sid       = "Queue"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.runs.arn]
  }
  statement {
    sid       = "RunTokens"
    actions   = ["kms:Sign", "kms:GetPublicKey"]
    resources = [aws_kms_key.run_token.arn]
  }
  statement {
    sid       = "Secrets"
    actions   = ["ssm:GetParameters"]
    resources = ["${local.ssm_arn}/control-plane/*"]
  }
}

resource "aws_iam_role_policy" "control_plane" {
  role   = aws_iam_role.control_plane.id
  policy = data.aws_iam_policy_document.control_plane.json
}

# --- Tool gateway (Lambda) ----------------------------------------------------------------

resource "aws_iam_role" "gateway" {
  name               = "${local.name}-tool-gateway"
  assume_role_policy = data.aws_iam_policy_document.lambda_trust.json
}

data "aws_iam_policy_document" "gateway" {
  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.gateway.arn}:*"]
  }
  statement {
    sid       = "TokenStore"
    actions   = ["ssm:GetParameter", "ssm:PutParameter", "ssm:DeleteParameter"]
    resources = ["${local.ssm_arn}/gateway/tokens/*"]
  }
  statement {
    sid     = "Secrets"
    actions = ["ssm:GetParameters"]
    resources = [
      aws_ssm_parameter.secret["gateway/github-oauth-client-secret"].arn,
      aws_ssm_parameter.secret["gateway/oauth-state-secret"].arn,
    ]
  }
  statement {
    sid       = "RunTokenKey"
    actions   = ["kms:GetPublicKey"]
    resources = [aws_kms_key.run_token.arn]
  }
  statement {
    sid       = "ApprovalCheck"
    actions   = ["execute-api:Invoke"]
    resources = [local.internal_route]
  }
}

resource "aws_iam_role_policy" "gateway" {
  role   = aws_iam_role.gateway.id
  policy = data.aws_iam_policy_document.gateway.json
}

# --- Worker (ECS): execution role starts the task, task role is what the code may do ------

resource "aws_iam_role" "worker_execution" {
  name               = "${local.name}-worker-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_trust.json
}

data "aws_iam_policy_document" "worker_execution" {
  statement {
    sid       = "PullImage"
    actions   = ["ecr:GetDownloadUrlForLayer", "ecr:BatchGetImage"]
    resources = [aws_ecr_repository.worker.arn]
  }
  statement {
    sid       = "EcrLogin"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"] # account-wide by design; it grants no repository
  }
  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.worker.arn}:*"]
  }
  statement {
    sid       = "Secrets"
    actions   = ["ssm:GetParameters"]
    resources = ["${local.ssm_arn}/worker/*"]
  }
}

resource "aws_iam_role_policy" "worker_execution" {
  role   = aws_iam_role.worker_execution.id
  policy = data.aws_iam_policy_document.worker_execution.json
}

resource "aws_iam_role" "worker_task" {
  name               = "${local.name}-worker-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_trust.json
}

data "aws_iam_policy_document" "worker_task" {
  statement {
    sid = "Queue"
    actions = [
      "sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:ChangeMessageVisibility",
      "sqs:GetQueueAttributes",
    ]
    resources = [aws_sqs_queue.runs.arn]
  }
  statement {
    sid       = "RunState"
    actions   = concat(local.table_item_actions, ["dynamodb:TransactWriteItems"])
    resources = [local.table_arns["run-state"]]
  }
  statement {
    sid       = "PlatformApi"
    actions   = ["execute-api:Invoke"]
    resources = [local.internal_route, local.mcp_route]
  }
}

resource "aws_iam_role_policy" "worker_task" {
  role   = aws_iam_role.worker_task.id
  policy = data.aws_iam_policy_document.worker_task.json
}

# --- Scheduler (sweeps) -------------------------------------------------------------------

resource "aws_iam_role" "scheduler" {
  name = "${local.name}-scheduler"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "scheduler.amazonaws.com" }
      Condition = { StringEquals = { "aws:SourceAccount" = local.account } }
    }]
  })
}

resource "aws_iam_role_policy" "scheduler" {
  role = aws_iam_role.scheduler.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "lambda:InvokeFunction"
      Resource = aws_lambda_alias.control_plane.arn
    }]
  })
}
