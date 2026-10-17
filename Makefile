.PHONY: up up-all down logs topics consume migrate companies financials financials-rebuild psql lint test

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

migrate:   ## apply new db/migrations/*.sql (needs DATABASE_URL, e.g. from .env)
	set -a; . ./.env; set +a; ./scripts/migrate.sh

companies: ## refresh company identity from the SEC (run daily)
	$(COMPOSE) --profile jobs build company-loader
	$(COMPOSE) --profile jobs run --rm company-loader

financials: ## fetch XBRL facts for tracked companies → bronze/silver/gold (run daily)
	$(COMPOSE) --profile jobs build facts-fetcher
	$(COMPOSE) --profile jobs run --rm facts-fetcher

financials-rebuild: ## re-derive silver/gold from stored bronze (after changing metric_tags)
	$(COMPOSE) --profile jobs build facts-fetcher
	$(COMPOSE) --profile jobs run --rm facts-fetcher facts-fetcher --rebuild

psql:      ## open a psql shell on the Tributary database
	$(COMPOSE) exec postgres psql -U tributary

lint:
	ruff check .

test:
	pytest
