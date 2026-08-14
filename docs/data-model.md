# Multi-sensor telemetry platform: data model and project bootstrap

**Status:** stack decisions locked, implementation not started
**Last revised:** 2026-08-12
**Scope:** logical model, architecture, data generator spec, and bootstrap plan.

## How to use this file

Reference context for Claude Code. When generating schema, migrations, generator, replayer, or dashboards, follow the decisions in "Closed decisions" and treat the entities in "Logical model" as the source of truth for names and semantics.

Working rules:

- Don't invent fields outside the catalog without adding them here first.
- Decisions marked OPEN are unresolved. If a task depends on one, ask instead of assuming.
- When a decision changes, edit the entry and log the change in the changelog at the end.
- Entity, field, file, and code names in English, snake_case. Documentation in English.
- Don't introduce new dependencies without justification. The stack is locked in DEC-09.

## Challenge context

Instrumented mobile platform (GNSS, IMU, magnetometer, gas sensors, UV, weather, lightning detection) producing telemetry for storage and web visualization.

Brief requirements:

1. GNSS coordinates on a map.
2. Volatiles: composition, concentration, location, and time.
3. Geomagnetic field vector on a map.
4. Accelerometer and gyroscope waveforms, per component.
5. UV index as a bar or level gauge, 0 to 12 scale.
6. Temperature, humidity, and pressure over overlaid geographic maps.
7. Flexible dashboard for individual values.
8. Flexible dashboard for dynamic waveform representation.
9. Reads from a database, presented online.

Constraints: dynamically updated data, web interface, flexible user interaction.

**Evaluation focuses on visualization and dynamism, not stack sophistication.** Infrastructure choices should favor simplicity and demo reliability.

## Starting point

**Revised 2026-08-13: real measurements were provided, in `data/`.** The original premise, that no data existed and everything would be generated, no longer holds. See DEC-17, DEC-18, and the reopened OPEN-02.

### Provided data

| Dataset | Files | Format | Quantities | Timestamps |
|---|---|---|---|---|
| `Volatiles/` | 58 CSV, 3.4 MB | wide CSV, 1 Hz, ~600 rows each | MQ-4 CH4, MiCS-5524 CO and ethanol, MiCS-6814 CO, NH3, NO2. Each as raw ADC, sensor resistance, and calibrated ppm | real, 2026-05-18 |
| `Particles/` | 17 TXT, 7 MB | repeating 15-line blocks | SPS30: PM1.0, PM2.5, PM4.0, PM10, number concentration 0.5 to 10, typical particle size | none |
| `Particles/*_Coord.txt` | 3 of the above | same, plus a `Coordinates:` line per block | PM with latitude and longitude, bike runs around Taveiro | none |
| `Alcohol/` | 6 TXT, 6.2 MB | Arduino serial log | ADC, volts, kOhm, ethanol ppm, air temperature, relative humidity | none |
| `CH4/` | 2 TXT, 0.4 MB | same | same columns; the logger header wrongly says "ALCOHOL 3" | none |
| `GNSSprecision/` | 80 MAT, 25 MB | MATLAB v5 numeric arrays | ECEF Xpos/Ypos/Zpos, iTOW, MeanAcc, Obs, Dur. About 100k samples per file | GPS time of week |
| `GNSSresRTK/` | 18703 MAT, 611 MB | MATLAB MCOS objects | NMEA tables: GGA, GLL, GSA, GSV, RMC, VTG | real, but not readable outside MATLAB |
| `GNSSsatellites/` | 2 XLSX, 10 MB | multi-sheet workbooks | broadcast ephemeris and about 100k rows of raw observables for GPS, Galileo, GLONASS, SBAS | real, 2025-09-09 |

### What the provided data does not contain

Nothing covers the geomagnetic vector (requirement 3), accelerometer or gyroscope waveforms (requirements 4 and 8), or UV index (requirement 5). Barometric pressure is absent too, so requirement 6 can only be served with temperature and humidity.

### Three properties that constrain the design

1. **There is no common clock.** These are independent bench and field sessions spread across a year: particles in June 2025, GNSS from September to December 2025, gases in May 2026. A campaign where one platform measured everything at once does not exist in this data, so cross-sensor joins at a common instant are impossible. Each capture session becomes its own `mission`.
2. **Position exists for very little of it.** Only the three `Particles/*_Coord.txt` bike runs pair a measurement with a coordinate. The GNSS datasets carry position but no chemistry. `observation_geo` therefore returns null coordinates for most observations, which is correct rather than broken.
3. **Half the sources have no timestamps at all.** The particle and Arduino serial logs record sample order only. See OPEN-10 for how a time base is synthesised.

## Architecture

```
generator (Python)  -->  PostgreSQL  -->  Grafana
   |                       ^              |
   +--> NDJSON file -------+              +--> browser, auto-refresh 1-5 s
        (optional, replay)
```

