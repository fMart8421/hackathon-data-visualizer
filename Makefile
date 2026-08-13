# Command surface defined in docs/data-model.md, "Environment and commands".
#
# Recipes assume a POSIX shell. On Windows run them from Git Bash or WSL.

SHELL   := /bin/sh
COMPOSE := docker compose

.DEFAULT_GOAL := help
.PHONY: help env up down reset migrate psql status logs generate demo export replay test

help:
	@echo "make up        start PostgreSQL and Grafana, apply migrations and seed"
	@echo "make down      stop everything, keep the volume"
	@echo "make reset     drop the volume and recreate from scratch"
	@echo "make migrate   re-apply pending migrations and the seed"
	@echo "make psql      open a psql shell on the running database"
	@echo "make status    container status"
	@echo "make logs      follow logs"
	@echo "make generate  run the generator in real time            (phase 2)"
	@echo "make demo      run the generator at 60x                  (phase 2)"
	@echo "make export FILE=flight.ndjson   full flight to file     (phase 6)"
	@echo "make replay FILE=flight.ndjson   replay a file           (phase 6)"
	@echo "make test      generator tests                           (phase 2)"

# .env is gitignored; first run bootstraps it from the versioned example.
env:
	@test -f .env || { cp .env.example .env; echo "created .env from .env.example"; }

up: env
	$(COMPOSE) up -d --wait
	$(COMPOSE) exec -T db sh /db/apply.sh
	@echo
	@echo "Grafana     http://localhost:$$(grep -E '^GRAFANA_PORT=' .env | cut -d= -f2)"
	@echo "PostgreSQL  localhost:$$(grep -E '^POSTGRES_PORT=' .env | cut -d= -f2)"

down:
	$(COMPOSE) down

reset:
	$(COMPOSE) down --volumes
	@$(MAKE) up

migrate:
	$(COMPOSE) exec -T db sh /db/apply.sh

psql:
	$(COMPOSE) exec db sh -c 'psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB"'

status:
	$(COMPOSE) ps

logs:
	$(COMPOSE) logs -f --tail=100

# --- Phases 2 and 6 ---------------------------------------------------------
# Declared here because the command table in docs/data-model.md is the contract.
# Each becomes a real recipe when its phase lands.

generate:
	@echo "generator not implemented yet: phase 2 in docs/data-model.md" && exit 1

demo:
	@echo "generator not implemented yet: phase 2 in docs/data-model.md" && exit 1

export:
	@echo "file mode not implemented yet: phase 6 in docs/data-model.md" && exit 1

replay:
	@echo "replayer not implemented yet: phase 6 in docs/data-model.md" && exit 1

test:
	@echo "generator tests not implemented yet: phase 2 in docs/data-model.md" && exit 1
