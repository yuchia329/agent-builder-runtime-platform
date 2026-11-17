.PHONY: up down status logs

AWS = docker compose run --rm --no-deps --entrypoint aws aws-init

## up: start DynamoDB Local and ElasticMQ; returns once the table exists
up: .env
	docker compose up -d
	@docker compose wait aws-init >/dev/null && docker compose logs --no-log-prefix aws-init | tail -1

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
