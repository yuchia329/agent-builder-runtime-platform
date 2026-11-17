#!/bin/sh
# M2 crash-safety demo against `make up`: start a run, kill the worker container (SIGKILL) while
# the run waits on a slow GitHub search, restart it, and check the run still succeeds with the
# label added exactly once. The search is read-only, so its repeat after the crash is expected
# (ADR 0013); the side effect (add label) must happen once. Needs sh, curl and docker.
set -eu

cd "$(dirname "$0")/.."
. ./.env
API="${API_URL:-http://localhost:8080}"
FAKE=http://localhost:8099
KEY="X-Api-Key: ${TENANT_API_KEY}"
CHAOS="docker compose -f docker-compose.yml -f local-stack/chaos.compose.yml --profile chaos"

restore() {
  echo "Restoring the normal worker"
  docker compose up -d --wait worker >/dev/null 2>&1 || true
  docker compose --profile chaos stop fake-github >/dev/null 2>&1 || true
}
trap restore EXIT

field() { sed -n "s/.*\"$1\": *\"\{0,1\}\([^\",}]*\).*/\1/p"; }
wait_for() { # wait_for SECONDS COMMAND...: runs COMMAND every 0.5 s until it succeeds
  limit=$(($1 * 2)); shift; i=0
  until "$@"; do
    i=$((i + 1)); [ "$i" -ge "$limit" ] && return 1
    sleep 0.5
  done
}

echo "1. Start fake GitHub and a worker that uses it"
$CHAOS up -d --wait fake-github worker >/dev/null 2>&1
wait_for 20 curl -sf "$FAKE/_requests" >/dev/null
# The replaced worker's last long poll (20 s) stays open at the queue; a message sent now could be
# handed to that dead connection and stay hidden for 60 s. Let it expire first.
echo "   waiting 21 s for the old worker's queue poll to expire"
sleep 21
curl -s -X POST "$FAKE/_reset" >/dev/null

echo "2. Publish local-stack/chaos-spec.yaml as chaos-demo"
curl -s -o /dev/null -X POST "$API/agents" -H "$KEY" -H 'Content-Type: application/json' \
  -d '{"name":"chaos-demo"}'
curl -sf -o /dev/null -X POST "$API/agents/chaos-demo/versions" -H "$KEY" \
  -H 'Content-Type: application/yaml' --data-binary @local-stack/chaos-spec.yaml

echo "3. Start a run"
run_id=$(curl -sf -X POST "$API/runs" -H "$KEY" -H 'Content-Type: application/json' \
  -d '{"agent":"chaos-demo","input":{"repository":{"full_name":"acme/payments"},"issue":{"number":42}}}' \
  | field run_id)
echo "   $run_id"

echo "4. Wait until the worker is inside the slow search, then kill it (SIGKILL)"
searching() { [ "$(curl -s "$FAKE/_requests" | field search)" -ge 1 ]; }
wait_for 30 searching || { echo "The worker never called search" >&2; exit 1; }
docker compose kill worker >/dev/null 2>&1
echo "   worker killed mid-run"

echo "5. Restart the worker; the run comes back after its visibility timeout (10 s)"
docker compose start worker >/dev/null 2>&1
finished() {
  status=$(curl -s "$API/runs/$run_id" -H "$KEY" | field status)
  [ "$status" = SUCCEEDED ] || [ "$status" = FAILED ]
}
wait_for 90 finished || true

run=$(curl -s "$API/runs/$run_id" -H "$KEY")
status=$(printf '%s' "$run" | field status)
steps=$(printf '%s' "$run" | field steps)
requests=$(curl -s "$FAKE/_requests")
searches=$(printf '%s' "$requests" | field search)
labels=$(printf '%s' "$requests" | field add_label)
echo "   run: $status in $steps steps; GitHub saw $searches search(es), $labels add-label request(s)"

if [ "$status" = SUCCEEDED ] && [ "$steps" = 5 ] && [ "$labels" = 1 ] && [ "$searches" = 2 ]; then
  echo "Chaos passed: the killed run resumed from its checkpoint and added the label once."
else
  echo "Chaos failed (expected SUCCEEDED, 5 steps, 2 searches, 1 add-label). Worker logs:" >&2
  docker compose logs --tail 40 worker >&2 || true
  exit 1
fi
