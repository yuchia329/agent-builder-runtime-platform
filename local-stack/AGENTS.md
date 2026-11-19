# local-stack

The whole platform in Docker: local stand-ins for the AWS services (DynamoDB Local on port 8000,
ElasticMQ, a local SQS, on 9324 with read-only queue stats on 9325) plus the control plane (8080),
the tool gateway (8090) and a worker, built from `control-plane/Dockerfile`,
`tool-gateway/Dockerfile` and `worker/Dockerfile`. Files: root
`docker-compose.yml`, `.dockerignore`, `.env.example`, `Makefile`, and this folder.

## Commands

Run from the repo root. Docker must be running.

| What | Command |
| --- | --- |
| Build and start everything, wait until ready (creates `.env` if missing) | `make up` |
| Smoke test: publish the example spec, run it to `SUCCEEDED` with its tools through the gateway | `make demo` |
| Create the local run-token key pair (once; `make up` does it) | `make keys` (delete `.run-token-*.env` for a new pair) |
| Crash-safety demo: kill the worker mid-run, check the run resumes and adds its label once | `make chaos` (about 1 min) |
| Start only DynamoDB Local + ElasticMQ (to run a service on the host) | `make stack` |
| List tables and queues | `make status` |
| Follow logs | `make logs` |
| Stop and delete all data | `make down` |

To run a service on your machine instead, use `make stack` (otherwise the containers hold ports
8080 and the queue). The control plane (Gradle does not read `.env`):

```sh
cd control-plane && set -a && . ../.env && set +a && ./gradlew bootRun
```

## Conventions

- Tables are created by `create-tables.sh` from each service's own files:
  `control-plane/dynamodb/control-plane-table.json` (+ `control-plane-ttl.json`) and
  `worker/dynamodb/run-state-table.json` (+ `run-state-ttl.json`). Never define keys here; change those files. Every step checks
  first, so the script is safe to run again (DynamoDB refuses enabling TTL twice).
- `make down` deletes all local data, including agents you published by hand
  (`specs/issue-triage.yaml`): republish after it. `make demo` republishes `issue-triage` with the
  stub spec and needs the gateway's `CONNECTORS=fake` (the default; its spec lists M3 tools).
- Queues are declared in `elasticmq.conf`; ElasticMQ creates them on start.
- DynamoDB Local runs in memory: every restart starts empty.
- Pin every image version. Keep DynamoDB Local on the same version as the control-plane tests.
- `.env` holds host addresses (`localhost`). Containers reach each other by service name, so
  compose overrides the endpoints, `RUN_QUEUE_URL` and `CONTROL_PLANE_URL` per service. ElasticMQ's
  `node-address` stays `localhost` for host tools; the SDKs send to the endpoint, not the URL host.
- Images build inside Docker (no Java or uv on the host). Build context is the repo root; the
  root `.dockerignore` keeps `notes/`, `.env` and build output out of every image.
- `demo.sh` needs only `sh` and `curl`, and exits non-zero unless the run reaches `SUCCEEDED`.
- `chaos.sh` adds `chaos.compose.yml` and the `chaos` profile (service `fake-github`, port
  8099) only for its own run, then restores the normal worker; `make up` never starts
  `fake-github`. Its worker uses a dummy GitHub token against the fake, never the real one.
- After replacing the worker container, wait about 21 s before sending runs: the old container's
  open 20 s queue poll can swallow a message, which then stays hidden for its visibility timeout.
- Fake services use the standard library only and a pinned image (`python:3.12.8-slim`).
- `.env` is git-ignored. Containers get only what they need: the tool gateway gets named
  settings (never `env_file: .env`), and the Docker worker always uses `TOOLS_PROVIDER=mcp` with
  `GITHUB_TOKEN=""` (only the gateway holds credentials, ADR 0006). Keep it that way when adding
  a setting.
- Run-token keys live in `.run-token-private.env` (control plane only) and
  `.run-token-public.env` (control plane and gateway) at the repo root, written by
  `run-token-keys.sh`; git- and docker-ignored, and never under `local-stack/`, which is mounted
  into containers. Compose reads only `.env` for `${...}`, so these come in through `env_file`.
- The GitHub App `.pem` is mounted read-only only when `GITHUB_APP_PRIVATE_KEY_FILE` is set
  (`${VAR:+...}` gives the container path; `/dev/null` is mounted otherwise).
- `demo.sh` fails unless the gateway logged an access line for the run.
