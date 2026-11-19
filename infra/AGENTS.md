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
  (`use_lockfile`, no lock table); bucket name from `backend.hcl` (git-ignored).

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
| Activate the cost tag (once, after a tagged resource exists, up to 24 h) | `aws ce update-cost-allocation-tags-status --cost-allocation-tags-status TagKey=project,Status=Active --profile default` |

## Conventions

- Profile `default`, region `us-east-1`. Never the `prod` profile (a variable validation refuses
  it). Credentials stay in `~/.aws`: never in `.env`, `*.tf` or `*.tfvars`.
- Every provider sets `default_tags { project = "agent-platform" }`; the project budget counts
  only tagged resources, from the day the tag is activated as a cost allocation tag.
- Personal or account values (alert email, account id, domain name, bucket suffix) go in
  git-ignored `*.tfvars` or `backend.hcl`, with a committed `.example` beside them. The repo is
  public.
- Secret values (API keys, GitHub App key) are never in Terraform files or state: Terraform makes
  the SSM parameter, the user puts the value (`aws ssm put-parameter --overwrite`), and Terraform
  ignores value changes.
- Commit `.terraform.lock.hcl`; never commit `.terraform/`, `*.tfstate*` or `*.tfplan`.
- Nothing that bills by the hour (ADR 0007): no NAT gateway, load balancer, interface endpoints,
  RDS or always-on instance. Prefer free options (SSE-S3 over KMS for state, SSM over Secrets
  Manager).
