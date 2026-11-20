# Workers on Fargate (ADR 0005, ADR 0007): 0 tasks while idle, scaled on the run queue
# (scaling.tf). ARM64 image from ECR (make worker-image; worker/AGENTS.md has the push).

resource "aws_ecs_cluster" "main" {
  name = local.name

  setting {
    name  = "containerInsights"
    value = "disabled" # costs per metric; the queue alarms are enough here
  }
}

resource "aws_ecs_task_definition" "worker" {
  family                   = "${local.name}-worker"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 512
  memory                   = 1024
  execution_role_arn       = aws_iam_role.worker_execution.arn
  task_role_arn            = aws_iam_role.worker_task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  container_definitions = jsonencode([{
    name      = "worker"
    image     = "${aws_ecr_repository.worker.repository_url}:${var.worker_image_tag}"
    essential = true
    environment = [for k, v in merge(var.worker_model_env, {
      AWS_REGION         = var.region
      QUEUE_BACKEND      = "sqs"
      RUN_QUEUE_URL      = aws_sqs_queue.runs.url
      STATE_BACKEND      = "dynamodb"
      STATE_TABLE        = aws_dynamodb_table.this["run-state"].name
      CONTROL_PLANE_URL  = local.api_url
      TOOLS_PROVIDER     = "mcp"
      TOOL_GATEWAY_URL   = local.api_url
      TOOL_GATEWAY_SIGV4 = "true"
      WORKER_CONCURRENCY = "4"
      TRACING_EXPORTER   = "xray"                  # spans to the X-Ray OTLP endpoint (ADR 0022)
      METRICS_NAMESPACE  = local.metrics_namespace # model spend metric (alarms.tf, ADR 0023)
    }, local.worker_load_env) : { name = k, value = v }]
    # ECS reads these from SSM when the task starts; the container sees plain env vars.
    secrets = [{
      name      = "MODEL_MINIMAX_API_KEY"
      valueFrom = aws_ssm_parameter.secret["worker/model/minimax/api-key"].arn
    }]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.worker.name
        awslogs-region        = var.region
        awslogs-stream-prefix = "worker"
      }
    }
  }])
}

resource "aws_ecs_service" "worker" {
  name            = "${local.name}-worker"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.worker.arn
  launch_type     = "FARGATE"
  desired_count   = 0

  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.worker.id]
    assign_public_ip = true # no NAT: the public IP is the way out
  }

  lifecycle {
    ignore_changes = [desired_count] # the scaling policies own it
  }
}
