# Workers follow the run queue: more tasks while messages wait, sized to the backlog (1-9
# waiting: +1, 10-49: +5, 50 or more: +10; up to worker_max_tasks, more in load-test mode), back
# to 0 after 5 minutes with nothing waiting or in flight. One run still adds exactly one task. A run in progress keeps its message in
# flight (the worker extends its visibility), so scale-in never cuts a run short. SQS metrics are
# per minute; a queue with no call for six hours stops reporting and takes up to 15 minutes to
# report again, so a schedule calls it every hour (scheduler.tf, ADR 0025).

resource "aws_appautoscaling_target" "worker" {
  service_namespace  = "ecs"
  resource_id        = "service/${aws_ecs_cluster.main.name}/${aws_ecs_service.worker.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  min_capacity       = 0
  max_capacity       = local.worker_max_tasks
}

resource "aws_appautoscaling_policy" "worker_out" {
  name               = "${local.name}-worker-out"
  policy_type        = "StepScaling"
  service_namespace  = aws_appautoscaling_target.worker.service_namespace
  resource_id        = aws_appautoscaling_target.worker.resource_id
  scalable_dimension = aws_appautoscaling_target.worker.scalable_dimension

  step_scaling_policy_configuration {
    adjustment_type         = "ChangeInCapacity"
    cooldown                = 120
    metric_aggregation_type = "Maximum"

    # Bounds are relative to the alarm threshold (1 waiting): +0 is 1 waiting, +9 is 10, +49 is 50.
    step_adjustment {
      metric_interval_lower_bound = 0
      metric_interval_upper_bound = 9
      scaling_adjustment          = 1
    }
    step_adjustment {
      metric_interval_lower_bound = 9
      metric_interval_upper_bound = 49
      scaling_adjustment          = 5
    }
    step_adjustment {
      metric_interval_lower_bound = 49
      scaling_adjustment          = 10
    }
  }
}

resource "aws_appautoscaling_policy" "worker_in" {
  name               = "${local.name}-worker-in"
  policy_type        = "StepScaling"
  service_namespace  = aws_appautoscaling_target.worker.service_namespace
  resource_id        = aws_appautoscaling_target.worker.resource_id
  scalable_dimension = aws_appautoscaling_target.worker.scalable_dimension

  step_scaling_policy_configuration {
    adjustment_type         = "ExactCapacity"
    cooldown                = 60
    metric_aggregation_type = "Maximum"

    step_adjustment {
      metric_interval_upper_bound = 0
      scaling_adjustment          = 0
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "runs_waiting" {
  alarm_name          = "${local.name}-runs-waiting"
  alarm_description   = "Runs wait in the queue: add a worker task"
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  dimensions          = { QueueName = aws_sqs_queue.runs.name }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_appautoscaling_policy.worker_out.arn]
}

resource "aws_cloudwatch_metric_alarm" "runs_idle" {
  alarm_name          = "${local.name}-runs-idle"
  alarm_description   = "No run waiting or in progress for 5 minutes: scale workers to 0"
  evaluation_periods  = 5
  datapoints_to_alarm = 5
  threshold           = 0
  comparison_operator = "LessThanOrEqualToThreshold"
  treat_missing_data  = "breaching" # an idle queue may stop reporting; nothing to do then
  alarm_actions       = [aws_appautoscaling_policy.worker_in.arn]

  metric_query {
    id          = "total"
    expression  = "waiting + inflight"
    label       = "Runs waiting or in progress"
    return_data = true
  }

  metric_query {
    id = "waiting"
    metric {
      namespace   = "AWS/SQS"
      metric_name = "ApproximateNumberOfMessagesVisible"
      dimensions  = { QueueName = aws_sqs_queue.runs.name }
      stat        = "Maximum"
      period      = 60
    }
  }

  metric_query {
    id = "inflight"
    metric {
      namespace   = "AWS/SQS"
      metric_name = "ApproximateNumberOfMessagesNotVisible"
      dimensions  = { QueueName = aws_sqs_queue.runs.name }
      stat        = "Maximum"
      period      = 60
    }
  }
}
