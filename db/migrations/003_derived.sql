-- 003_derived.sql
-- Layer 3: derived. No new semantics, everything here is recomputable from
-- layer 2.
--
-- Implemented as plain views: with the generator writing continuously, a view
-- is always current and needs no refresh job. Materializing them is a
-- performance decision for phase 6, to be logged in docs/data-model.md if it
-- happens (same rule as OPEN-08 for TimescaleDB).

-- --------------------------------------------------------------------------
-- observation_1min: per-minute aggregates for long time windows.
-- --------------------------------------------------------------------------

CREATE VIEW observation_1min AS
SELECT
    date_trunc('minute', event_time) AS bucket_time,
    mission_id,
    device_id,
    sensor_id,
    metric_key,
    count(*)                        AS sample_count,
    min(value)                      AS value_min,
    max(value)                      AS value_max,
    avg(value)                      AS value_avg,
    min(value_raw)                  AS value_raw_min,
    max(value_raw)                  AS value_raw_max,
    avg(value_raw)                  AS value_raw_avg
FROM observation
GROUP BY 1, 2, 3, 4, 5;

COMMENT ON VIEW observation_1min IS
    'Minute buckets for long views. Recomputable cache, no new semantics.';

-- --------------------------------------------------------------------------
-- waveform_envelope: per-block min/max/rms per axis.
-- --------------------------------------------------------------------------
-- Lets a waveform panel show the shape of a long window without shipping every
-- sample to the browser. Blocks are one second, so this is the per-second
-- envelope.

CREATE VIEW waveform_envelope AS
SELECT
    b.block_start_time,
    b.mission_id,
    b.device_id,
    b.sensor_id,
    b.metric_key,
    b.sample_rate_hz,
    b.sample_count,
    b.first_sample_index,
    x.lo AS x_min, x.hi AS x_max, x.rms AS x_rms,
    y.lo AS y_min, y.hi AS y_max, y.rms AS y_rms,
    z.lo AS z_min, z.hi AS z_max, z.rms AS z_rms
FROM waveform_block b
CROSS JOIN LATERAL (
    SELECT min(s) AS lo, max(s) AS hi, sqrt(avg(s * s)) AS rms
    FROM unnest(b.samples_x) AS s
) AS x
CROSS JOIN LATERAL (
    SELECT min(s) AS lo, max(s) AS hi, sqrt(avg(s * s)) AS rms
    FROM unnest(b.samples_y) AS s
) AS y
CROSS JOIN LATERAL (
    SELECT min(s) AS lo, max(s) AS hi, sqrt(avg(s * s)) AS rms
    FROM unnest(b.samples_z) AS s
) AS z;

COMMENT ON VIEW waveform_envelope IS
    'Per-block envelope of the IMU arrays. Preview for requirement 8.';

-- --------------------------------------------------------------------------
-- observation_geo: each observation with the position it was taken at.
-- --------------------------------------------------------------------------
-- Requirements 2, 3 and 6 put non-positional quantities on a map, so the join
-- to position belongs here rather than in every panel query.
--
-- Nearest GNSS fix within +/- 5 s. At 1 Hz position cadence that is
-- indistinguishable from linear interpolation on a map; if a panel ever needs
-- true interpolation, it becomes a change to this view alone.

CREATE VIEW observation_geo AS
SELECT
    o.event_time,
    o.ingested_at,
    o.mission_id,
    o.device_id,
    o.sensor_id,
    o.metric_key,
    o.value,
    o.value_raw,
    o.vx,
    o.vy,
    o.vz,
    o.quality,
    o.seq,
    p.latitude,
    p.longitude,
    p.altitude_m
FROM observation o
LEFT JOIN LATERAL (
    SELECT pos.latitude, pos.longitude, pos.altitude_m
    FROM position pos
    WHERE pos.mission_id = o.mission_id
      AND pos.device_id  = o.device_id
      AND pos.event_time BETWEEN o.event_time - interval '5 seconds'
                             AND o.event_time + interval '5 seconds'
    ORDER BY abs(extract(epoch FROM pos.event_time - o.event_time))
    LIMIT 1
) AS p ON true;

COMMENT ON VIEW observation_geo IS
    'Observations joined to the nearest GNSS fix. Feeds the map panels.';
