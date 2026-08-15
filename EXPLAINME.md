# What this repository actually is

A telemetry archive with a live front end. Sensor readings from an instrumented mobile
platform go into PostgreSQL in one shape, and Grafana reads them back as maps, series,
waveforms and gauges that update while you watch.

Most of what it holds was genuinely measured. A small, clearly labelled part was generated,
because four of the nine things the dashboards must show were never measured at all. Telling
those two apart, permanently and unmistakably, is one of the design constraints of the whole
project.

For running it, see [README.md](README.md). For the reasoning behind every decision,
[docs/data-model.md](docs/data-model.md). This file is the middle layer: what the data is,
where it came from, and what is real.

---

## The problem it solves

A platform carrying GNSS receivers, gas sensors, a particle counter, an IMU, a magnetometer
and a UV sensor produces readings that have nothing in common except that they were taken
somewhere at some time. The brief asked for nine things: coordinates on a map, volatiles by
composition and place, the geomagnetic vector, accelerometer and gyroscope waveforms, a UV
index gauge, weather over a map, a flexible dashboard for any single value, a flexible
dashboard for any waveform, and all of it served live from a database.

The tempting shape is one table per sensor and one dashboard per table. That collapses the
first time somebody adds a sensor. The shape here instead is:

- **A catalog** that says what quantities exist (`metric`), what units they are in, what
  range is plausible, and what counts as a warning. Roughly thirty rows.
- **A narrow observation table** holding `(when, where from, which metric, value)` — never
  one column per quantity. Adding a gas sensor adds catalog rows, not columns.
- **Derived views** that are pure cache: minute rollups, waveform envelopes, and a join of
  each observation to the position it was taken at.

Dashboards read the catalog to build their own variables, so a new quantity appears as a new
panel with no JSON edited anywhere. That is requirements 7 and 8, and it is why the narrow
format is worth its slight awkwardness in SQL.

Two timestamps travel with every row: `event_time`, when the platform measured, and
`ingested_at`, when the row reached the database. The gap between them is the pipeline's
own latency, which is what the health dashboard draws — about 1 ms, worst case 30 ms.

---

## The data

Everything under `data/` was provided as real captures. 663 MB, spread across a year of
independent sessions, in eight formats.

| Source | Volume | What it holds | Timestamps |
|---|---|---|---|
| `Volatiles/` | 58 CSV | MQ-4 methane, MiCS-5524 CO and ethanol, MiCS-6814 CO/NH₃/NO₂, each as raw ADC, sensor resistance and calibrated ppm | real, May 2026 |
| `Particles/` | 17 TXT | SPS30 particle counter: PM1.0, PM2.5, PM4.0, PM10, number concentrations, typical particle size | none, sample order only |
| `Particles/*_Coord.txt` | 3 of those | the same, plus a coordinate per block — three bike runs around Taveiro | none |
| `Alcohol/`, `CH4/` | 8 TXT | Arduino serial logs: ADC, volts, kΩ, ppm, air temperature, relative humidity | none |
| `GNSSprecision/` | 80 MAT | u-blox survey-in: ECEF position, mean accuracy, observation count | GPS time of week |
| `GNSSresRTK/` | 18,703 MAT | RTK sessions as NMEA tables (GGA, GLL, GSA, GSV, RMC, VTG) | real, Nov 2025 |
| `GNSSsatellites/` | 2 XLSX | broadcast ephemeris and ~100k raw observables for GPS, Galileo, GLONASS, SBAS | real, Sep 2025 |

Loaded, that is **1.61 million observations and 131,000 GNSS fixes across 47 measured
missions**. One mission per capture session, because that is what these files are: separate
sessions, not one campaign.

### Three properties that shaped everything

**There is no common clock.** Particles were captured in June 2025, GNSS between September
and December 2025, gases in May 2026. No moment exists where two sensors measured together,
so cross-sensor joins at a common instant are impossible and no dashboard pretends
otherwise. Each dashboard pins the time range of the capture it shows.

**Position exists for very little of it.** Only the three geo-referenced bike runs pair a
measurement with a coordinate. The GNSS files carry position but no chemistry; the bench
rigs carry chemistry but no position. This is why requirement 6, weather over a map, is a
time series here instead: the rigs that measured temperature and humidity had no GPS, and
inventing a coordinate for them would be inventing a measurement.

**Half the sources have no timestamps.** The particle and Arduino logs record sample order
only. They are ingested at an assumed 1 Hz anchored on the capture date in the filename, and
every affected mission says so in its description: the time axis is sample order, not
measured time.

