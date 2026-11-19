# Secret parameters (ADR 0018). Terraform creates each one with a random placeholder through the
# write-only `value_wo`, so no value is ever in a plan or the state; the user then puts the real
# value (infra/scripts/put_secrets.py, or aws ssm put-parameter --overwrite). A service that starts
# before that fails closed: the placeholder is random, not guessable.
#
# Never change `value_wo_version`: Terraform would write a new placeholder over the real value.
# Encrypted with the AWS managed key aws/ssm (free); who can read a parameter is decided by IAM on
# its path (/agent-platform/dev/<service>/...), in the compute task.

locals {
  # Parameter path under /<project>/<environment>/ => the env var the service reads (and the .env
  # name put_secrets.py copies from).
  secrets = {
    "control-plane/tenant-api-key"        = "TENANT_API_KEY"
    "control-plane/github-webhook-secret" = "GITHUB_WEBHOOK_SECRET"
    "control-plane/slack-bot-token"       = "SLACK_BOT_TOKEN"
    "control-plane/slack-signing-secret"  = "SLACK_SIGNING_SECRET"
    "worker/model/minimax/api-key"        = "MODEL_MINIMAX_API_KEY"
    "gateway/github-oauth-client-secret"  = "GITHUB_OAUTH_CLIENT_SECRET"
    "gateway/oauth-state-secret"          = "OAUTH_STATE_SECRET"
  }
}

ephemeral "random_password" "placeholder" {
  for_each = local.secrets
  length   = 40
  special  = false
}

resource "aws_ssm_parameter" "secret" {
  for_each = local.secrets

  name             = "/${var.project}/${local.environment}/${each.key}"
  description      = "${each.value}; value put by the user, not Terraform"
  type             = "SecureString"
  value_wo         = ephemeral.random_password.placeholder[each.key].result
  value_wo_version = 1
}
