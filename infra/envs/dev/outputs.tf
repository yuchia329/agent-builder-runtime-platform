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

output "secret_parameters" {
  description = "SSM parameter name for each secret env var (used by infra/scripts/put_secrets.py)."
  value       = { for k, p in aws_ssm_parameter.secret : local.secrets[k] => p.name }
}