### What the real data does not contain

Nothing measures the geomagnetic vector, nothing measures acceleration or angular rate, and
nothing measures UV. Barometric pressure is absent too. That is requirements 3, 4, 5 and 8
with no data behind them at all.

---

## Measured against generated

The four gaps are filled by a generator, under rules strict enough that the fill can never
be mistaken for a measurement.

**Every generated capture is labelled in the database.** `mission.kind` is `measured` or
`synthetic`, indexed, and set at write time — not inferred from a naming convention
afterwards. The three dashboards fed by generated data say "(synthetic)" in their title and
carry a banner. The metric explorer has a `$kind` variable that defaults to `measured`,
because before it existed the explorer was quietly averaging a generated balloon's −56 °C
into the same series as real bench temperatures.

**Generated data obeys the rules the real data set.** It uses metric keys already in the
catalog, the same units, the same cadences, and it produces a raw and a calibrated value the
way the real loggers do. A panel behaves identically whichever fed it, which is exactly what
makes the flexible dashboards flexible.

**Where a quantity has a real anchor, the generator uses it rather than inventing one:**

| Generated channel | Anchored to |
|---|---|
| Magnetic field | the actual field at Taveiro — 44.5 µT, 55° inclination |
| Platform attitude | a route that was genuinely ridden, taken from a measured bike run |
| Gyroscope yaw rate | that same route's own curvature |
| UV index | the real solar elevation for that site and date |
| Accelerometer | gravity on the vertical axis; envelope RMS comes out at 9.839 |

Each channel carries the error its real part would have: a hard-iron offset on the
magnetometer, a dark current on the UV photodiode that keeps the raw trace above zero all
night. That is what makes `value_raw` and `value` differ for generated data the way they
differ in the real logs.

**The synthetic side is small and knowable:** four missions, 63,000 observations and
946,200 IMU samples, against 1.61 million measured observations. The largest one,
`synthetic-taveiro-2025-06-28`, opens its description with "SYNTHETIC. Not measured."

There is also a generated stratospheric balloon flight. It predates the real data — the
project began with nothing but a generator — and it survives because it is the only thing
that produces a fast-moving live stream to demonstrate requirement 9. It is labelled
synthetic like everything else generated.

### How to check for yourself

```sql
SELECT kind, count(*) AS missions, sum(observations) AS observations
FROM mission_summary GROUP BY kind;

SELECT mission_id, kind, left(description, 60)
FROM mission WHERE kind = 'synthetic';
```

The same split is the first thing on the Pipeline health dashboard, measured in green and
synthetic in orange.

---

## How a reading gets in, and back out

```
                    ┌── ingest    reads data/, one parser per format
   PostgreSQL  ◀────┼── generator writes a live flight as it happens
                    ├── replayer  reads an NDJSON export at its own pace
   Grafana    ◀─────┴── supplement writes the four unmeasured channels
```

All four write through the same contract and the same sink. A file-fed row, a generated row
and a replayed row are the same shape by construction — the difference is recorded in
`ingest_batch.source` and in `mission.kind`, never in the shape of the data.

Writes are idempotent through a natural key per table, so re-running any loader inserts
nothing the second time. That is what makes it safe to widen the ingest and run it again, or
to restart a replay that died halfway.

Readings that fall outside a metric's plausible range are stored with `quality = 'suspect'`
rather than dropped — 129,336 of them, almost all from an uncalibrated MiCS-6814 CO channel.
Discarding them would hide a real property of the instrument; hiding the flag would let a
broken channel look like a measurement.

---

## What it does not do

- **No pressure anywhere in the measured data**, so requirement 6 is temperature and
  humidity only. It is not faked.
- **No weather on a map**, for the same reason: no GPS on the rigs that measured it.
- **No cross-sensor correlation**, because no two sensors ever ran at the same time.
- **The `event` table is empty.** Nothing in the provided data records a discrete event, and
  the generator does not manufacture lightning strikes.
- **RTK is a precision demonstration, not a track.** Every RTK capture is a static
  occupation: the rover sits still and holds 1.9 cm against 3.2 m without corrections. The
  only real route in the project is the particle bike runs.
- **Alerts never leave the machine.** Rules evaluate and colour the dashboard; the
  notification policy is muted around the clock, because a demo laptop has no mail server and
  a contact point that cannot deliver is just noise in the log.
