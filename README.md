# Multi-sensor telemetry platform

Telemetry from an instrumented mobile platform — GNSS, gas sensors, particulates, IMU,
magnetometer, UV — stored in PostgreSQL and shown in Grafana, live.

Everything runs from Docker Compose. Two containers, one command to start, no interpreter
and no database client needed on the host.

- **What the data is, and which parts of it are real:** [EXPLAINME.md](EXPLAINME.md)
- **Why the schema and the decisions look like this:** [docs/data-model.md](docs/data-model.md)

---

## Quick start

You need Docker Desktop (or Docker Engine with Compose v2), about 2 GB of free disk, and
ports 3000 and 5432 free. The measurements live in `data/` at the repository root; that
folder is 663 MB and is deliberately not committed, so copy it in before ingesting.

```bash
cp .env.example .env
make up
make ingest
make supplement
```

Then open <http://localhost:3000>, sign in with the credentials in `.env`, and open the
**Telemetry** folder. `make up` prints the URLs it just started.

`make up` is idempotent and safe to re-run: it applies pending migrations, re-applies the
catalog seed, and leaves existing data alone. `make ingest` and `make supplement` are
idempotent too — running them twice inserts nothing the second time (DEC-03).

### No `make` on this machine

Every target is a thin wrapper around `docker compose`. The equivalents:

| Instead of | Run |
|---|---|
| `make up` | `docker compose up -d --wait` then `docker compose exec -T db sh /db/apply.sh` |
| `make ingest` | `docker compose run --rm ingest` |
| `make supplement` | `docker compose run --rm supplement` |
| `make demo` | `docker compose run --rm generator --speed 60` |
| `make export FILE=f.ndjson` | `docker compose run --rm generator --sink ndjson --out /exports/f.ndjson` |
| `make replay FILE=f.ndjson` | `docker compose run --rm replayer --file /exports/f.ndjson` |
| `make test` | `docker compose run --rm --entrypoint pytest generator` (repeat for `ingest /ingest/tests` and `replayer /replayer/tests`) |
| `make psql` | `docker compose exec db psql -U telemetry -d telemetry` |

On Git Bash, prefix commands that pass container paths with `MSYS_NO_PATHCONV=1`, or
`/exports/flight.ndjson` is rewritten into a Windows path before Docker sees it.

---

## What is on screen

Eleven dashboards, all provisioned from `grafana/dashboards/` at startup (DEC-10). Every
dashboard carries a link bar to the others.

| Dashboard | Requirement | Source | What it shows |
|---|---|---|---|
| GNSS RTK | 1 | measured | Base and rover on a map, RTK-fixed scatter of 1.9 cm against 3.2 m standalone |
| GNSS survey-in | 1 | measured | A 12-day static occupation converging from 0.117 m to 0.013 m |
| Volatiles | 2 | measured | Gas species selector driving linked series, raw ADC against calibrated ppm |
| Particulates | 2 | measured | Three geo-referenced bike runs around Taveiro, route coloured by PM concentration |
| Magnetometer | 3 | **synthetic** | Field vector along the ride, markers rotated to the horizontal bearing |
| IMU waveforms | 4, 8 | **synthetic** | 100 Hz accelerometer and gyroscope, `$channel` and `$axis` driving repeated panels |
| UV index | 5 | **synthetic** | Gauge on the WHO bands plus the 24 hour profile |
| Weather | 6 | measured | Temperature and humidity from the bench logs. Not a map: those rigs carry no GPS |
| Metric explorer | 7 | either | One panel per metric, generated from the catalog. No JSON edit to add a quantity |
| Pipeline health | 9 | either | Inventory, end-to-end lag, rows arriving per second, alert state |
| Flight live | — | **synthetic** | The generated balloon flight, for watching data arrive in real time |

