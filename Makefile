.PHONY: up stack demo down status logs

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

## down: stop everything and delete local data
down:
	docker compose down -v

## status: list the local tables and queues
status:
	@echo "Tables:" && $(AWS) dynamodb list-tables --query 'TableNames' --output text
	@echo "Queues:" && $(AWS) sqs list-queues --query 'QueueUrls' --output text

## logs: follow container logs
logs:
	docker compose logs -f

.env:
	cp .env.example .env