Two operating modes:

- **Direct mode:** the generator writes to PostgreSQL in real time, at clock pace, with a configurable acceleration factor. Used for the demo.
- **File mode:** the generator produces an NDJSON of a complete flight, and a separate replayer inserts it at the pace of the original timestamps. Lets you replay the exact same flight across demos.

No MQTT, no message queue, no intermediate API service. These would be defensible in a real system with intermittent connectivity, but here they just add pieces that can fail during evaluation. Grafana reads PostgreSQL directly via SQL.

## Guiding principle

Separate the logical model from the implementation. Hypertables, indexes, materialized views, and the dashboard JSON are all projections of the model. If the model is right, switching database engine or visualization tool means rewriting adapters, not rethinking the domain.

## Logical model

Three layers: catalog (metadata, low volume), observations (facts, high volume), derived (cache, recomputable).

### Layer 1: catalog

Adding a new sensor should mean inserting rows in this layer. Never changing schema or editing panels.

#### `mission`

Campaign or flight. Primary filter for most queries and top-level variable in dashboards.

| Field | Type | Notes |
|---|---|---|
| `mission_id` | id | PK |
| `name` | text | |
| `started_at` / `ended_at` | timestamptz | `ended_at` null while ongoing |
| `description` | text | |

#### `device`

Physical platform. Participates in multiple missions.

| Field | Type | Notes |
|---|---|---|
| `device_id` | id | PK |
| `model` | text | |
| `firmware_version` | text | |

#### `sensor`

Acquisition unit mounted on a `device`.

| Field | Type | Notes |
|---|---|---|
| `sensor_id` | id | PK is `(device_id, sensor_id)`: a part number like `bme280` is only unique within a platform, and a second balloon can carry the same part (OPEN-05) |
| `device_id` | id | FK |
| `model` / `manufacturer` | text | |
| `mount_position` | text | |
| `active_from` / `active_to` | timestamptz | |

#### `metric`

Catalog of quantities. Most important entity in the model. Dashboard variables are fed by queries to this table, which satisfies requirements 7 and 8 without hardcoded panel lists.

| Field | Type | Notes |
|---|---|---|
| `metric_key` | text | canonical PK, e.g. `air_temperature`, `co2_concentration`, `mag_field` |
| `kind` | enum | `scalar`, `vector`, `event`, `waveform` |
| `canonical_unit` | text | storage unit, SI |
| `display_unit` | text | display unit |
| `valid_min` / `valid_max` | number | physically plausible range |
| `warn_low` / `warn_high` | number | alert thresholds |
| `frame` | enum | `body`, `ned`, `none`. Required for `kind = vector` |
| `description` | text | |

#### `calibration`

Coefficients per sensor/metric pair, with time validity. **Optional at this stage.** Only implement if there's time to spare, or to demonstrate retroactive recalculation.

### Layer 2: observations

#### `position`

Spatial reference stream. Sole source of truth for where the platform was.

| Field | Type | Notes |
|---|---|---|
| `event_time` | timestamptz ms | |
| `ingested_at` | timestamptz ms | |
| `mission_id` / `device_id` | id | |
| `latitude` / `longitude` | degree, WGS84 | |
| `altitude_m` | meter | |
| `speed_ms` / `heading_deg` / `vertical_speed_ms` | number | |
| `fix_quality` / `satellites` / `hdop` | | fix quality, always stored |
| `seq` | integer | generator counter |

#### `observation`

Low-cadence scalars and vectors: temperature, humidity, pressure, gases, UV index, magnetic field. Narrow format.

| Field | Type | Notes |
|---|---|---|
| `event_time` | timestamptz ms | |
| `ingested_at` | timestamptz ms | |
| `mission_id` / `device_id` / `sensor_id` | id | |
| `metric_key` | text | FK to `metric` |
| `value` | number | calibrated value, canonical unit |
| `value_raw` | number | simulated raw reading, with noise |
| `vx` / `vy` / `vz` | number | only for `kind = vector`. For `mag_field`, `value` holds the magnitude |
| `quality` | enum | `good`, `suspect`, `bad`, `missing`. Defaults to `good` |
| `seq` | integer | |

#### `waveform_block`

Accelerometer and gyroscope in blocks. One record covers an interval, typically 1 s.

| Field | Type | Notes |
|---|---|---|
| `block_start_time` | timestamptz ms | |
| `ingested_at` | timestamptz ms | |
| `mission_id` / `device_id` / `sensor_id` | id | |
| `metric_key` | text | `acceleration`, `angular_rate` |
| `sample_rate_hz` | number | |
| `sample_count` | integer | |
| `full_scale` | number | |
| `samples_x` / `samples_y` / `samples_z` | number array | |
| `first_sample_index` | integer | monotonic counter |

