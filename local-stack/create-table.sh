#!/bin/sh
# Creates the control-plane table in DynamoDB Local from the control plane's own definition
# (control-plane/dynamodb/control-plane-table.json, mounted at /defs). Safe to run again.
set -eu

TABLE="${CONTROL_PLANE_TABLE:-control-plane}"

i=0
until aws dynamodb list-tables >/dev/null 2>&1; do
  i=$((i + 1))
  if [ "$i" -ge 30 ]; then
    echo "DynamoDB Local did not answer after 30 s" >&2
    exit 1
  fi
  sleep 1
done

if aws dynamodb describe-table --table-name "$TABLE" >/dev/null 2>&1; then
  echo "Table $TABLE already exists"
else
  aws dynamodb create-table --cli-input-json file:///defs/control-plane-table.json \
    --table-name "$TABLE" >/dev/null
  echo "Created table $TABLE"
fi
