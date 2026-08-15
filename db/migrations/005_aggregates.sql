-- 005_aggregates.sql
-- Phase 7: make the derived layer earn its place.
--
-- observation_1min shipped in phase 1 as a plain view, on the reasoning that a
-- view is always current and needs no refresh job. Measured in phase 7 against
-- the real 1.66 M observations, that turned out to be backwards: the view was
-- *slower* than the base table it was supposed to accelerate.
--
--   long window off observation_1min (plain view)   442 ms
--   the same window straight off observation        181 ms
--   the same window off this materialised rollup      0.8 ms
--
-- The reason is that the time filter arrives on bucket_time, a value computed
-- by the view, so it cannot use the index on event_time; the plain view had to
-- aggregate the whole metric before filtering. date_trunc on a timestamptz is
-- STABLE rather than IMMUTABLE, so an expression index cannot fix it either.
--
-- Materialising costs 13 MB against an 821 MB base table and 665 ms for a full
-- CONCURRENTLY refresh, which is cheap enough to run at the end of every load.
--
-- The staleness rule that comes with it, and it matters:
--
--   live panels read the base tables.       observation, position,
--                                           waveform_block. Anything the
--                                           generator or the replayer is
--                                           writing right now.
--   observation_1min is the archive.        Long windows over data that has
--                                           finished arriving, refreshed when
--                                           a load finishes.
--
-- No dashboard queried observation_1min before this migration, so nothing
-- silently changes meaning underneath a panel.

-- IF EXISTS so the migration also applies to a database where the plain view
-- was already dropped by hand.
DROP VIEW IF EXISTS observation_1min;

CREATE MATERIALIZED VIEW observation_1min AS
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

-- REFRESH MATERIALIZED VIEW CONCURRENTLY requires a unique index, and
-- concurrency is the point: a demo must not block on a refresh. The columns
-- are the view's own grouping key, so uniqueness is guaranteed by
-- construction.
CREATE UNIQUE INDEX observation_1min_key_idx
    ON observation_1min (mission_id, device_id, sensor_id, metric_key, bucket_time);

CREATE INDEX observation_1min_metric_time_idx
    ON observation_1min (metric_key, bucket_time DESC);

COMMENT ON MATERIALIZED VIEW observation_1min IS
    'Minute buckets for long views over data that has finished arriving. '
    'Refreshed by make refresh, and at the end of make ingest / make supplement. '
    'Live panels read observation directly.';

-- --------------------------------------------------------------------------
-- mission_summary: one row per capture, measured or synthetic.
-- --------------------------------------------------------------------------
-- What the health dashboard needs to answer "what is actually in this
-- database", and what anyone reading the repo asks first. Kept a plain view,
-- not materialised: it must stay truthful about a mission being written right
-- now, and it costs 12 ms.
--
-- One grouped pass per table, joined once, rather than a LATERAL subquery per
-- mission. Written the correlated way first, it took 5.2 s: fifty missions
-- times four index scans over 1.66 M rows, on a panel that refreshes every
-- five seconds.

CREATE VIEW mission_summary AS
WITH observations AS (
    SELECT mission_id,
           count(*)                                  AS observations,
           count(DISTINCT metric_key)                AS metrics,
           min(event_time)                           AS first_observation,
           max(event_time)                           AS last_observation,
           count(*) FILTER (WHERE quality <> 'good') AS suspect
    FROM observation GROUP BY mission_id
),
positions AS (
    SELECT mission_id, count(*) AS positions FROM position GROUP BY mission_id
),
waveforms AS (
    SELECT mission_id, count(*) AS waveform_blocks, sum(sample_count) AS waveform_samples
    FROM waveform_block GROUP BY mission_id
),
batches AS (
    SELECT mission_id,
           count(*)                        AS batches,
           string_agg(DISTINCT source, ', ') AS sources,
           max(received_at)                AS last_received
    FROM ingest_batch GROUP BY mission_id
)
SELECT
    m.mission_id,
    m.name,
    m.kind,
    m.started_at,
    m.ended_at,
    coalesce(o.observations, 0)      AS observations,
    coalesce(o.metrics, 0)           AS metrics,
    o.first_observation,
    o.last_observation,
    coalesce(o.suspect, 0)           AS suspect,
    coalesce(p.positions, 0)         AS positions,
    coalesce(w.waveform_blocks, 0)   AS waveform_blocks,
    coalesce(w.waveform_samples, 0)  AS waveform_samples,
    coalesce(b.batches, 0)           AS batches,
    b.sources,
    b.last_received,
    m.description
FROM mission m
LEFT JOIN observations o ON o.mission_id = m.mission_id
LEFT JOIN positions    p ON p.mission_id = m.mission_id
LEFT JOIN waveforms    w ON w.mission_id = m.mission_id
LEFT JOIN batches      b ON b.mission_id = m.mission_id;

COMMENT ON VIEW mission_summary IS
    'One row per capture: what it holds, when, and whether it was measured or generated (DEC-18).';
