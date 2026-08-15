# Command surface defined in docs/data-model.md, "Environment and commands".
#
# Recipes assume a POSIX shell. On Windows run them from Git Bash or WSL.

SHELL   := /bin/sh
COMPOSE := docker compose

.DEFAULT_GOAL := help
.PHONY: help env up down reset migrate psql refresh status logs generate demo build export replay replay-dry test ingest ingest-dry supplement supplement-dry

help:
	@echo "make up        start PostgreSQL and Grafana, apply migrations and seed"
	@echo "make down      stop everything, keep the volume"
	@echo "make reset     drop the volume and recreate from scratch"
	@echo "make migrate   re-apply pending migrations and the seed"
	@echo "make psql      open a psql shell on the running database"
	@echo "make refresh   rebuild the observation_1min rollup"
	@echo "make status    container status"
	@echo "make logs      follow logs"
	@echo "make ingest    load the measured data in data/ into PostgreSQL"
	@echo "make ingest-dry  parse and report without writing"
	@echo "make supplement  generate the synthetic channels nothing measured"
	@echo "make generate  run the generator in real time, 150 min"
	@echo "make demo      run the generator at 60x, 2.5 min"
	@echo "make test      generator tests"
	@echo "make build     rebuild the generator image"
	@echo "make export FILE=flight.ndjson   full flight to exports/"
	@echo "make replay FILE=flight.ndjson   replay it, live, as if it were happening now"
	@echo "make replay-dry FILE=flight.ndjson  read and report, write nothing"
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

# Rebuild the minute rollup (migration 005). make ingest, make supplement and
# make replay do this themselves; this is for a database loaded some other way.
refresh:
	$(COMPOSE) exec -T db sh -c 'psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB" \
	  -c "REFRESH MATERIALIZED VIEW CONCURRENTLY observation_1min;"'

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
	$(COMPOSE) run --rm --entrypoint pytest ingest /ingest/tests
	$(COMPOSE) run --rm --entrypoint pytest replayer /replayer/tests

# --- Measured data ----------------------------------------------------------

ingest: env
	$(COMPOSE) run --rm ingest $(ARGS)

ingest-dry:
	$(COMPOSE) run --rm ingest --dry-run $(ARGS)

# --- Synthetic supplement (DEC-18) ------------------------------------------

supplement: env
	$(COMPOSE) run --rm supplement $(ARGS)

supplement-dry:
	$(COMPOSE) run --rm supplement --dry-run $(ARGS)

build:
	$(COMPOSE) build generator

# --- File mode and replay (DEC-20) ------------------------------------------
# Files live in exports/, mounted into both containers as /exports.

export: env
	@test -n "$(FILE)" || { echo "usage: make export FILE=flight.ndjson"; exit 1; }
	$(COMPOSE) run --rm generator --sink ndjson --out /exports/$(FILE) $(ARGS)

replay: env
	@test -n "$(FILE)" || { echo "usage: make replay FILE=flight.ndjson"; exit 1; }
	$(COMPOSE) run --rm replayer --file /exports/$(FILE) $(ARGS)

replay-dry:
	@test -n "$(FILE)" || { echo "usage: make replay-dry FILE=flight.ndjson"; exit 1; }
	$(COMPOSE) run --rm replayer --file /exports/$(FILE) --dry-run $(ARGS)