#### `event`

Discrete occurrences: lightning strikes, alarms, flight phase transitions.

| Field | Type | Notes |
|---|---|---|
| `event_time` | timestamptz ms | |
| `ingested_at` | timestamptz ms | |
| `mission_id` / `device_id` / `sensor_id` | id | |
| `event_type` | text | `lightning_strike`, `threshold_alarm`, `phase_change` |
| `payload` | JSONB | type-specific attributes, e.g. energy, estimated distance |
| `latitude` / `longitude` / `altitude_m` | | derived copy, see DEC-05 |

#### `ingest_batch`

Record of each inserted batch: source, timestamp, count, status. Kept even with a local generator, since it doubles as a pipeline health panel in the dashboard.

| Field | Type | Notes |
|---|---|---|
| `batch_id` | id | surrogate PK |
| `mission_id` / `device_id` | id | |
| `source` | text | `direct`, `replay`, or `export` |
| `seq` | integer | generator batch counter |
| `batch_time` | timestamptz ms | batch timestamp at the source |
| `received_at` | timestamptz ms | arrival, the `ingested_at` of this table |
| `record_count` | integer | rows written by the batch |
| `status` | text | `ok`, `partial`, `error` |

The gap between `batch_time` and `received_at` is the end-to-end lag panel.

### Layer 3: derived

No new semantics. Recomputable cache.

- `observation_1min`: aggregates (min, max, average, count) for long views.
- `waveform_envelope`: per-second envelope to preview waveforms without loading every sample.
- `observation_geo`: view joining each observation to the interpolated position at the same instant, so map panels don't do the join at query time. Panels for requirements 2, 3, and 6 depend on this view.

Implemented in phase 1 as plain views, not materialized: while the generator writes continuously a view is always current and needs no refresh job. Materializing them is a phase 6 performance decision, to be logged here if it happens, on the same terms as OPEN-08.

`observation_geo` resolves position as the nearest GNSS fix within 5 s rather than by linear interpolation. At 1 Hz position cadence the two are indistinguishable on a map, and if a panel ever needs true interpolation it becomes a change to this view alone.

## Closed decisions

### DEC-01: two timestamps per record

`event_time` (moment of measurement) and `ingested_at` (arrival in the database). Everything in UTC with millisecond resolution. Conversion to local time only at display. With the generator running accelerated, the gap between the two becomes visible and useful for debugging.

### DEC-02: monotonic counter for waveform

Each block stores `first_sample_index`. The time position of each sample is computed from the index and the sample rate, not from the clock.

### DEC-03: idempotency via natural key

Natural key `(device_id, sensor_id, seq)`. Reinserting the same batch doesn't duplicate records. Lets the replayer restart mid-stream without wiping the database.

Refined in phase 1, since `(device_id, sensor_id, seq)` alone is not unique in practice. `mission_id` is included everywhere so a second flight can be replayed without a `make reset` (relevant to OPEN-04), and the discriminator inside a batch is added per table:

| Table | Natural key |
|---|---|
| `position` | `(mission_id, device_id, seq)`, no sensor involved |
| `observation` | `(mission_id, device_id, sensor_id, metric_key, seq)`, one sensor emits several metrics per batch |
| `waveform_block` | `(mission_id, device_id, sensor_id, metric_key, first_sample_index)`, per DEC-02 |
| `event` | `(mission_id, device_id, sensor_id, event_type, seq, event_time)` as a `UNIQUE NULLS NOT DISTINCT` constraint, plus a surrogate `event_id`. `sensor_id` is null for `phase_change`, which is emitted by the platform and not by a sensor, and a nullable column cannot sit in a primary key |
| `ingest_batch` | `(mission_id, device_id, source, seq)` |

### DEC-04: canonical unit in storage

One unit per metric, SI, fixed in `metric.canonical_unit`. Conversion to display unit lives in the catalog and is applied in Grafana panel units.

Convention fixed when seeding the catalog in phase 1: `canonical_unit` is the physical unit actually stored, SI where SI stays readable, and `display_unit` holds the Grafana unit id a panel applies. So pressure is stored in `Pa` and displayed with `pressurepa`, never stored in hPa. Three quantities deliberately depart from strict SI because the alternative makes every panel and every debug query harder to read: `air_temperature` in `degC` rather than kelvin, `mag_field` in `uT` rather than tesla, gases in `ppm` and `ppb`. The rule that matters is that storage never converts.

### DEC-05: raw and calibrated, both

`value_raw` stores the reading with simulated noise and drift. `value` stores it after compensation. The generator produces both, giving material to demonstrate filtering and correction in panels.

Latitude and longitude copied into `observation` and `event` are a derived copy from `position`. The source of truth is always `position`.

### DEC-06: vector components in the same row

