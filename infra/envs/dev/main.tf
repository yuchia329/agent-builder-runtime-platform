# The dev environment. Data: DynamoDB tables (dynamodb.tf), the run queue (sqs.tf), the worker
# image repository (ecr.tf), the run-token signing key (kms.tf), secret parameters (ssm.tf).
# Compute: Lambda packages (artifacts.tf), the two functions (lambda.tf), the HTTP API (api.tf),
# the workers' network (network.tf), Fargate (ecs.tf, scaling.tf), sweeps (scheduler.tf), roles
# (iam.tf), log groups (logs.tf), alerts (alarms.tf). Personal values in terraform.tfvars (see the .example).

locals {
  environment = "dev"
  # Prefix of every resource name: agent-platform-dev-...
  name = "${var.project}-${local.environment}"
  # Repository root, for definitions owned by the services (table JSON files).
  repo = "${path.module}/../../.."
  # CloudWatch namespace of the platform's own metrics (the worker's model spend, alarms.tf).
  metrics_namespace = "AgentPlatform/${local.environment}"
}
