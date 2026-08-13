# Command surface defined in docs/data-model.md, "Environment and commands".
#
# Recipes assume a POSIX shell. On Windows run them from Git Bash or WSL.

SHELL   := /bin/sh
COMPOSE := docker compose

.DEFAULT_GOAL := help
.PHONY: help env up down reset migrate psql status logs generate demo build export replay test

help:
	@echo "make up        start PostgreSQL and Grafana, apply migrations and seed"
	@echo "make down      stop everything, keep the volume"
	@echo "make reset     drop the volume and recreate from scratch"
	@echo "make migrate   re-apply pending migrations and the seed"
	@echo "make psql      open a psql shell on the running database"
	@echo "make status    container status"
	@echo "make logs      follow logs"
	@echo "make generate  run the generator in real time, 150 min"
	@echo "make demo      run the generator at 60x, 2.5 min"
	@echo "make test      generator tests"
	@echo "make build     rebuild the generator image"
	@echo "make export FILE=flight.ndjson   full flight to file     (phase 6)"
	@echo "make replay FILE=flight.ndjson   replay a file           (phase 6)"
	@echo
	@echo "extra flags:   make demo ARGS=\"--duration-min 20 --seed 7\""

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

# --- Generator --------------------------------------------------------------
# One-off containers, so nothing has to be installed on the host. ARGS passes
# extra flags through, e.g. make demo ARGS="--duration-min 20".

generate: env
	$(COMPOSE) run --rm generator --speed 1 $(ARGS)

demo: env
	$(COMPOSE) run --rm generator --speed 60 $(ARGS)

test:
	$(COMPOSE) run --rm --entrypoint pytest generator

build:
	$(COMPOSE) build generator

# --- Phase 6 ----------------------------------------------------------------

export:
	@echo "file mode not implemented yet: phase 6 in docs/data-model.md" && exit 1

replay:
	@echo "replayer not implemented yet: phase 6 in docs/data-model.md" && exit 1
