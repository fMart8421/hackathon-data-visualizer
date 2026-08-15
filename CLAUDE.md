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
- **Dashboards as code.** JSON versioned in `grafana/dashboards/`, loaded via provisioning at startup. Changes made in the Grafana UI get exported and committed; nothing lives only in a local instance. Write the files without a BOM: PowerShell's `Set-Content -Encoding UTF8` adds one and Grafana refuses the file, while the previously-loaded version keeps rendering, so the failure is silent.
- **A dashboard is not verified until it has been looked at.** Panel SQL that returns correct rows still hides variable-interpolation errors, unresolved unit ids, geomap layers bound to the wrong query, and series drawn off the edge of an axis. Check `docker compose logs grafana` for provisioning errors too.
- **One dashboard per capture epoch.** The time range is dashboard-wide, and the captures are months apart; mixing them compresses every panel into an unreadable spike.
- **A new sensor is a catalog row**, never a schema or panel change. So is a new alert threshold: `metric.warn_low` and `warn_high` are what the alert rules read (DEC-22).
- **Live panels read the base tables.** `observation_1min` is materialized (DEC-21) and only as current as the last `make refresh`, which the loaders run themselves. Pointing a live panel at it would show a series that stops moving.
- **An alert query returns exactly one series.** Grafana's PostgreSQL datasource turns a string column into a series name rather than a label, so a query grouped by `metric_key` or `mission_id` dies with "frame cannot uniquely be identified by its labels" the moment two rows come back. Group on a constant, and never write a bare aggregate: with nothing running it returns one null-timestamped row and errors instead of reporting no data.

Full decisions with rationale: "Closed decisions" section (DEC-01 to DEC-12) in `docs/data-model.md`.

## Repository layout

```
.
├── CLAUDE.md
├── README.md                   running it, and the five-minute demo
├── EXPLAINME.md                what the data is, measured against generated
├── docs/data-model.md          data model, generator, roadmap, acceptance criteria
├── docker-compose.yml
├── db/migrations/               numbered SQL
├── db/seed/                     initial catalog: metric, device, sensor
├── data/                        provided measurements, read-only, not modified
├── generator/src/                flight.py, sensors.py, contract.py, sinks.py, cli.py
├── ingest/                       parsers per provided format (phase 3)
│   └── matlab/export_rtk.m       run in MATLAB to unlock the RTK captures
├── replayer/src/replay.py        NDJSON back in, at the file's own pace
├── exports/                      NDJSON flights, gitignored
└── grafana/
    ├── provisioning/datasources/
    ├── provisioning/dashboards/
    ├── provisioning/alerting/    rules, and the notification policy that mutes them
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
| `make supplement` | generate the synthetic channels nothing measured (DEC-18) |
| `make generate` | run generator in real time |
| `make demo` | run generator at 60x, for demonstration |
| `make export FILE=flight.ndjson` | generate a full flight into `exports/` (DEC-20) |
| `make replay FILE=flight.ndjson` | replay it, re-anchored to now under a fresh mission id |
| `make refresh` | rebuild the `observation_1min` rollup (DEC-21) |
| `make test` | generator, ingest and replayer tests |
| `make build` | rebuild the generator image |

The generator runs as a Compose one-off (DEC-15), so no Python is needed on the host. Pass extra flags with `ARGS`, e.g. `make demo ARGS="--duration-min 20"`.

Grafana at `localhost:3000`, PostgreSQL at `localhost:5432`. Credentials in `.env`, from `.env.example`.

## How to work in this repository

Follow the phase order in `docs/data-model.md`, "Roadmap" section. Each phase leaves the system working end to end; don't move to the next phase with the previous one half done. Summary:

1. Foundations: Compose, migrations, catalog seed. **Done.**
2. Minimal generator: altitude, trajectory, three scalars, direct write. **Done.**
3. Ingest layer: parsers for the chemistry files, catalog expansion, `mission.kind` migration. **Done.**
4. GNSS: precision files (ECEF to WGS84), the RTK CSV export, satellite counts. **Done.**
5. Dashboards for measured data: requirements 1, 2, 6, 7, 9. **Done** — `gnss-rtk`, `gnss-survey`, `volatiles`, `particulates`, `weather`, `metric-explorer`. (The Claude Design link is blocked by the browser's navigation policy, so layout follows the acceptance table instead — see OPEN-14.)
6. Synthetic supplement under DEC-18: magnetic vector, IMU waveforms, UV index, requirements 3, 4, 5, 8. **Done** — `make supplement`, dashboards `magnetometer`, `imu`, `uv-index`.
7. Polish: file mode, replayer, aggregates, alerts, demo README. **Done** — `--sink ndjson` and `make replay` (DEC-20), `observation_1min` materialized (DEC-21), three provisioned alert rules (DEC-22), the `Pipeline health` dashboard, `README.md` and `EXPLAINME.md`.

The roadmap is complete. New work is a change to a finished system, not the next phase: put the reasoning in `docs/data-model.md` before writing it, as every phase did.

Before marking a phase done, check it against the "Acceptance criteria" table in `docs/data-model.md`.

## Still open

Check the "Open questions" table in `docs/data-model.md` before deciding on your own: real IMU sample rate (OPEN-03), single vs multiple devices at once (OPEN-05), and the capture date for `RTK_BaseRover` (OPEN-13), which cannot be answered here at all — it needs whoever ran the capture. If a task depends on one of these, ask instead of assuming.

Closed in phase 7: alerts are in scope and catalog-driven (OPEN-06), TimescaleDB is not needed and the measurements are recorded (OPEN-08), and synthetic supplementation is accepted by the evaluators (OPEN-11). Accepted does not loosen DEC-18: generated data stays labelled everywhere it appears.
