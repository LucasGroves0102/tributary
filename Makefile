.PHONY: up up-all down logs topics consume migrate seed-app authz authz-test cdc propagation-test token backfill companies financials financials-bulk financials-rebuild seed-documents crawl reparse psql lint test hooks secrets ask adversarial web citations typecheck exhibits

COMPOSE := $(shell docker compose version >/dev/null 2>&1 && echo "docker compose" || echo docker-compose)
# use the repo virtualenv when there is one, so `make test` works without activating it
BIN := $(if $(wildcard .venv/bin/pytest),.venv/bin/,)

up:        ## Kafka, Postgres, OpenFGA
	$(COMPOSE) up -d
	set -a; . ./.env; set +a; ./scripts/create-databases.sh
	$(COMPOSE) --profile tools run --rm openfga-migrate
	$(COMPOSE) --profile authz up -d openfga

up-all:    ## infra + app services
	$(COMPOSE) --profile app up -d --build

down:
	$(COMPOSE) --profile app --profile authz --profile tools --profile jobs --profile crawl down

logs:
	$(COMPOSE) logs -f

topics:
	$(COMPOSE) exec kafka /opt/kafka/bin/kafka-topics.sh --bootstrap-server localhost:9092 --list

consume:   ## watch the raw filings topic
	$(COMPOSE) exec kafka /opt/kafka/bin/kafka-console-consumer.sh --bootstrap-server localhost:9092 --topic filings.raw --from-beginning

migrate:   ## apply new migrations to the tributary and app databases
	set -a; . ./.env; set +a; ./scripts/create-databases.sh && \
		./scripts/migrate.sh db/migrations "$$DATABASE_URL" && \
		./scripts/migrate.sh db/app/migrations "$$APP_DATABASE_URL"

seed-app:  ## demo users, grants and watchlists in the app database
	set -a; . ./.env; set +a; psql "$$APP_DATABASE_URL" -v ON_ERROR_STOP=1 -q -f db/app/seed.sql

cdc:       ## register or update the Debezium connector for the app database
	set -a; . ./.env; set +a; ./scripts/register-connector.sh

propagation-test: ## revoke access live and time propagation (needs the stack + authz-sync)
	set -a; . ./.env; set +a; E2E=1 $(BIN)pytest -q -s tests/propagation

token:     ## dev bearer token for the Query API: make token USER=alice
	@set -a; . ./.env; set +a; $(BIN)query-api-token $(or $(USER_ID),$(USER))

ask:       ## ask the agent as a user (needs query-api running): make ask USER=alice Q="..."
	@set -a; . ./.env; set +a; \
	AGENT_TOKEN=$$($(BIN)query-api-token $(or $(USER_ID),$(USER))) $(BIN)agent-ask "$(Q)"

citations: ## citation-accuracy eval (~$0.03 on Haiku; needs query-api); CI replays it
	@set -a; . ./.env; set +a; $(BIN)python tests/citations/run.py $(CASES)

web:       ## chat UI dev server on http://localhost:5173 (needs query-api and agent-api)
	cd services/web && npm install --no-audit --no-fund && npm run dev

adversarial: ## FR10 live attack suite (~$0.10 on Haiku; needs query-api); CI replays it
	@set -a; . ./.env; set +a; $(BIN)python tests/adversarial/run.py $(CASES)

authz:     ## create the OpenFGA store if missing and write authz/model.fga
	COMPOSE="$(COMPOSE)" ./scripts/authz-bootstrap.sh

authz-test: ## run the OpenFGA model tests in authz/model.fga.yaml
	docker run --rm -v "$(CURDIR)/authz:/authz:ro" -w /authz openfga/cli:v0.6.3 model test --tests model.fga.yaml

backfill:  ## publish missed filings from EDGAR daily indexes: make backfill DAYS=7
	$(COMPOSE) --profile app build poller
	$(COMPOSE) --profile app run --rm -e SEC_CALLER_CLASS=batch poller poller-backfill --days $(or $(DAYS),1)

companies: ## refresh company identity from the SEC (run daily)
	$(COMPOSE) --profile jobs build company-loader
	$(COMPOSE) --profile jobs run --rm company-loader

financials: ## fetch XBRL facts for tracked companies → bronze/silver/gold (run daily)
	$(COMPOSE) --profile jobs build facts-fetcher
	$(COMPOSE) --profile jobs run --rm facts-fetcher

financials-bulk: ## every filer's XBRL facts from a local companyfacts.zip: make financials-bulk ZIP=... LIMIT=500
	@set -a; . ./.env; set +a; $(BIN)facts-fetcher-bulk --zip $(ZIP) --limit $(or $(LIMIT),500)

financials-rebuild: ## re-derive silver/gold from stored bronze (after changing metric_tags)
	$(COMPOSE) --profile jobs build facts-fetcher
	$(COMPOSE) --profile jobs run --rm facts-fetcher facts-fetcher --rebuild

seed-documents: ## publish tracked companies' recent 10-K/10-Q/8-K filings to filings.raw
	$(COMPOSE) --profile app build doc-fetcher
	$(COMPOSE) --profile app run --rm -e SEC_CALLER_CLASS=bulk doc-fetcher doc-fetcher-seed

crawl:     ## document backfill for every operating company (doc-crawler + parser-backfill)
	$(COMPOSE) --profile crawl up -d --build doc-crawler parser-backfill

exhibits:  ## fetch EX-99.x exhibits for stored 8-Ks, then re-parse
	@set -a; . ./.env; set +a; $(BIN)doc-fetcher-exhibits
	$(MAKE) reparse

reparse:   ## re-parse every stored document (after changing the parser)
	$(COMPOSE) --profile app build parser
	$(COMPOSE) --profile app run --rm parser parser --reparse-all

psql:      ## open a psql shell on the Tributary database
	$(COMPOSE) exec postgres psql -U tributary

lint:
	$(BIN)ruff check .

typecheck:
	$(BIN)pyright --pythonpath $(or $(wildcard .venv/bin/python),python3)
	cd services/web && npm run -s typecheck

hooks:     ## install the git pre-commit hook (gitleaks secret scan)
	$(BIN)pre-commit install

secrets:   ## scan the full git history for secrets (same check as CI)
	docker run --rm -v "$$PWD:/repo" -w /repo zricethezav/gitleaks:v8.30.1 git --redact --no-banner .

test:
	$(BIN)pytest
