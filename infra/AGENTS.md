# infra

Terraform for every AWS resource (ADR 0007: scale to zero, no hourly cost while idle). Design:
`docs/architecture.md` → Deployment. Applied from the laptop; CI deploys come later.

## Layout

- `bootstrap/`: made once. The state bucket (versioned, SSE-S3, public access blocked, TLS only,
  old versions expire after 90 days) and the two budgets ($10 a month on the `project` tag, $90 a
  month for the whole account; email at 50% and 100% actual, 100% forecast). Its own state is a
  local `terraform.tfstate` (git-ignored): it creates the bucket the other states live in. Budgets
  live here so destroying an environment leaves the alerts on.
- `envs/dev/`: the one environment. S3 backend in the bootstrap bucket with Terraform's lock file
  (`use_lockfile`, no lock table); bucket name from `backend.hcl` (git-ignored). One file per kind
  of resource: data (`dynamodb.tf`, `sqs.tf`, `ecr.tf`, `kms.tf`, `ssm.tf`) and compute
  (`artifacts.tf` Lambda zips in S3, `lambda.tf`, `api.tf`, `network.tf`, `ecs.tf`, `scaling.tf`,
  `scheduler.tf`, `iam.tf`, `logs.tf`), alerts (`alarms.tf`: SNS email topic, model spend
  alarm; ADR 0023) and load-test mode (`load_test.tf`: one switch, ADR 0024). Names `agent-platform-dev-<name>` (`local.name`); names
  other components need in `outputs.tf`. Personal values in `terraform.tfvars` (git-ignored; copy
  `terraform.tfvars.example`).
- `scripts/put_secrets.py`: puts secret values into the SSM parameters (ADR 0018).
- `scripts/aws_demo.py`: `make demo` on AWS. Publishes the example spec (stub model) as its own
  agent `demo-stub` (trigger `manual.demo.run`, never `issue-triage`; needs the gateway's fake
  connector: load-test mode), runs it to `SUCCEEDED`, reports the wait for a worker, finds the run's trace in `aws/spans` (worker span
  names and counts, never attributes), then waits for 0 workers. A few cents.
- `scripts/throttle_check.py`: 80 requests to `GET /agents` without a key, 8 at a time; passes
  when some answer 429. A few free-tier Lambda invocations.

## Deploy order

From an empty account (bootstrap and dev init in the table below), from the repo root:

1. Build the code: `cd control-plane && ./gradlew lambdaZip`, `cd tool-gateway && uv run python
   scripts/lambda_zip.py`, `make worker-image` (tag = short commit). `plan` reads the zips.
2. Data first: `terraform -chdir=envs/dev apply -target=aws_ssm_parameter.secret
   -target=aws_ecr_repository.worker` (or the whole data layer).
3. Secrets: `put_secrets.py --generate TENANT_API_KEY`, then `put_secrets.py`. Push the image
   (commands in `worker/AGENTS.md`) and set `worker_image_tag` in `terraform.tfvars`.
4. `terraform -chdir=envs/dev apply`, then `aws_demo.py`.

New code later: rebuild the zip or image (new tag in `terraform.tfvars`), `plan`, `apply`. A
control-plane change publishes a new version and waits about 85 s for its snapshot.

## Commands

Run from `infra/`. Every `apply` is shown as a plan to the user first.

