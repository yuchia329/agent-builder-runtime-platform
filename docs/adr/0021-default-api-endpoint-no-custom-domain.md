# 0021. The default execute-api endpoint, no custom domain

- Status: Proposed
- Date: 2026-11-19

## Context

ADR 0007 put the HTTP API behind a custom domain (DNS on Cloudflare, an ACM certificate) and
disabled the default `execute-api` endpoint. Building the compute layer (M6) raised the question
of whether the domain is worth it for this deployment:

- The AWS deployment is an experiment: one tenant, one user, deleted when the project ends.
- The default address (`https://<api-id>.execute-api.<region>.amazonaws.com`) does not change
  when the Lambdas are redeployed or Terraform applies changes. It changes only when the API
  resource itself is replaced (destroyed and created again).
- A custom domain needs a certificate validated with a DNS record, a CNAME, an API mapping and
  more Terraform, plus records the user adds in Cloudflare by hand, and it puts the domain name
  into local configuration of a public repository.

Options:

- **Custom domain** (ADR 0007): a stable, readable URL that survives replacing the API; more
  setup, and the domain is tied to the experiment.
- **Default endpoint**: nothing to set up; the URL is stable for as long as the API exists.

## Decision

Use the API's default `execute-api` endpoint and keep it enabled. Terraform outputs it
(`api_url`) and passes it to the services that need it (`CONTROL_PLANE_URL`, `TOOL_GATEWAY_URL`,
`GATEWAY_PUBLIC_URL`). The URL stays out of the repository: it is read from `terraform output`.
This supersedes the custom-domain part of ADR 0007; the rest of ADR 0007 stands.

## Consequences

- No DNS or certificate steps; nothing to undo in Cloudflare when the experiment is deleted.
- The URLs registered outside AWS (GitHub App webhook, Slack Request URL, GitHub OAuth callback)
  point at the `execute-api` address. If the API is ever replaced, they must be updated by hand.
- The address shows it is AWS. Route throttling (M6 protection task) applies to the default
  endpoint, since it is the only one.
- A custom domain can be added later without code changes: a domain name, an API mapping, and
  new values for the URL settings.
