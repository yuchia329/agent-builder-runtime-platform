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

# Transaction Search (X-Ray spans in CloudWatch Logs, ADR 0022) made this group when it was turned
# on; Terraform takes it over for its retention (it was 30 days). skip_destroy: destroying dev
# leaves it for the account's Transaction Search.
import {
  to = aws_cloudwatch_log_group.spans
  id = "aws/spans"
}

resource "aws_cloudwatch_log_group" "spans" {
  name              = "aws/spans"
  retention_in_days = var.log_retention_days
  skip_destroy      = true
}

# Application Signals made this group with Transaction Search, with no retention (logs kept
# forever). Taken over like aws/spans: 14 days, kept on destroy.
import {
  to = aws_cloudwatch_log_group.application_signals
  id = "/aws/application-signals/data"
}

resource "aws_cloudwatch_log_group" "application_signals" {
  name              = "/aws/application-signals/data"
  retention_in_days = var.log_retention_days
  skip_destroy      = true
}
