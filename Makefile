.PHONY: up stack demo chaos down status logs keys service-env eval eval-baseline worker-image

AWS = docker compose run --rm --no-deps --entrypoint aws aws-init
STACK = dynamodb elasticmq aws-init

## up: build and start everything (AWS stand-ins, control plane, gateway, worker); returns when ready
up: .env keys service-env
	docker compose up -d --build --wait

## worker-image: build the worker image for Fargate (linux/arm64), tagged with the current commit
worker-image:
	docker build --platform linux/arm64 -f worker/Dockerfile -t agent-platform-worker:$$(git rev-parse --short HEAD) .

## stack: start only DynamoDB Local and ElasticMQ, to run a service on your machine instead
stack: .env keys service-env
	docker compose up -d $(STACK)
	@docker compose wait aws-init >/dev/null && docker compose logs --no-log-prefix aws-init | tail -1

## keys: create the local run-token key pair once (.run-token-*.env, git-ignored)
keys:
	@sh local-stack/run-token-keys.sh

## service-env: copy each service's own settings from .env into .worker.env and .control-plane.env (git-ignored)
service-env: .env
	@sh local-stack/service-env.sh

## demo: publish the example spec, start a run, wait for SUCCEEDED (needs `make up`)
demo: .env keys service-env
	@sh local-stack/demo.sh

## chaos: kill the worker mid-run and check the run resumes with no duplicate side effect (needs `make up`)
chaos: .env keys service-env
	@sh local-stack/chaos.sh

## eval: run agent evals on the spec's real model, compare with evals/baselines (SPEC=<name> for one; costs money)
eval: .env
	@cd evals && set -a && . ../.env && set +a && uv run evals suite $(SPEC)

## eval-baseline: like eval, then save the result as the new baseline (commit evals/baselines/)
eval-baseline: .env
	@cd evals && set -a && . ../.env && set +a && uv run evals suite --save-baseline $(SPEC)

## down: stop everything and delete local data (keeps the run-token keys)
down: keys service-env
	docker compose --profile chaos down -v

## status: list the local tables and queues
status: keys service-env
	@echo "Tables:" && $(AWS) dynamodb list-tables --query 'TableNames' --output text
	@echo "Queues:" && $(AWS) sqs list-queues --query 'QueueUrls' --output text

## logs: follow container logs
logs: keys service-env
	docker compose logs -f

.env:
	cp .env.example .env
