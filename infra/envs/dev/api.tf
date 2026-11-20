# One HTTP API in front of both functions (ADR 0007), on its default execute-api address: no
# custom domain for this experiment (the address stays the same across deploys; it changes only
# if the API itself is replaced). Public routes check API keys or signatures in the app;
# /internal/* and /mcp need IAM (SigV4), and the gateway still checks the run token (ADR 0020).

locals {
  routes = {
    "ANY /agents"            = { target = "control_plane", iam = false }
    "ANY /agents/{proxy+}"   = { target = "control_plane", iam = false }
    "ANY /runs"              = { target = "control_plane", iam = false }
    "ANY /runs/{proxy+}"     = { target = "control_plane", iam = false }
    "POST /triggers/github"  = { target = "control_plane", iam = false }
    "POST /approvals/slack"  = { target = "control_plane", iam = false }
    "ANY /internal/{proxy+}" = { target = "control_plane", iam = true }
    "ANY /mcp"               = { target = "gateway", iam = true }
    "GET /oauth/{proxy+}"    = { target = "gateway", iam = false }
  }
}

resource "aws_apigatewayv2_api" "main" {
  name          = local.name
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.main.id
  name        = "$default"
  auto_deploy = true

  # Each route gets its own token bucket (HTTP APIs apply the default to every route separately).
  # Above it API Gateway answers 429 before any Lambda runs. Kept under the account's Lambda
  # concurrency (10 in dev), so a burst gets a clean 429 rather than Lambda throttling errors.
  # Per API, not per caller: a flood also delays Slack clicks and GitHub webhooks (GitHub does not
  # retry a 429; redeliver from the App's settings).
  default_route_settings {
    throttling_rate_limit  = var.api_throttle_rate
    throttling_burst_limit = var.api_throttle_burst
  }

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api.arn
    # No headers, query strings or bodies: tokens, signatures and login codes never reach the log.
    format = jsonencode({
      requestId   = "$context.requestId"
      time        = "$context.requestTime"
      route       = "$context.routeKey"
      status      = "$context.status"
      latencyMs   = "$context.responseLatency"
      integration = "$context.integrationStatus"
      error       = "$context.error.message"
      authError   = "$context.authorizer.error"
      caller      = "$context.identity.userArn"
    })
  }
}

resource "aws_apigatewayv2_integration" "control_plane" {
  api_id                 = aws_apigatewayv2_api.main.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_alias.control_plane.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_integration" "gateway" {
  api_id                 = aws_apigatewayv2_api.main.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.gateway.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "this" {
  for_each           = local.routes
  api_id             = aws_apigatewayv2_api.main.id
  route_key          = each.key
  authorization_type = each.value.iam ? "AWS_IAM" : "NONE"
  target = "integrations/${each.value.target == "gateway"
    ? aws_apigatewayv2_integration.gateway.id
  : aws_apigatewayv2_integration.control_plane.id}"
}

resource "aws_lambda_permission" "api_control_plane" {
  statement_id  = "api-gateway"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.control_plane.function_name
  qualifier     = aws_lambda_alias.control_plane.name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.main.execution_arn}/*/*"
}

resource "aws_lambda_permission" "api_gateway" {
  statement_id  = "api-gateway"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.gateway.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.main.execution_arn}/*/*"
}
