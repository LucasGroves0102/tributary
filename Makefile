.PHONY: up up-all down logs topics consume lint test

COMPOSE := $(shell docker compose version >/dev/null 2>&1 && echo "docker compose" || echo docker-compose)

up:        ## Kafka + Postgres only
	$(COMPOSE) up -d

up-all:    ## infra + app services
	$(COMPOSE) --profile app up -d --build

down:
	$(COMPOSE) --profile app down

logs:
	$(COMPOSE) logs -f

topics:
	$(COMPOSE) exec kafka /opt/kafka/bin/kafka-topics.sh --bootstrap-server localhost:9092 --list

consume:   ## watch the raw filings topic
	$(COMPOSE) exec kafka /opt/kafka/bin/kafka-console-consumer.sh --bootstrap-server localhost:9092 --topic filings.raw --from-beginning

lint:
	ruff check .

test:
	pytest
