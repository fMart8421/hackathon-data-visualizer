# CLAUDE.md

Startup context for Claude Code in this repository. Read this before touching schema, generator, dashboards, or infrastructure.

## What this project is

Grafana-style telemetry dashboard for an instrumented mobile platform (GNSS, IMU, magnetometer, gas sensors, UV, weather, lightning detection). No data is provided: a custom generator produces a synthetic, physically plausible stratospheric balloon flight and feeds the database in real time or accelerated.

Full reference document, with the data model, generator spec, phased roadmap, and acceptance criteria: **`docs/data-model.md`**. This file summarizes the rules that always apply. For any detail, check the full document before assuming.

Reference layout for the dashboards, produced in Claude Design: https://claude.ai/design/p/aebf1c35-a6ce-4706-9433-5a3653237537?file=Telemetry+Dashboard.dc.html&via=share

## Stack, locked

PostgreSQL 16, Grafana OSS, generator in Python 3.12, Docker Compose. No message broker, no custom HTTP backend, no ORM. TimescaleDB only if waveform queries turn out to be slow, and only after that decision is logged in `docs/data-model.md`.

Don't add new dependencies without a written justification in that document first.

## Non-negotiable rules

- **No data file provided.** All telemetry comes from the generator in `generator/`. A flat or empty panel is a generator bug, not a dashboard bug.
- **Two timestamps per record.** `event_time` (moment of measurement) and `ingested_at` (arrival in the database). Always UTC, millisecond resolution.
- **Narrow format for observations.** `metric_key` plus `value`, never one column per quantity. This is what keeps dashboards flexible without editing panels for every new sensor.
- **The `metric` catalog is the source of truth.** Dashboard variables and repeated panels query that table. Don't hardcode metric lists in panel queries.
- **Waveform stored in blocks, never sample by sample.** See `waveform_block` in the data model. Sample-by-sample at 100 Hz or higher blows up volume for no reason.
- **Raw and calibrated, always both.** `value_raw` and `value` in every observation.
- **Dashboards as code.** JSON versioned in `grafana/dashboards/`, loaded via provisioning at startup. Changes made in the Grafana UI get exported and committed; nothing lives only in a local instance.
- **A new sensor is a catalog row**, never a schema or panel change.

Full decisions with rationale: "Closed decisions" section (DEC-01 to DEC-12) in `docs/data-model.md`.

## Repository layout

```
.
├── CLAUDE.md
├── docs/data-model.md          data model, generator, roadmap, acceptance criteria
├── docker-compose.yml
├── db/migrations/               numbered SQL
├── db/seed/                     initial catalog: metric, device, sensor
├── generator/src/                flight.py, sensors.py, contract.py, sinks.py, cli.py
├── replayer/src/replay.py
└── grafana/
    ├── provisioning/datasources/
    ├── provisioning/dashboards/
    └── dashboards/               one JSON per dashboard
```

## Commands

| Command | Effect |
|---|---|
| `make up` | start PostgreSQL and Grafana, apply migrations and seed |
| `make down` | stop everything, keep the volume |
| `make reset` | drop the volume and recreate from scratch |
| `make generate` | run generator in real time |
| `make demo` | run generator at 60x, for demonstration |
| `make export FILE=flight.ndjson` | generate a full flight to file |
| `make replay FILE=flight.ndjson` | replay a file into the database |
| `make test` | generator tests |

Grafana at `localhost:3000`, PostgreSQL at `localhost:5432`. Credentials in `.env`, from `.env.example`.

## How to work in this repository

Follow the phase order in `docs/data-model.md`, "Roadmap" section. Each phase leaves the system working end to end; don't move to the next phase with the previous one half done. Summary:

1. Foundations: Compose, migrations, catalog seed.
2. Minimal generator: altitude, trajectory, three scalars, direct write.
3. Metric coverage: remaining scalars, magnetic vector, gases, UV, events.
4. Waveform: accelerometer and gyroscope in blocks, envelope view.
5. Dashboards: one panel per requirement in the brief, layout per the Claude Design link above.
6. Polish: file mode, replayer, aggregates, alerts, demo README.

Before marking a phase done, check it against the "Acceptance criteria" table in `docs/data-model.md`.

## Still open

Check the "Open questions" table in `docs/data-model.md` before deciding on your own: real IMU sample rate (OPEN-03), retention policy between demos (OPEN-04), single vs multiple devices at once (OPEN-05), whether alerts are in scope (OPEN-06), whether TimescaleDB is needed (OPEN-08). If a task depends on one of these, ask instead of assuming.
