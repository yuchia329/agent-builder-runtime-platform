# Log groups made here (not by the services on first write) so they carry tags and a retention.

resource "aws_cloudwatch_log_group" "control_plane" {
  name              = "/aws/lambda/${local.name}-control-plane"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "gateway" {
  name              = "/aws/lambda/${local.name}-tool-gateway"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "worker" {
  name              = "/ecs/${local.name}-worker"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "api" {
  name              = "/aws/apigateway/${local.name}"
  retention_in_days = var.log_retention_days
}