| What | Command |
| --- | --- |
| Check (no AWS calls) | `terraform fmt -recursive -check && terraform -chdir=bootstrap validate && terraform -chdir=envs/dev validate` |
| Format | `terraform fmt -recursive` |
| Bootstrap (once) | `cp bootstrap/terraform.tfvars.example bootstrap/terraform.tfvars` (set `alert_email`), then `terraform -chdir=bootstrap init && terraform -chdir=bootstrap apply` |
| Dev backend file | `terraform -chdir=bootstrap output -raw backend_hcl > envs/dev/backend.hcl` |
| Dev init | `terraform -chdir=envs/dev init -backend-config=backend.hcl` |
| Dev plan / apply | `terraform -chdir=envs/dev plan` / `terraform -chdir=envs/dev apply` |
| Secret status (version 1 = placeholder) | `uv run --with boto3 python scripts/put_secrets.py --status` |
| Put secrets from `../.env` | `uv run --with boto3 python scripts/put_secrets.py [NAME ...]` |
| New random secret (tenant API key) | `uv run --with boto3 python scripts/put_secrets.py --generate TENANT_API_KEY` |
| Read one back | `aws ssm get-parameter --with-decryption --name <name> --query Parameter.Value --output text --profile default` |
| API address | `terraform -chdir=envs/dev output -raw api_url` |
| End-to-end check on AWS (a few cents) | `uv run --with boto3 python scripts/aws_demo.py` (`--no-trace` skips the trace lookup, `--no-scale-in` the wait for 0 workers) |
| Load-test mode on / off | `load_test = true` (or `false`) in `envs/dev/terraform.tfvars`, then `plan` and `apply`; `terraform -chdir=envs/dev output load_test` shows the mode |
| Lambda concurrency quota | `aws service-quotas get-service-quota --service-code lambda --quota-code L-B99A9384 --profile default` (1,000 since 2026-11-20; was 10) |
| Route throttling (some 429s) | `uv run python scripts/throttle_check.py` (keep `--concurrency` under 10: the account's Lambda limit) |
| Spend alarm state | `aws cloudwatch describe-alarms --alarm-names agent-platform-dev-model-spend --query 'MetricAlarms[0].StateValue' --profile default` |
| Test the spend alarm (stays in ALARM 24 h) | `aws cloudwatch put-metric-data --namespace AgentPlatform/dev --metric-name ModelCostUsd --value 2 --profile default` |
| Transaction Search on? | `aws xray get-trace-segment-destination --profile default` (must say `CloudWatchLogs` / `ACTIVE`) |
| Workers running now | `aws ecs describe-services --cluster agent-platform-dev --services agent-platform-dev-worker --query 'services[0].[desiredCount,runningCount]' --profile default` |
| Queue keep-alive calls (ADR 0025) | `aws cloudwatch get-metric-statistics --namespace AWS/Scheduler --metric-name InvocationAttemptCount --dimensions Name=ScheduleGroup,Value=default --statistics Sum --period 3600 --start-time <iso> --end-time <iso> --profile default` (also `TargetErrorCount`, must be 0) |
| Is the run queue reporting? | `aws cloudwatch get-metric-statistics --namespace AWS/SQS --metric-name ApproximateNumberOfMessagesVisible --dimensions Name=QueueName,Value=agent-platform-dev-runs --statistics Maximum --period 60 --start-time <iso> --end-time <iso> --profile default` (a point every minute = active) |
| Activate the cost tag (once, after a tagged resource exists, up to 24 h) | `aws ce update-cost-allocation-tags-status --cost-allocation-tags-status TagKey=project,Status=Active --profile default` |

## Conventions

- Profile `default`, region `us-east-1`. Never the `prod` profile (a variable validation refuses
  it). Credentials stay in `~/.aws`: never in `.env`, `*.tf` or `*.tfvars`.
- Every provider sets `default_tags { project = "agent-platform" }`; the project budget counts
  only tagged resources, from the day the tag is activated as a cost allocation tag.
- Personal or account values (alert email, account id, domain name, bucket suffix) go in
  git-ignored `*.tfvars` or `backend.hcl`, with a committed `.example` beside them. The repo is
  public.
- Secret values are never in Terraform files, plans or state (ADR 0018). A secret parameter is an
  `aws_ssm_parameter` with `value_wo` from an `ephemeral "random_password"` and
  `value_wo_version = 1`; the user puts the value with `scripts/put_secrets.py`. Never use `value`
  with `ignore_changes` (refresh copies the decrypted value into state) and never change
  `value_wo_version` (Terraform writes a new placeholder over the real value). A new secret: one
  line in `local.secrets` in `ssm.tf` (path => env var name).
- Tables come from the services' JSON files (`jsondecode(file(...))`); change a table there, never
  in Terraform. GSI keys use `key_schema` blocks (`hash_key` in a GSI is deprecated in provider 6).
- Commit `.terraform.lock.hcl`; never commit `.terraform/`, `*.tfstate*` or `*.tfplan`.
- No custom domain (ADR 0021): every URL comes from `aws_apigatewayv2_api.main.api_endpoint`.
  Never replace that API (the GitHub App and Slack hold its address).
- Secrets are read once per Lambda instance. The 5-minute sweeps keep a control-plane instance
  warm, so after changing a control-plane secret, publish a new version (any change to the
  function, then `apply`) to be sure it is used.
- Access logs and other log formats never include headers, query strings or bodies (tokens,
  signatures, login codes).
- Right after the API is created, routes can answer 404 for up to a minute (stage auto-deploy).
- Resources an AWS feature made by itself (the `aws/spans` log group of Transaction Search) are
  taken over with an `import` block and `skip_destroy = true`, never re-created by Terraform.
- Throttling cannot be tested on IAM routes: unsigned requests get 403 and are not counted. Test
  on a public route with a steady stream (one burst at once got Lambda's 503s, no 429). Throttle
  limits stay under the account's Lambda concurrency (1,000 since 2026-11-20); raise both together.
- A CloudWatch alarm's 86400 s period is the last 24 hours, not a calendar day. Metric filter
  patterns cannot select JSON keys with dots (span attributes like `cost.usd`).
- Emails and other personal values in variables are `sensitive = true`, so plans show
  `(sensitive value)`.
- Load-test mode is one switch (`load_test`, ADR 0024): change load values in
  `load_test_settings`, never by editing the normal variables for a test. A `load_test_settings`
  block in tfvars replaces the whole default, so it lists all five values (throttle rate and
  burst, worker tasks, `worker_concurrency`, stub latency; example in `terraform.tfvars.example`).
  `terraform output worker_concurrency` shows the runs per worker in use. Turn it off right after
  a test and confirm `plan` shows no changes; `aws_demo.py` refuses while the gateway runs only
  `github` (dev live).
- `worker_model_env` in tfvars replaces the whole default map; per-mode worker settings go in
  `local.worker_load_env`, merged in `ecs.tf`.
- Check a new worker image before pushing it: tags are immutable, so a bad push costs a new tag
  (`docker run --rm --entrypoint /app/.venv/bin/python <image> -c "import worker..."`).
- EventBridge Scheduler's universal target (`arn:aws:scheduler:::aws-sdk:<service>:<action>`)
  refuses read-only actions (`get`, `list`, `describe`, `receive`, ... prefixes). The queue
  keep-alive therefore calls `TagQueue` with the tag the queue already has; never `SendMessage`
  (a message starts a run and a worker).
- Nothing that bills by the hour (ADR 0007): no NAT gateway, load balancer, interface endpoints,
  RDS or always-on instance. Prefer free options (SSE-S3 over KMS for state, SSM over Secrets
  Manager).
