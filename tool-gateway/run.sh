#!/bin/bash
# Lambda entry (ADR 0019): the AWS Lambda Web Adapter layer (AWS_LAMBDA_EXEC_WRAPPER=/opt/bootstrap)
# runs this script once per environment, waits for GET /health, then turns each API Gateway event
# into an HTTP request to this server. The app is the same one `uv run gateway` serves locally.
set -euo pipefail
export PYTHONPATH="${LAMBDA_TASK_ROOT}${PYTHONPATH:+:${PYTHONPATH}}"
export GATEWAY_HOST=127.0.0.1
export GATEWAY_PORT="${AWS_LWA_PORT:-8080}"
export CONTRACTS_DIR="${CONTRACTS_DIR:-${LAMBDA_TASK_ROOT}/contracts}"
export CONNECTORS_DIR="${CONNECTORS_DIR:-${LAMBDA_TASK_ROOT}/connectors}"
exec python3 -m gateway
