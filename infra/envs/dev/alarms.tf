# Alerts that need a person: one SNS topic emailing alert_email (the subscription is confirmed once
# from the email SNS sends). Budgets in bootstrap/ cover AWS's own bill; model providers bill
# outside AWS (MiniMax), so their spend has its own alarm on a metric the worker writes.

resource "aws_sns_topic" "alerts" {
  name = "${local.name}-alerts"
}

resource "aws_sns_topic_subscription" "alerts_email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# The worker writes the cost of every model call (US dollars, no dimensions) to
# <metrics_namespace>/ModelCostUsd as a CloudWatch embedded metric log line (ADR 0023). The alarm
# sums it over the last 24 hours (a one-day period is a window ending at each evaluation, not a
# calendar day: a test value keeps it in ALARM for 24 hours). No data (idle days) is OK.
resource "aws_cloudwatch_metric_alarm" "model_spend" {
  alarm_name          = "${local.name}-model-spend"
  alarm_description   = "Model spend in the last 24 hours is above ${var.model_spend_alarm_usd} USD (sum of ModelCostUsd)"
  namespace           = local.metrics_namespace
  metric_name         = "ModelCostUsd"
  statistic           = "Sum"
  period              = 86400
  evaluation_periods  = 1
  threshold           = var.model_spend_alarm_usd
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]
}