Dashboards marked **synthetic** are fed by generated data and say so in their title, in a
banner, and in the mission description. Nothing generated is ever shown as measured — see
[EXPLAINME.md](EXPLAINME.md#measured-against-generated).

Each dashboard pins its own absolute time range, because the captures are months apart and
a range wide enough to hold two of them compresses both into a spike.

---

## A five-minute demo

1. **What is in the database.** Open *Pipeline health*. The inventory table at the bottom
   lists every capture, `measured` in green and `synthetic` in orange.
2. **Real measurements.** *GNSS RTK* for centimetre positioning, then *Particulates* for a
   real bike route coloured by particle concentration, then *Volatiles* for the gas
   species selector.
3. **Live data.** Leave *Flight live* open and run:

   ```bash
   make demo
   ```

   A 150 minute flight arrives in 2.5 minutes of wall clock. Nothing is reloaded by hand:
   the dashboard refreshes every 5 s and the series grow (requirement 9). *Pipeline health*
   shows the rows arriving and the end-to-end lag, about 1 ms.
4. **The same flight again, from a file.**

   ```bash
   make export FILE=flight.ndjson
   make replay FILE=flight.ndjson ARGS="--speed 60"
   ```

   The export is the wire contract, one JSON object per line. The replay re-anchors it to
   now under a fresh mission id, so it arrives live all over again.
5. **An alert firing.** The catalog decides what is out of band, not the alert rule:

   ```bash
   make psql
   ```
   ```sql
   UPDATE metric SET warn_low = 0 WHERE metric_key = 'air_temperature';
   ```

   Within a minute the *Reading outside the catalog's warning band* rule turns red on
   *Pipeline health*, because the balloon is well below freezing. Undo it with
   `make migrate`, which re-applies the seed.

   Killing a `make demo` run mid-flight fires the other one: the mission never got an
   `ended_at`, so *Live stream stopped mid-mission* goes red 30 s later.

---

## Commands

| Command | Effect |
|---|---|
| `make up` | start PostgreSQL and Grafana, apply migrations and seed |
| `make down` | stop everything, keep the data |
| `make reset` | drop the volume and start over |
| `make migrate` | re-apply pending migrations and the catalog seed |
| `make ingest` | load the measurements in `data/` |
| `make ingest-dry` | parse and report, write nothing |
| `make supplement` | generate the four channels nothing measured |
| `make generate` | run the balloon generator in real time |
| `make demo` | the same flight at 60x |
| `make export FILE=f.ndjson` | write a whole flight to `exports/` |
| `make replay FILE=f.ndjson` | replay that file as if it were happening now |
| `make replay-dry FILE=f.ndjson` | report what a replay would do |
| `make refresh` | rebuild the `observation_1min` rollup |
| `make psql` | psql shell on the running database |
| `make test` | generator, ingest and replayer tests |
| `make logs` | follow container logs |

Extra flags go through `ARGS`, for example `make demo ARGS="--duration-min 20 --seed 7"`.

---

## How data gets in

```
data/*.csv .txt .mat .xlsx  ──make ingest──────┐
                                               │
generator (balloon flight)  ──make demo────────┼──▶ PostgreSQL ──▶ Grafana
                            ──make export──┐   │                   auto-refresh 5 s
                                           │   │
exports/*.ndjson            ──make replay──┴───┤
                                               │
synthetic supplement        ──make supplement──┘
```

Four writers, one contract, one sink. A row loaded from a file, generated live, or replayed
from disk takes the identical path into the database, which is why a panel cannot tell them
apart — and why every generated mission is labelled `kind = 'synthetic'` so a human can.

---

## Layout

```
.
├── README.md                  this file
├── EXPLAINME.md               what the data is, and what is real
├── CLAUDE.md                  working rules for this repository
├── docs/data-model.md         model, decisions, roadmap, acceptance criteria
├── docker-compose.yml
├── db/
│   ├── migrations/            numbered SQL, applied once each
│   ├── seed/                  catalog: device, sensor, metric
│   └── apply.sh               migration runner, called by make up
├── data/                      the provided measurements, read-only, gitignored
├── exports/                   NDJSON flights, gitignored
├── ingest/src/                one parser per provided format
├── generator/src/             flight profile, sensor models, contract, sinks
├── replayer/src/              NDJSON back into the database, at the file's pace
└── grafana/
    ├── provisioning/          datasource, dashboard provider, alert rules
    └── dashboards/            one JSON per dashboard
```

---

## Troubleshooting

**A panel is empty or flat.** That is a data-source bug, not a dashboard bug: check the
mission is loaded (*Pipeline health* → inventory) and that the dashboard's time range covers
the capture.

**A dashboard change did not appear.** Grafana rescans `grafana/dashboards/` every 10 s.
If the file has a UTF-8 BOM the parser rejects it silently and keeps serving the previous
version — PowerShell's `Set-Content -Encoding UTF8` adds one. Check
`docker compose logs grafana` for provisioning errors.

**Alert rules did not load.** They are read at startup only: `docker compose restart grafana`.
One bad file stops the whole alerting provisioner, so check the log for
`Failed to provision alerting`.

**Port already in use.** Change `GRAFANA_PORT` or `POSTGRES_PORT` in `.env` and `make up`.

**Start over.** `make reset` drops the volume and rebuilds; then `make ingest` and
`make supplement` again. Data is otherwise kept between runs.