`vx`, `vy`, `vz` in one row. The three components are measured simultaneously and queried together. The frame is explicit in `metric.frame`.

### DEC-07: waveform in blocks

One record per interval with per-axis arrays. Six channels at 200 Hz is 4 million rows per hour in per-sample format, versus 3600 records in this format.

### DEC-08: narrow format for observations

`metric_key` plus `value`, instead of one column per quantity. Basis for requirements 7 and 8.

### DEC-09: stack locked

PostgreSQL 16, Grafana OSS, generator in Python 3.12, all orchestrated by Docker Compose. TimescaleDB extension added only if waveform performance requires it, and that decision gets logged here when it happens. No message broker, no custom HTTP backend, no ORM.

### DEC-10: dashboards as code

Dashboard JSON lives in the repo and loads via Grafana provisioning at startup. No dashboard exists only on someone's instance. Changes made in the UI get exported and committed.

### DEC-11: the generator is the data source

**Superseded on 2026-08-13 by DEC-17.** Kept for the record: when no file had been provided, the generator produced a complete, physically plausible flight and was responsible for ensuring all nine requirements had data with visible variation.

The part that still holds: a flat-line panel is a data-source defect, not a dashboard defect.

### DEC-12: reference scenario

**Retired on 2026-08-13.** The stratospheric balloon flight was chosen when the data was ours to invent, because it correlated every required quantity and gave the demo a dramatic moment. Real measurements make it a fiction competing with the actual campaigns, so it stops being the organising scenario.

The phase 2 implementation is not deleted. Under DEC-18 the flight profile becomes one synthetic scenario among the real sessions, and it must obey the same catalog and conventions as measured data.

### DEC-13: migrations applied by a script, not by the image entrypoint

`db/apply.sh` runs inside the database container and is what `make up` and `make migrate` call. Migrations in `db/migrations/` are applied once each and tracked in `schema_migrations`; the seed in `db/seed/` is written to be idempotent and re-applied on every run, so a catalog edit reaches a running database without `make reset`.

PostgreSQL's `docker-entrypoint-initdb.d` was rejected because it only fires on an empty volume: the same command would behave differently on a fresh machine and on the demo machine, which is exactly the failure you don't want minutes before a presentation.

### DEC-14: Grafana connects with a read-only role

`db/apply.sh` creates the role in `GRAFANA_DB_USER` with `SELECT` only, and `ALTER DEFAULT PRIVILEGES` covers tables added by later migrations. Grafana reads the flight and cannot write to it, so a mistyped panel query fails instead of mutating data mid-demo. Credentials reach the datasource through environment variables in `docker-compose.yml`, never committed.

### DEC-15: the generator runs as a Compose one-off

`make generate`, `make demo`, and `make test` are `docker compose run --rm generator` against an image built from `generator/Dockerfile`. No Python is needed on the host, and the demo machine gets the same interpreter as the development machine. The service sits behind a Compose profile so `make up` never starts it, and the source is bind-mounted so a code change needs no rebuild.

Three dependencies are added, all required rather than chosen: `psycopg[binary]`, because DEC-09 locks PostgreSQL with no ORM and something has to speak the wire protocol; `pytest` for `make test`; and `scipy`, added in phase 4 because `data/GNSSprecision` holds MATLAB v5 `.mat` files and `scipy.io.loadmat` is the only maintained reader for that format. The ingest shares this image, so the declaration lives in one place. Nothing else.

### DEC-16: accelerated flight time is real time

In direct mode the balloon is flying *now*, only faster. `event_time` is wall-clock time and the acceleration factor compresses the flight into it, so at 60x the nominal 150 minute flight fills 2.5 minutes of the dashboard's axis. That is what makes requirement 9's live refresh worth watching; backdating the flight across a 150 minute span would fill a window that is already in the past.

`ingested_at` is left to the database default, so the DEC-01 gap between the two is genuine pipeline lag rather than an artefact of the simulation. Measured at 60x with one transaction per batch: about 1 ms average, 22 ms worst case.

`t_offset_ms` in the contract is an offset in *flight* time, the real gap between reading two registers. The sink scales it by 1/speed, or a batch's readings would land after the next batch's fix.

### DEC-17: measured data is the source of truth

Everything in `data/` is ingested and is what the dashboards show by default. The generator is demoted from *the* source to *a* source, kept for the four requirements the measurements cannot serve at all: the geomagnetic vector, the two IMU waveforms, and UV index.

Consequence for the catalog: metrics are added for what was actually measured (`ch4_concentration`, `co_concentration`, `nh3_concentration`, `pm2_5`, `particle_number_concentration`, and so on). The metrics with no measured backing stay in the catalog and are populated only by synthetic missions, which DEC-18 requires to be labelled.

### DEC-18: synthetic data obeys the rules of the real data

