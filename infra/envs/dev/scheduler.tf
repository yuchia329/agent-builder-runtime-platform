# The control plane's sweeps (re-enqueue stuck runs, post unsent approvals, expire old ones): a
# frozen Lambda keeps no timer, so EventBridge Scheduler invokes the `live` alias every 5 minutes
# with {"sweep": "all"} (ADR 0017). Within the free tier: $0 while idle, but never fully silent.

resource "aws_scheduler_schedule" "sweeps" {
  name                = "${local.name}-sweeps"
  schedule_expression = "rate(5 minutes)"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_alias.control_plane.arn
    role_arn = aws_iam_role.scheduler.arn
    input    = jsonencode({ sweep = "all" })

    retry_policy {
      maximum_retry_attempts       = 1 # the next run is 5 minutes away anyway
      maximum_event_age_in_seconds = 300
    }
  }
}
