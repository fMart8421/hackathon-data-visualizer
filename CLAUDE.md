# CLAUDE.md

Startup context for Claude Code in this repository. Read this before touching schema, generator, dashboards, or infrastructure.

## What this project is

Grafana-style telemetry dashboard for an instrumented mobile platform (GNSS, IMU, magnetometer, gas sensors, UV, weather, lightning detection).

**Real measurements were provided on 2026-08-13, in `data/`:** gas and volatile logs, particulate runs including three geo-referenced bike routes, GNSS precision and RTK captures, and satellite observables. These are the source of truth (DEC-17). The generator still exists, but only supplements the four requirements nothing was measured for (geomagnetic vector, IMU waveforms, UV index), and generated data must obey the conventions the real data sets (DEC-18).

Full reference document, with the data model, generator spec, phased roadmap, and acceptance criteria: **`docs/data-model.md`**. This file summarizes the rules that always apply. For any detail, check the full document before assuming.

Reference layout for the dashboards, produced in Claude Design: https://claude.ai/design/p/aebf1c35-a6ce-4706-9433-5a3653237537?file=Telemetry+Dashboard.dc.html&via=share

## Stack, locked

PostgreSQL 16, Grafana OSS, generator in Python 3.12, Docker Compose. No message broker, no custom HTTP backend, no ORM. TimescaleDB only if waveform queries turn out to be slow, and only after that decision is logged in `docs/data-model.md`.

Don't add new dependencies without a written justification in that document first.

## Non-negotiable rules

- **Measured data wins.** Real files in `data/` are ingested and are what panels show by default. The generator fills only what was never measured, and every synthetic mission is labelled `mission.kind = 'synthetic'`. Never let a panel imply a generated number was measured.
- **A flat or empty panel is a data-source bug**, in the ingest or the generator, not a dashboard bug.
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
├── data/                        provided measurements, read-only, not modified
├── generator/src/                flight.py, sensors.py, contract.py, sinks.py, cli.py
├── ingest/                       parsers per provided format (phase 3)
│   └── matlab/export_rtk.m       run in MATLAB to unlock the RTK captures
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
| `make ingest` | load the measured data in `data/` into PostgreSQL |
| `make ingest-dry` | parse and report without writing |
| `make generate` | run generator in real time |
| `make demo` | run generator at 60x, for demonstration |
| `make export FILE=flight.ndjson` | generate a full flight to file |
| `make replay FILE=flight.ndjson` | replay a file into the database |
| `make test` | generator tests |
| `make build` | rebuild the generator image |

The generator runs as a Compose one-off (DEC-15), so no Python is needed on the host. Pass extra flags with `ARGS`, e.g. `make demo ARGS="--duration-min 20"`.

Grafana at `localhost:3000`, PostgreSQL at `localhost:5432`. Credentials in `.env`, from `.env.example`.

## How to work in this repository

Follow the phase order in `docs/data-model.md`, "Roadmap" section. Each phase leaves the system working end to end; don't move to the next phase with the previous one half done. Summary:

1. Foundations: Compose, migrations, catalog seed. **Done.**
2. Minimal generator: altitude, trajectory, three scalars, direct write. **Done.**
3. Ingest layer: parsers for the chemistry files, catalog expansion, `mission.kind` migration. **Done.**
4. GNSS: precision files (ECEF to WGS84), the RTK CSV export, satellite skyplot.
5. Dashboards for measured data: requirements 1, 2, 6, 7, 9, layout per the Claude Design link above.
6. Synthetic supplement under DEC-18: magnetic vector, IMU waveforms, UV index, requirements 3, 4, 5, 8.
7. Polish: file mode, replayer, aggregates, alerts, demo README.

Before marking a phase done, check it against the "Acceptance criteria" table in `docs/data-model.md`.

## Still open

Check the "Open questions" table in `docs/data-model.md` before deciding on your own: real IMU sample rate (OPEN-03), retention policy between demos (OPEN-04), single vs multiple devices at once (OPEN-05), whether alerts are in scope (OPEN-06), whether TimescaleDB is needed (OPEN-08). If a task depends on one of these, ask instead of assuming.

Opened when the real data arrived: the RTK export from MATLAB (OPEN-09), the time base for the sources that have no timestamps (OPEN-10), whether the evaluators accept synthetic supplementation at all (OPEN-11), and what `CH4/` actually measures given its contradictory header (OPEN-12). OPEN-12 in particular must not be guessed: ingesting it under the wrong metric key would be a real error.