A synthetic mission is not free to invent. It must use metric keys already in the catalog, with the units, cadences, and plausible ranges that the ingested data establishes, and it must produce `value_raw` and `value` the same way the real loggers do: a raw ADC or resistance reading alongside a calibrated figure. Where a quantity has real measurements, the generator's parameters are derived from those measurements rather than chosen by hand.

Two reasons. A panel must look the same whether it is fed by a file or by the generator, or the flexible dashboards in requirements 7 and 8 stop being flexible. And nobody, including us, should be able to mistake generated data for measured data when reading the database, which is why `mission.kind` is added as `measured`, `synthetic`, or `mixed` in migration `004`.

### DEC-19: ingest is curated by default and scalable by configuration

The first load takes all of `Volatiles/`, `Particles/` including the three geo-referenced runs, `Alcohol/`, `CH4/`, the satellites workbook, and a few `GNSSprecision/` files. That is a few hundred thousand rows: quick to load, quick to query, and enough to serve every requirement the data can serve.

The loader is driven by file patterns rather than a hardcoded list, and is idempotent through the DEC-03 natural keys, so widening to all 80 `GNSSprecision` files, or to the whole RTK export, is a configuration change and a re-run rather than new code.

## Generator specification

### Flight profile

Nominal duration 150 minutes, accelerable. Four phases, emitted as `phase_change` events:

| Phase | Duration | Altitude | Notes |
|---|---|---|---|
| `ascent` | 0 to 90 min | 0 to 30000 m | climbs at roughly 5 m/s, with variation |
| `burst` | instantaneous | 30000 m | single event, vibration spike |
| `descent` | 90 to 140 min | 30000 to 1000 m | fast fall that slows with air density |
| `landing` | 140 to 150 min | 1000 to 0 m | final drift |

Trajectory starting in Ovar, drifting east with wind that rotates and strengthens with altitude. This produces a curved map track instead of a straight line.

### Quantities and expected behavior

| Metric | Cadence | Behavior |
|---|---|---|
| `air_temperature` | 1 Hz | drops to about -55 C at the 11 km tropopause, rises in the stratosphere, visible inversion |
| `relative_humidity` | 1 Hz | high in clouds below 3 km, near zero above 10 km |
| `pressure` | 1 Hz | exponential decay, 1013 hPa to about 10 hPa |
| `co2_concentration` | 0.2 Hz | about 420 ppm at surface, decreasing with altitude |
| `no2_concentration` | 0.2 Hz | peak in the urban boundary layer below 2 km |
| `o3_concentration` | 0.2 Hz | maximum in the ozone layer, 20 to 30 km |
| `voc_concentration` | 0.2 Hz | noisy, random spikes near the surface |
| `uv_index` | 0.2 Hz | from 4 at surface, saturating above 12 at altitude |
| `mag_field` | 1 Hz | vector of about 45 microtesla, component rotation from vehicle attitude |
| `acceleration` | 100 Hz | low noise during ascent, strong spike at burst, oscillation during descent |
| `angular_rate` | 100 Hz | slow rotation during ascent, chaotic tumble after burst |
| `lightning_strike` | sporadic | two to four events while passing through convective clouds |

All quantities get gaussian noise and slow drift, so `value_raw` is distinguishable from `value`.

### Runtime parameters

Acceleration factor, random seed, duration, starting point, output mode (database or NDJSON), and end-of-flight behavior (stop or restart). A fixed seed keeps the demo reproducible.

Implemented in `generator/src/cli.py`:

| Flag | Default | Notes |
|---|---|---|
| `--speed` | `1.0` | acceleration factor. `make demo` passes 60 |
| `--seed` | `20260812` | fixed, so the demo flight is reproducible |
| `--duration-min` | `150` | scales the phase boundaries, not the 30 km ceiling: a much shorter flight climbs implausibly fast |
| `--start-lat` / `--start-lon` | Ovar | launch site |
| `--mission-id` | `flight-<UTC timestamp>` | a fresh mission per run, so re-running always produces visibly new data |
| `--device-id` | `balloon-01` | must exist in the `device` seed |
| `--dsn` | `$TELEMETRY_DSN` | |
| `--sink` | `postgres` | NDJSON lands in phase 6 |
| `--on-finish` | `stop` | `restart` flies again under a new mission id |
| `--quiet` | off | suppress the per-sample progress lines |

## Data contract

Batch structure produced by the generator, used in file mode and as the internal form in direct mode.

