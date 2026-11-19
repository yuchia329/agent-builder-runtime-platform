# The run queue: the control plane sends run ids (contracts/run-message.schema.json), workers
# receive them. Same settings as local-stack/elasticmq.conf. The worker sets the visibility timeout
# on every receive (RUN_VISIBILITY_SECONDS) and extends it while working; 60 s is the fallback.

resource "aws_sqs_queue" "runs_dlq" {
  name                      = "${local.name}-runs-dlq"
  message_retention_seconds = 1209600 # 14 days, the maximum: time to look at a failed run
  sqs_managed_sse_enabled   = true
}

resource "aws_sqs_queue" "runs" {
  name                       = "${local.name}-runs"
  visibility_timeout_seconds = 60
  message_retention_seconds  = 345600 # 4 days; a lost message is re-sent by the sweep anyway
  sqs_managed_sse_enabled    = true

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.runs_dlq.arn
    maxReceiveCount     = 5
  })
}

resource "aws_sqs_queue_redrive_allow_policy" "runs_dlq" {
  queue_url = aws_sqs_queue.runs_dlq.id

  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [aws_sqs_queue.runs.arn]
  })
}
