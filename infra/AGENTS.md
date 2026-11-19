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
  of resource (`dynamodb.tf`, `sqs.tf`, `ecr.tf`, `kms.tf`, `ssm.tf`), names
  `agent-platform-dev-<name>` (`local.name`), names other components need in `outputs.tf`.
- `scripts/put_secrets.py`: puts secret values into the SSM parameters (ADR 0018).

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
- Nothing that bills by the hour (ADR 0007): no NAT gateway, load balancer, interface endpoints,
  RDS or always-on instance. Prefer free options (SSE-S3 over KMS for state, SSM over Secrets
  Manager).