```json
{
  "device_id": "balloon-01",
  "mission_id": "flight-2026-08-12",
  "seq": 1042,
  "batch_time": "2026-08-12T10:23:45.120Z",
  "position": {
    "lat": 40.86, "lon": -8.62, "alt_m": 12400.0,
    "speed_ms": 18.4, "heading_deg": 87.0, "vertical_speed_ms": 5.1,
    "fix_quality": 4, "satellites": 11, "hdop": 0.9
  },
  "observations": [
    { "sensor_id": "bme280", "metric_key": "air_temperature",
      "value": -52.1, "value_raw": -52.34, "t_offset_ms": 0 },
    { "sensor_id": "mmc5983", "metric_key": "mag_field",
      "vx": 21.4, "vy": -3.2, "vz": 43.8, "t_offset_ms": 20 }
  ],
  "waveforms": [
    { "sensor_id": "icm42688", "metric_key": "acceleration",
      "sample_rate_hz": 100, "first_sample_index": 208400, "full_scale": 16.0,
      "samples_x": [], "samples_y": [], "samples_z": [] }
  ],
  "events": [
    { "sensor_id": "as3935", "event_type": "lightning_strike",
      "t_offset_ms": 430, "payload": { "energy": 128000, "distance_km": 14 } }
  ]
}
```

`t_offset_ms` is the offset from `batch_time`. Shrinks the payload and avoids repeating the full timestamp on every line.

## Reference layout

Visual organization prototype for the dashboards, produced in Claude Design: https://claude.ai/design/p/aebf1c35-a6ce-4706-9433-5a3653237537?file=Telemetry+Dashboard.dc.html&via=share

Basis for panel arrangement in phase 5: layout, grouping, and hierarchy between the overview and detail views. It doesn't replace the decision to use Grafana or prescribe any specific panel; actual panel configuration follows the acceptance criteria table above.

## Repository layout

```
.
├── CLAUDE.md                  points to this file
├── docs/
│   └── data-model.md          this file
├── docker-compose.yml
├── db/
│   ├── migrations/            numbered SQL, applied in order
│   └── seed/                  initial catalog: metric, device, sensor
├── generator/
│   ├── pyproject.toml
│   ├── src/
│   │   ├── flight.py          flight profile, phases, trajectory
│   │   ├── sensors.py         per-metric models, noise, drift
│   │   ├── contract.py        batch serialization
│   │   ├── sinks.py           writes to PostgreSQL and NDJSON
│   │   └── cli.py             arguments and main loop
│   └── tests/
├── replayer/
│   └── src/replay.py          reads NDJSON, inserts at timestamp pace
└── grafana/
    ├── provisioning/
    │   ├── datasources/       PostgreSQL configured via environment variables
    │   └── dashboards/        points at the dashboard JSON directory
    └── dashboards/            one JSON per dashboard, versioned
```

## Environment and commands

Targets to expose in a `Makefile`:

| Command | Effect |
|---|---|
| `make up` | start PostgreSQL and Grafana, apply migrations and seed |
| `make down` | stop everything, keep the volume |
| `make reset` | drop the volume and recreate from scratch |
| `make generate` | run the generator in direct mode, real time |
| `make demo` | run the generator at 60x acceleration, for demonstration |
| `make export FILE=flight.ndjson` | generate a full flight to file |
| `make replay FILE=flight.ndjson` | replay a file into the database |
| `make test` | generator tests |

Grafana at `localhost:3000`, PostgreSQL at `localhost:5432`. Credentials in `.env`, with `.env.example` versioned.

## Roadmap

Execution order. Each phase should leave the system functional.

**Phase 1, foundations.** Docker Compose with PostgreSQL and Grafana. Catalog and stream migrations. Seed with the metrics table. Criterion: `make up` starts and Grafana connects to PostgreSQL.

**Phase 2, minimal generator.** Altitude profile, trajectory, and three scalars (temperature, pressure, humidity). Direct write to PostgreSQL. Criterion: a dashboard shows three series advancing.

Phases 1 and 2 are done. Phases 3 onward were rewritten on 2026-08-13, when real data arrived.

**Phase 3, ingest layer. Done.** New `ingest/` component: one parser per provided format, writing through the existing `contract` and `PostgresSink`. Covers `Volatiles/`, `Particles/` including the three `_Coord` runs, `Alcohol/`, and `CH4/`. Catalog grows by roughly twenty metric rows; migration `004` adds `mission.kind` for DEC-18. One mission per capture session, `ingest_batch.source` becomes `file:<name>`. Criterion: every chemistry file in `data/` is queryable, and the geo-referenced bike runs return coordinates from `observation_geo`.

**Phase 4, GNSS. Done.** `GNSSprecision` ECEF converted to WGS84 on ingest. The RTK CSV export produced by `ingest/matlab/export_rtk.m` loaded as positions. Satellites workbook loaded for a skyplot. Criterion: requirement 1 shows a real track from real receivers.

**Phase 5, dashboards for measured data.** One panel per requirement the measurements can serve: 1, 2, 6 in its temperature and humidity form, 7, and 9. Variables fed by the catalog, automatic provisioning, layout following the Claude Design reference. Criterion: those rows of the acceptance table are green.

