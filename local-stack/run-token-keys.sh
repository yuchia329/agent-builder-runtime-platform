#!/bin/sh
# Writes .run-token-private.env and .run-token-public.env at the repo root: a fresh P-256 pair for run tokens (ADR 0003), one line
# each (base64 body). The control plane gets both files and signs; the gateway gets only the
# public one and verifies. Local only: git-ignored, kept out of images and out of the folders mounted into
# containers, never reused on AWS (M6: KMS).
# Does nothing if both exist; delete them to get a new pair. Needs openssl.
set -eu

cd "$(dirname "$0")/.."
PRIVATE=.run-token-private.env
PUBLIC=.run-token-public.env
[ -f "$PRIVATE" ] && [ -f "$PUBLIC" ] && exit 0

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
umask 077
openssl ecparam -name prime256v1 -genkey -noout 2>/dev/null \
  | openssl pkcs8 -topk8 -nocrypt -out "$tmp/key.pem"
openssl ec -in "$tmp/key.pem" -pubout -out "$tmp/pub.pem" 2>/dev/null
{
  echo "# Local run-token signing key from local-stack/run-token-keys.sh. Secret; never commit."
  echo "RUN_TOKEN_PRIVATE_KEY=$(grep -v -- ----- "$tmp/key.pem" | tr -d '\n')"
} > "$PRIVATE"
{
  echo "# Public half of $PRIVATE (local-stack/run-token-keys.sh)."
  echo "RUN_TOKEN_PUBLIC_KEY=$(grep -v -- ----- "$tmp/pub.pem" | tr -d '\n')"
} > "$PUBLIC"
echo "Created $PRIVATE and $PUBLIC (run-token key pair)"
