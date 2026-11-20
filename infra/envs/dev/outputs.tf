# Names the compute task and the services' configuration need (docs/architecture.md, Configuration).

output "tables" {
  description = "DynamoDB table names: CONTROL_PLANE_TABLE (control-plane), STATE_TABLE (run-state)."
  value       = { for k, t in aws_dynamodb_table.this : k => t.name }
}

output "run_queue_url" {
  description = "RUN_QUEUE_URL for the control plane and the worker."
  value       = aws_sqs_queue.runs.url
}

output "run_dlq_url" {
  description = "Dead-letter queue: runs whose message was received 5 times."
  value       = aws_sqs_queue.runs_dlq.url
}

output "worker_repository_url" {
  description = "ECR repository for the worker image (docker push <url>:<tag>)."
  value       = aws_ecr_repository.worker.repository_url
}

output "run_token_key_alias" {
  description = "KMS alias of the run-token signing key."
  value       = aws_kms_alias.run_token.name
}

output "api_url" {
  description = "The platform's address: agents API, webhooks (/triggers/github), Slack (/approvals/slack), owner login (/oauth/github/...)."
  value       = aws_apigatewayv2_api.main.api_endpoint
}

output "worker_service" {
  description = "ECS cluster and service of the workers (desired count is 0 while idle)."
  value       = { cluster = aws_ecs_cluster.main.name, service = aws_ecs_service.worker.name }
}

output "functions" {
  description = "Lambda function names (control plane is called through its alias live)."
  value = {
    control_plane = aws_lambda_function.control_plane.function_name
    gateway       = aws_lambda_function.gateway.function_name
  }
}

output "secret_parameters" {
  description = "SSM parameter name for each secret env var (used by infra/scripts/put_secrets.py)."
  value       = { for k, p in aws_ssm_parameter.secret : local.secrets[k] => p.name }
}

output "load_test" {
  description = "Whether load-test mode is on (load_test.tf); set load_test = false after a test."
  value       = local.load_test
}

output "worker_concurrency" {
  description = "Runs each worker task executes at once now (more in load-test mode)."
  value       = local.worker_concurrency
}

output "gateway_connectors" {
  description = "Connectors the gateway runs now (fake in load-test mode); aws_demo.py needs fake."
  value       = local.gateway_connectors
}

output "model_spend_alarm" {
  description = "Alarm on the day's model spend, and the metric the worker writes (namespace, name)."
  value = {
    alarm     = aws_cloudwatch_metric_alarm.model_spend.alarm_name
    namespace = local.metrics_namespace
    metric    = aws_cloudwatch_metric_alarm.model_spend.metric_name
  }
}
