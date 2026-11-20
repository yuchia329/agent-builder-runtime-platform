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

# Keeps the run queue "active" for CloudWatch (ADR 0025). SQS stops sending a queue's metrics after
# six hours without any call to it, and they come back up to 15 minutes after it wakes: the
# scale-out alarm (scaling.tf) would wait that long for the first run after a quiet day. Workers at
# 0 never call the queue, so once an hour Scheduler calls TagQueue with the tag the queue already
# has (no change, nothing for Terraform to see). Scheduler cannot call read-only actions (Get*,
# List*), so this is the lightest call it can make. Within the free tier: about 720 calls a month.
resource "aws_scheduler_schedule" "keep_queue_active" {
  name                = "${local.name}-keep-queue-active"
  schedule_expression = "rate(1 hour)"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = "arn:aws:scheduler:::aws-sdk:sqs:tagQueue"
    role_arn = aws_iam_role.scheduler.arn
    input = jsonencode({
      QueueUrl = aws_sqs_queue.runs.url
      Tags     = { project = var.project }
    })

    retry_policy {
      maximum_retry_attempts       = 2
      maximum_event_age_in_seconds = 600
    }
  }
}