**Phase 6, synthetic supplement.** The generator reworked under DEC-18 to extend the real campaigns rather than fly a balloon: geomagnetic vector, accelerometer and gyroscope blocks, UV index, all labelled `kind = synthetic`. Waveform envelope view. Criterion: requirements 3, 4, 5, and 8 have panels, and no panel can be mistaken for measured data.

**Phase 7, polish.** File mode and replayer, aggregates, alerts, pipeline health panel, README with demo instructions.

## Acceptance criteria

Revised 2026-08-13 for real data. The source column says what feeds the panel: `measured` rows must never be served by the generator.

| Req. | Source | Panel | Check |
|---|---|---|---|
| 1 | measured | Geomap of the RTK base and rover, plus the survey-in convergence series | Every capture in the provided data is a static occupation, so this is a precision panel, not a track: the RTK-fixed rover holds 1.9 cm against 3.2 m standalone, and survey accuracy converges from 0.117 m to 0.013 m. The one real route in the project is the geo-referenced particle runs under requirement 2 |
| 2 | measured | Gas species selector plus linked time series, and PM on the map from the `_Coord` bike runs | species selector updates chart; the bike runs colour a real route by concentration |
| 3 | synthetic | Geomap with rotated markers | arrows change direction; panel labelled as synthetic |
| 4 | synthetic | Acceleration and angular rate series, 3 components each | transient identifiable; panel labelled as synthetic |
| 5 | synthetic | UV index bar gauge, 0 to 12 | coloured thresholds; panel labelled as synthetic |
| 6 | measured | Temperature and humidity layers, from the Alcohol and CH4 logs | toggleable layers, visible gradient. Pressure is absent from the provided data and is not faked |
| 7 | either | Metric variable with repeated panel | adding a row to `metric` produces a new panel without editing JSON |
| 8 | synthetic | Channel and axis variable for waveforms | axis choice changes the panel |
| 9 | either | PostgreSQL datasource, auto-refresh on | data changes without reloading the page |

## Open questions

| ID | Question | Impact | Status |
|---|---|---|---|
| OPEN-03 | IMU cadence in the scenario: is 100 Hz enough or does it need more? | Sizes `waveform_block` and panel performance | decide in phase 4, start at 100 Hz |
| OPEN-04 | Data retention between demos | Determines whether `make reset` runs before each presentation | to decide |
| OPEN-05 | Single balloon or several at once? | Several turn `device_id` into a dashboard variable and enrich the demo | phase 1 seeds one device, `balloon-01`. Schema and natural keys already carry `device_id`, so a second platform is a seed row plus a variable. Revisit before the demo |
| OPEN-06 | Are alerts in scope? | Slide 126 lists "warnings". If so, an `alert_rule` entity and status panels are missing | to decide, phase 6 |
| OPEN-08 | Is TimescaleDB needed? | Only if waveform panels turn out slow | re-evaluate in phase 6, now that waveforms are synthetic only |
| OPEN-09 | RTK export from MATLAB | 18703 files hold the best GNSS data but are MCOS objects Python cannot read | **Closed in phase 4.** `RTK_25Nov` and `RTK_27Nov` exported and ingested, five captures per session. Widening means raising `maxFilesPerSession` and re-running; the ingest is idempotent |
| OPEN-13 | Capture date for `RTK_BaseRover` | Its 312910 rows carry seconds-of-day and nothing else: no date in the filename, none in the struct, and the export drops the MATLAB header. Anchoring it would mean inventing a date, so it is not ingested | ask whoever ran the capture; it adds a third fix quality (DGPS) to the precision comparison |
| OPEN-10 | Time base for sources with no timestamps | `Particles/`, `Alcohol/` and `CH4/` record sample order only | **Closed in phase 3.** 1 Hz assumed, from the capture date in the filename. `--rate-hz` overrides it, and every affected `mission.description` states the assumption and that the axis is not measured time |
| OPEN-11 | Is synthetic supplementation acceptable to the evaluators? | Requirements 3, 4, 5 and 8 have no measured data. If generated data is not acceptable, four requirements go unanswered and phase 6 should be dropped rather than built | ask the organisers |
| OPEN-12 | Which quantity does `CH4/` actually hold? | The logger header says "ALCOHOL 3" and labels the column `Etanol [ppm]`, but the folder and filename say CH4 | **Closed in phase 3:** filed as `ch4_concentration`, trusting the folder and filename over a header that looks copy-pasted from the alcohol logger. The contradiction is recorded in the description of both `ch4-*` missions. Revisit if whoever ran the capture says otherwise |

## Closed questions

