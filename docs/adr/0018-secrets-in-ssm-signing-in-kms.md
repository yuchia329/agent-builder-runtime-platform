# 0018. Secrets in SSM Parameter Store, run-token signing in KMS, values outside Terraform

- Status: Proposed
- Date: 2026-11-19
- Supersedes: the "Secrets Manager in AWS (M6)" line of ADR 0010

## Context

On AWS the services need secrets: the tenant API key, webhook and Slack signing secrets, the Slack
bot token, model provider API keys (ADR 0010 planned Secrets Manager for these), the gateway's
OAuth client secret and state secret, and the private key that signs run tokens (ADR 0003; a key
pair in env vars locally). The platform must cost nothing while idle (ADR 0007), and the repo,
including its Terraform, is public.

Options for stored secrets:

- **Secrets Manager:** rotation built in, $0.40 per secret a month (seven secrets: about $2.80).
- **SSM Parameter Store SecureString (standard tier):** free, encrypted with KMS, read by ECS task
  definitions natively and by SDKs; no built-in rotation.

Options for the run-token private key:

- **Keep it as a secret** (an SSM parameter the control plane reads): the private key exists in
  the function's memory and in its SnapStart snapshot.
- **A KMS asymmetric key** (`ECC_NIST_P256`, `SIGN_VERIFY`): the control plane calls `kms:Sign`,
  the gateway reads the public key; the private key never leaves KMS. $1 a month, about $0.000015
  per token.

And how Terraform handles values: a parameter made with `value` and `ignore_changes` still copies
the decrypted value into the state on every refresh (checked in the AWS provider 6.73 source).

## Decision

- **Stored secrets in SSM Parameter Store SecureString**, standard tier, under
  `/<project>/<environment>/<service>/<name>` (for example
  `/agent-platform/dev/control-plane/slack-bot-token`), encrypted with the AWS managed key
  `aws/ssm`. Who may read a parameter is decided by IAM on its path: each service's role gets only
  its own `<service>/` prefix. The gateway's `TokenStore` (ADR 0006) uses SecureString parameters
  too, under the gateway's prefix.
- **Run tokens are signed in KMS** with an `ECC_NIST_P256` key (ES256). Locally the key pair in
  env vars stays.
- **Values never pass through Terraform.** Terraform creates each parameter with a write-only
  `value_wo` set from an ephemeral random placeholder, so neither the plan nor the state holds a
  value, and a service started before the real value is put fails closed. The user puts real
  values with `infra/scripts/put_secrets.py` (from `.env`, or `--generate` for a new random one).
  `value_wo_version` is never changed, or Terraform would write a new placeholder over the value.

## Consequences

- Secrets cost $0; the KMS signing key is the environment's only fixed cost ($1 a month).
- No built-in rotation: rotating means putting a new value and restarting (or waiting for) the
  service. Fine for one user; revisit if tenants bring their own secrets.
- With `aws/ssm`, protection is IAM on parameter paths, not a key policy. A customer managed key
  ($1 a month) would add a second lock; not needed while one person administers the account.
- Lambda cannot inject SecureString values into environment variables: the control plane and the
  gateway read their parameters through the SDK on first use (not at startup: SnapStart, ADR
  0017). ECS injects the worker's through the task definition's `secrets`.
- The control plane needs a KMS signer behind its run-token issuer (JOSE signature from KMS's DER
  output) and the gateway gets the public key from KMS: M6 control-plane and gateway tasks.
- Terraform needs version 1.11 or later (write-only arguments) and the `random` provider.
- Development values stay out of AWS: the script refuses a tenant API key or state secret shorter
  than 32 characters.
