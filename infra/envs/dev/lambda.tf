# The two Lambda functions (ADR 0007). Control plane: Java with SnapStart, called through the
# alias `live`, which follows each published version (ADR 0017). Gateway: Python under the Lambda
# Web Adapter layer (ADR 0019). Settings from each service's handoff (docs/architecture.md,
# Configuration); secrets come from SSM and KMS, never from here.

locals {
  api_url = aws_apigatewayv2_api.main.api_endpoint # https://<id>.execute-api.<region>.amazonaws.com
}

resource "aws_lambda_function" "control_plane" {
  function_name    = "${local.name}-control-plane"
  role             = aws_iam_role.control_plane.arn
  runtime          = "java21"
  architectures    = ["arm64"]
  handler          = "dev.agentplatform.controlplane.lambda.LambdaHandler::handleRequest"
  memory_size      = var.control_plane_memory_mb
  timeout          = 30
  s3_bucket        = aws_s3_object.control_plane.bucket
  s3_key           = aws_s3_object.control_plane.key
  source_code_hash = filebase64sha256(local.control_plane_zip)
  publish          = true # SnapStart works on published versions only

  snap_start {
    apply_on = "PublishedVersions"
  }

  environment {
    variables = {
      STORE_BACKEND          = "dynamodb"
      CONTROL_PLANE_TABLE    = aws_dynamodb_table.this["control-plane"].name
      QUEUE_BACKEND          = "sqs"
      RUN_QUEUE_URL          = aws_sqs_queue.runs.url
      TENANT_ID              = var.tenant_id
      SECRETS_SOURCE         = "ssm"
      SECRETS_SSM_PATH       = "/${var.project}/${local.environment}/control-plane"
      RUN_TOKEN_KMS_KEY_ID   = aws_kms_alias.run_token.name
      SLACK_APPROVAL_CHANNEL = var.slack_approval_channel
    }
  }

  depends_on = [aws_cloudwatch_log_group.control_plane, aws_iam_role_policy.control_plane]
}

resource "aws_lambda_alias" "control_plane" {
  name             = "live"
  function_name    = aws_lambda_function.control_plane.function_name
  function_version = aws_lambda_function.control_plane.version
}

resource "aws_lambda_function" "gateway" {
  function_name    = "${local.name}-tool-gateway"
  role             = aws_iam_role.gateway.arn
  runtime          = "python3.12"
  architectures    = ["arm64"]
  handler          = "run.sh"
  memory_size      = var.gateway_memory_mb
  timeout          = 30
  s3_bucket        = aws_s3_object.gateway.bucket
  s3_key           = aws_s3_object.gateway.key
  source_code_hash = filebase64sha256(local.gateway_zip)
  layers = [
    "arn:aws:lambda:${var.region}:753240598075:layer:LambdaAdapterLayerArm64:${var.web_adapter_layer_version}",
  ]

  environment {
    variables = merge(
      {
        AWS_LAMBDA_EXEC_WRAPPER      = "/opt/bootstrap"
        AWS_LWA_READINESS_CHECK_PATH = "/health"
        RUN_TOKEN_KMS_KEY_ID         = aws_kms_alias.run_token.name
        TOKEN_STORE                  = "ssm"
        TOKEN_STORE_SSM_PATH         = "/${var.project}/${local.environment}/gateway/tokens"
        SECRETS_SSM_PATH             = "/${var.project}/${local.environment}/gateway"
        CONTROL_PLANE_URL            = local.api_url
        CONTROL_PLANE_SIGV4          = "true"
        CONNECTORS                   = var.gateway_connectors
        GITHUB_OAUTH_CLIENT_ID       = var.github_oauth_client_id
        GATEWAY_PUBLIC_URL           = local.api_url
      },
      var.github_repos == "" ? {} : { GITHUB_REPOS = var.github_repos },
    )
  }

  depends_on = [aws_cloudwatch_log_group.gateway, aws_iam_role_policy.gateway]
}
