#!/bin/sh
# Creates the local DynamoDB tables from each service's own definition files. Safe to run again.
#   control-plane: control-plane/dynamodb/control-plane-table.json + control-plane-ttl.json
#                  (mounted at /defs/control-plane)
#   run-state:     worker/dynamodb/run-state-table.json + run-state-ttl.json (at /defs/worker)
set -eu

CONTROL_PLANE_TABLE="${CONTROL_PLANE_TABLE:-control-plane}"
STATE_TABLE="${STATE_TABLE:-run-state}"

i=0
until aws dynamodb list-tables >/dev/null 2>&1; do
  i=$((i + 1))
  if [ "$i" -ge 30 ]; then
    echo "DynamoDB Local did not answer after 30 s" >&2
    exit 1
  fi
  sleep 1
done

create() { # table name, definition file
  if aws dynamodb describe-table --table-name "$1" >/dev/null 2>&1; then
    echo "Table $1 already exists"
  else
    aws dynamodb create-table --cli-input-json "file://$2" --table-name "$1" >/dev/null
    echo "Created table $1"
  fi
}

create "$CONTROL_PLANE_TABLE" /defs/control-plane/control-plane-table.json
create "$STATE_TABLE" /defs/worker/run-state-table.json

# DynamoDB refuses to enable TTL twice, so check first.
enable_ttl() { # table name, TTL file
  ttl=$(aws dynamodb describe-time-to-live --table-name "$1" \
    --query 'TimeToLiveDescription.TimeToLiveStatus' --output text)
  if [ "$ttl" = "ENABLED" ]; then
    echo "TTL already enabled on $1"
  else
    aws dynamodb update-time-to-live --cli-input-json "file://$2" --table-name "$1" >/dev/null
    echo "Enabled TTL on $1"
  fi
}

enable_ttl "$CONTROL_PLANE_TABLE" /defs/control-plane/control-plane-ttl.json
enable_ttl "$STATE_TABLE" /defs/worker/run-state-ttl.json
