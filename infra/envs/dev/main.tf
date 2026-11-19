# The dev environment. Data (M6 task 3): DynamoDB tables (dynamodb.tf), the run queue (sqs.tf), the
# worker image repository (ecr.tf), the run-token signing key (kms.tf) and secret parameters
# (ssm.tf). Compute (Lambdas, HTTP API, Fargate, IAM) comes next (docs/roadmap.md, M6 tasks).

locals {
  environment = "dev"
  # Prefix of every resource name: agent-platform-dev-...
  name = "${var.project}-${local.environment}"
  # Repository root, for definitions owned by the services (table JSON files).
  repo = "${path.module}/../../.."
}
