# 0007. Serverless, scale-to-zero deployment

- Status: Accepted
- Date: 2026-11-16

## Context

The platform is a demo that its owner and visitors try out now and then. Most of the time nobody
uses it, so anything billed per hour is wasted money. The first deployment plan billed by the hour
in several places: the control plane and the tool gateway as always-on Fargate services, an
Application Load Balancer, and RDS PostgreSQL. Together that is roughly $60–70 a month while
idle.

The control plane cannot simply be scaled to zero on Fargate: workers need it to start and finish
every run (ADR 0001), and GitHub and Slack expect webhook replies within seconds (Slack within 3).

Options considered:

- Fargate for everything, scaled to zero by a "wake" endpoint. Cold start is a minute or more, the
  ALB still bills by the hour, and webhooks arriving while it sleeps time out.
- An always-on EC2 instance hosting the control plane and gateway. Cheap per service, but the
  instance bills around the clock, which is the cost we want to remove.
- Lambda for the request/response services, Fargate scaled to zero for workers.

## Decision

- **Control plane** runs on AWS Lambda (Java 21 managed runtime with SnapStart). The same Spring
  Boot application runs as a normal server locally and behind a Lambda handler
  (`aws-serverless-java-container`) in AWS.
- **Tool gateway** runs on AWS Lambda (Python). The FastAPI app is served through a Lambda adapter;
  MCP uses the stateless Streamable HTTP transport with JSON responses, so no session survives
  between invocations.
- **One API Gateway HTTP API** fronts both functions under one custom domain (DNS on Cloudflare,
  TLS certificate from ACM). The default `execute-api` endpoint is disabled.
  - Public control-plane routes use the per-tenant API key, checked in the app. Webhook routes
    verify GitHub HMAC or the Slack signature in the app.
  - `/internal/*` (control plane) and `/mcp` (gateway) use IAM authorization. Workers and the
    gateway sign requests with SigV4 using their IAM roles. The gateway still verifies the run
    token (ADR 0003).
  - Gateway OAuth callback routes are public, since browsers reach them.
  - Route throttling limits load from visitors.
- **Workers** stay on ECS Fargate (ADR 0005). The service is scaled on SQS queue depth and runs 0
  tasks when idle. Tasks run in public subnets with a public IP and no NAT gateway.
- **Bedrock** stays on-demand, billed per token.
- No load balancer, NAT gateway, interface VPC endpoints, RDS Proxy or always-on instance. The
  Lambdas run outside a VPC.

## Consequences

- Idle cost is close to zero: storage, logs, secrets and a KMS key, a few dollars a month at most.
- The first request after idle pays a cold start (Lambda with SnapStart, about a second). The first
  run after idle also waits for the worker service to scale from 0, which can take minutes because
  SQS metrics reach CloudWatch about once a minute and the alarm needs a data point. If that is too
  slow for demos, add the Lambda worker entrypoint from ADR 0005.
- The control plane cannot use a connection-pooled relational database without paying for an
  always-on instance or a proxy. Its data moves to DynamoDB (ADR 0008).
- Spring Boot on Lambda needs SnapStart and care with startup work, such as no eager connections or
  heavy component scanning. If cold starts stay too slow, a lighter framework is an option.
- Lambda caps a request at 15 minutes and API Gateway HTTP API caps it at 30 seconds. Every
  control-plane and gateway endpoint must finish quickly. Long work belongs on the queue.
- Public visitors can spend Bedrock money. Per-run budgets, route throttling and an AWS Budgets
  alert are required before the URL is shared.
- `infra` creates Lambda functions, the HTTP API, routes and the custom domain instead of ECS
  services and an ALB for those two components.
