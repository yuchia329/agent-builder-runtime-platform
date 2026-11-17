# local-stack

Local stand-ins for the AWS services, in Docker: DynamoDB Local (port 8000) and ElasticMQ, a local
SQS (port 9324; read-only queue stats on 9325). Files: root `docker-compose.yml`, `.env.example`,
`Makefile`, and this folder.

## Commands

Run from the repo root. Docker must be running.

| What | Command |
| --- | --- |
| Start (creates `.env` from `.env.example` if missing) | `make up` |
| List tables and queues | `make status` |
| Follow logs | `make logs` |
| Stop and delete all data | `make down` |

Run the control plane against the stack (Gradle does not read `.env`):

```sh
cd control-plane && set -a && . ../.env && set +a && ./gradlew bootRun
```

## Conventions

- The control-plane table is created by `create-table.sh` from
  `control-plane/dynamodb/control-plane-table.json`. Never define its keys here; change that file.
- Queues are declared in `elasticmq.conf`; ElasticMQ creates them on start.
- DynamoDB Local runs in memory: every restart starts empty.
- Pin every image version. Keep DynamoDB Local on the same version as the control-plane tests.
- Queue URLs use `localhost:9324` because services run on the host. When services move into
  compose, set `node-address.host` (or per-service `RUN_QUEUE_URL`) to `elasticmq`.
- `.env` holds fake credentials only and is git-ignored.
