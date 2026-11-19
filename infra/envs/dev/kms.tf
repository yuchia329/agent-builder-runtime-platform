# The run-token signing key (ADR 0003, ADR 0018): the control plane signs ES256 tokens with
# kms:Sign, the gateway verifies with the public key (kms:GetPublicKey). The private key never
# leaves KMS. The only fixed cost of the environment: $1 a month.

resource "aws_kms_key" "run_token" {
  description              = "${local.name} run-token signing key (ES256)"
  key_usage                = "SIGN_VERIFY"
  customer_master_key_spec = "ECC_NIST_P256"
  deletion_window_in_days  = 7
  # Default key policy: the account's IAM policies decide who may sign (control plane) and read
  # the public key (gateway). Asymmetric keys cannot rotate automatically; rotating means a new key.
}

resource "aws_kms_alias" "run_token" {
  name          = "alias/${local.name}-run-token"
  target_key_id = aws_kms_key.run_token.key_id
}
