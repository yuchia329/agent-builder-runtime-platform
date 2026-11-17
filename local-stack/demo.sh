#!/bin/sh
# End-to-end smoke test against `make up`: publish the example spec, start a run, wait until the
# worker finishes it. Exits non-zero unless the run reaches SUCCEEDED. Needs only sh and curl.
set -eu

cd "$(dirname "$0")/.."
. ./.env
API="${API_URL:-http://localhost:8080}"
KEY="X-Api-Key: ${TENANT_API_KEY}"
SPEC=contracts/examples/agent-spec.valid.yaml
TIMEOUT=60

# Prints the response body; fails on any HTTP status not listed in $2 (default: 2xx).
call() { # call METHOD PATH [ALLOWED_STATUS] [curl args...]
  method=$1 path=$2 allowed=$3; shift 3
  out=$(curl -s -w '\n%{http_code}' -X "$method" "$API$path" -H "$KEY" "$@") || {
    echo "Cannot reach the control plane at $API. Is the stack up (make up)?" >&2; exit 1; }
  status=$(printf '%s' "$out" | tail -n 1)
  body=$(printf '%s' "$out" | sed '$d')
  case "$status" in 2??|$allowed) printf '%s' "$body" ;;
    *) echo "$method $path returned $status: $body" >&2; exit 1 ;;
  esac
}
field() { sed -n "s/.*\"$1\":\"\{0,1\}\([^\",}]*\).*/\1/p"; }

echo "1. Create agent issue-triage (409 if it already exists)"
call POST /agents 409 -H 'Content-Type: application/json' -d '{"name":"issue-triage"}' >/dev/null

echo "2. Publish $SPEC"
version=$(call POST /agents/issue-triage/versions none \
  -H 'Content-Type: application/yaml' --data-binary "@$SPEC" | field version)
echo "   version $version"

echo "3. Start a run"
run_id=$(call POST /runs none -H 'Content-Type: application/json' \
  -d '{"agent":"issue-triage","input":{"repository":{"full_name":"acme/payments"},"issue":{"number":42,"title":"Refund fails for EUR cards"}}}' \
  | field run_id)
echo "   $run_id"

echo "4. Wait for the worker (up to ${TIMEOUT}s)"
i=0
while :; do
  run=$(call GET "/runs/$run_id" none)
  status=$(printf '%s' "$run" | field status)
  case "$status" in SUCCEEDED|FAILED|WAITING_APPROVAL) break ;; esac
  i=$((i + 1))
  if [ "$i" -ge $((TIMEOUT * 2)) ]; then status="still $status after ${TIMEOUT}s"; break; fi
  sleep 0.5
done

echo "   $run"
if [ "$status" = SUCCEEDED ]; then
  echo "Demo passed: run $run_id SUCCEEDED in $(printf '%s' "$run" | field steps) steps."
else
  echo "Demo failed: run $run_id is $status. Worker logs:" >&2
  docker compose logs --tail 30 worker >&2 || true
  exit 1
fi
