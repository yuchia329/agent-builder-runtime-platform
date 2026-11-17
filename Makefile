.PHONY: up stack demo chaos down status logs

AWS = docker compose run --rm --no-deps --entrypoint aws aws-init
STACK = dynamodb elasticmq aws-init

## up: build and start everything (AWS stand-ins, control plane, worker); returns when ready
up: .env
	docker compose up -d --build --wait

## stack: start only DynamoDB Local and ElasticMQ, to run a service on your machine instead
stack: .env
	docker compose up -d $(STACK)
	@docker compose wait aws-init >/dev/null && docker compose logs --no-log-prefix aws-init | tail -1

## demo: publish the example spec, start a run, wait for SUCCEEDED (needs `make up`)
demo: .env
	@sh local-stack/demo.sh

## chaos: kill the worker mid-run and check the run resumes with no duplicate side effect (needs `make up`)
chaos: .env
	@sh local-stack/chaos.sh

## down: stop everything and delete local data
down:
	docker compose --profile chaos down -v

## status: list the local tables and queues
status:
	@echo "Tables:" && $(AWS) dynamodb list-tables --query 'TableNames' --output text
	@echo "Queues:" && $(AWS) sqs list-queues --query 'QueueUrls' --output text

## logs: follow container logs
logs:
	docker compose logs -f

.env:
	cp .env.example .env