| ID | Original question | Resolution |
|---|---|---|
| OPEN-01 | Grafana or from-scratch UI? | Grafana approved. See DEC-09 and DEC-10. |
| OPEN-02 | Real or simulated data? | ~~No data provided. Custom generator.~~ **Reopened and re-closed on 2026-08-13:** real measurements were provided in `data/`. Measured data is the source of truth (DEC-17); generated data supplements only what was not measured, under the rules in DEC-18. |
| OPEN-07 | Database engine | PostgreSQL 16. See DEC-09. |

## Changelog

| Date | Change |
|---|---|
| 2026-08-11 | Initial version. Three-layer model, DEC-01 to DEC-08, seven open questions. |
| 2026-08-12 | Grafana approved, stack locked (DEC-09, DEC-10). Confirmed no data was provided: generator becomes the data source (DEC-11) with a stratospheric balloon scenario (DEC-12). Added architecture, generator spec, repository layout, commands, six-phase roadmap, and acceptance criteria. Closed OPEN-01, OPEN-02, OPEN-07. |
| 2026-08-12 | Added reference layout link (Claude Design prototype) as the basis for phase 5 panel organization. |
| 2026-08-12 | Translated both project files (CLAUDE.md, data-model.md) from Portuguese to English for token efficiency. |
| 2026-08-14 | Phase 4 implemented. `ingest/src/gnss.py` loads the MATLAB RTK export and the u-blox survey-in captures: 271116 rows across 24 GNSS missions, 140005 positions in total. ECEF converted to WGS84 with Bowring's closed form, validated externally by landing within 2 m of the RTK base's independently measured coordinate. Base and rover are separate devices. NMEA's 99.99 DOP sentinel becomes null, and survey records with no fix yet are skipped rather than placed at the centre of the Earth. Added five metrics: `satellites_in_use`, `position_accuracy`, and the three GST error components. Added `scipy` under DEC-15. Closed OPEN-09, opened OPEN-13. Confirmed across every capture that nothing moves: the RTK rover holds 1.9 cm of scatter under a fixed solution against 3.2 m without corrections, and the 12-day survey converges from 0.117 m to 0.013 m. GSA and GSV carry no timestamp in the export, so per-epoch DOP and satellites-in-view cannot be series; satellite count comes from GGA. |
| 2026-08-14 | Phase 3 implemented. `ingest/src` parses all four chemistry formats into the generator's contract and sink, so a file-fed row and a generated row take the same path into the database. Migration `004` adds `mission.kind`. Catalog grew to 27 metrics; `no2_concentration` moved from ppb to ppm to match the MiCS-6814 and the other measured gases. Loaded 1,486,972 observations and 18,463 positions across 23 measured missions in 40 s, idempotent on re-run. Readings outside a metric's plausible range are stored with `quality = 'suspect'` rather than dropped: 129,336 of them, almost all the uncalibrated MiCS-6814 CO channel. The three `_Coord` bike runs supersede their identical plain twins, which are skipped. Closed OPEN-10 and OPEN-12. |
| 2026-08-13 | **Real data provided, premise revised.** Inventoried `data/`: 58 volatiles CSVs, 17 particle logs including 3 geo-referenced bike runs, 8 Arduino gas logs, 80 readable GNSS precision files, 18703 unreadable RTK MCOS files, and 2 satellite workbooks. Superseded DEC-11, retired DEC-12, reopened and re-closed OPEN-02. Added DEC-17 (measured data is the source of truth), DEC-18 (synthetic data obeys the rules of the real data, and `mission.kind` labels it), DEC-19 (curated ingest, scalable by configuration). Rewrote phases 3 to 7 and the acceptance table, which now names measured against synthetic per requirement. Added OPEN-09 to OPEN-12. Schema survives unchanged apart from `mission.kind`: the narrow format and the raw-plus-calibrated pair fit the real loggers directly. |
| 2026-08-13 | Phase 2 implemented. Generator in `generator/src` (flight profile with ISA atmosphere, parachute descent integrated against air density, wind-driven curved track, and the three BME280 scalars with raw and calibrated output), direct PostgreSQL sink, `flight-live` dashboard, 36 tests. Added DEC-15 (generator as a Compose one-off, and the psycopg and pytest dependencies) and DEC-16 (accelerated flight time is real time). Documented the implemented runtime flags. Phase change events stay in phase 3 with the rest of the event work. |
| 2026-08-12 | Phase 1 implemented. Compose with PostgreSQL 16 and Grafana OSS 12.0.2, migrations `001_catalog` / `002_observations` / `003_derived`, catalog seed, datasource and dashboard provisioning. Added DEC-13 (migration runner) and DEC-14 (read-only Grafana role). Refined DEC-03 with per-table natural keys, fixed the unit convention under DEC-04, specified the `ingest_batch` fields and the `quality` enum, made `(device_id, sensor_id)` the sensor key, and recorded that the derived layer ships as plain views with nearest-fix geo resolution. OPEN-05 answered for now with a single device. `calibration` stays unimplemented, as the model marks it optional. |
