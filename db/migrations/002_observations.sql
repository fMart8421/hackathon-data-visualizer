-- 002_observations.sql
-- Layer 2: observations. High volume, append-only.
--
-- Every table carries event_time and ingested_at (DEC-01), both UTC with
-- millisecond resolution, and a natural key that makes reinserting the same
-- batch a no-op (DEC-03).

-- --------------------------------------------------------------------------
-- position
-- --------------------------------------------------------------------------
-- Sole source of truth for where the platform was. Any lat/lon stored
-- elsewhere is a derived copy (DEC-05).

CREATE TABLE position (
    event_time        timestamptz(3) NOT NULL,
    ingested_at       timestamptz(3) NOT NULL DEFAULT now(),
    mission_id        text NOT NULL REFERENCES mission (mission_id) ON DELETE CASCADE,
    device_id         text NOT NULL REFERENCES device (device_id),
    latitude          double precision NOT NULL,
    longitude         double precision NOT NULL,
    altitude_m        double precision,
    speed_ms          double precision,
    heading_deg       double precision,
    vertical_speed_ms double precision,
    fix_quality       smallint,
    satellites        smallint,
    hdop              double precision,
    seq               bigint NOT NULL,
    PRIMARY KEY (mission_id, device_id, seq),
    CONSTRAINT position_latitude_range  CHECK (latitude  BETWEEN -90  AND 90),
    CONSTRAINT position_longitude_range CHECK (longitude BETWEEN -180 AND 180),
    CONSTRAINT position_heading_range   CHECK (heading_deg IS NULL OR heading_deg >= 0 AND heading_deg < 360)
);

CREATE INDEX position_mission_time_idx ON position (mission_id, event_time DESC);
CREATE INDEX position_time_idx         ON position (event_time DESC);

COMMENT ON TABLE  position IS 'GNSS stream. Requirement 1, and the geo source for the observation_geo view.';
COMMENT ON COLUMN position.seq IS 'Generator batch counter. Part of the natural key (DEC-03).';

-- --------------------------------------------------------------------------
-- observation
-- --------------------------------------------------------------------------
-- Narrow format (DEC-08): one row per (metric, instant), never one column per
-- quantity. Vector components share the row (DEC-06).

CREATE TABLE observation (
    event_time  timestamptz(3) NOT NULL,
    ingested_at timestamptz(3) NOT NULL DEFAULT now(),
    mission_id  text NOT NULL REFERENCES mission (mission_id) ON DELETE CASCADE,
    device_id   text NOT NULL,
    sensor_id   text NOT NULL,
    metric_key  text NOT NULL REFERENCES metric (metric_key),
    value       double precision,
    value_raw   double precision,
    vx          double precision,
    vy          double precision,
    vz          double precision,
    quality     observation_quality NOT NULL DEFAULT 'good',
    seq         bigint NOT NULL,
    PRIMARY KEY (mission_id, device_id, sensor_id, metric_key, seq),
    FOREIGN KEY (device_id, sensor_id) REFERENCES sensor (device_id, sensor_id),
    CONSTRAINT observation_has_a_reading
        CHECK (value IS NOT NULL OR vx IS NOT NULL OR vy IS NOT NULL OR vz IS NOT NULL)
);

CREATE INDEX observation_mission_metric_time_idx
    ON observation (mission_id, metric_key, event_time DESC);
CREATE INDEX observation_time_idx
    ON observation (event_time DESC);

COMMENT ON COLUMN observation.value     IS 'Calibrated value in metric.canonical_unit (DEC-05).';
COMMENT ON COLUMN observation.value_raw IS 'Raw reading, with simulated noise and drift (DEC-05).';
COMMENT ON COLUMN observation.vx        IS 'Vector component, only for metric.kind = vector.';

-- --------------------------------------------------------------------------
-- waveform_block
-- --------------------------------------------------------------------------
-- High-rate IMU stored one block per interval, never sample by sample
-- (DEC-07). Sample timing comes from first_sample_index and sample_rate_hz,
-- not from the clock (DEC-02).

CREATE TABLE waveform_block (
    block_start_time   timestamptz(3) NOT NULL,
    ingested_at        timestamptz(3) NOT NULL DEFAULT now(),
    mission_id         text NOT NULL REFERENCES mission (mission_id) ON DELETE CASCADE,
    device_id          text NOT NULL,
    sensor_id          text NOT NULL,
    metric_key         text NOT NULL REFERENCES metric (metric_key),
    sample_rate_hz     double precision NOT NULL,
    sample_count       integer NOT NULL,
    full_scale         double precision,
    samples_x          double precision[] NOT NULL,
    samples_y          double precision[] NOT NULL,
    samples_z          double precision[] NOT NULL,
    first_sample_index bigint NOT NULL,
    PRIMARY KEY (mission_id, device_id, sensor_id, metric_key, first_sample_index),
    FOREIGN KEY (device_id, sensor_id) REFERENCES sensor (device_id, sensor_id),
    CONSTRAINT waveform_rate_positive  CHECK (sample_rate_hz > 0),
    CONSTRAINT waveform_count_positive CHECK (sample_count > 0),
    CONSTRAINT waveform_axes_match_count CHECK (
        cardinality(samples_x) = sample_count AND
        cardinality(samples_y) = sample_count AND
        cardinality(samples_z) = sample_count
    )
);

CREATE INDEX waveform_block_mission_metric_time_idx
    ON waveform_block (mission_id, metric_key, block_start_time DESC);

COMMENT ON COLUMN waveform_block.first_sample_index IS
    'Monotonic sample counter since mission start (DEC-02).';

-- --------------------------------------------------------------------------
-- event
-- --------------------------------------------------------------------------
-- Discrete occurrences. sensor_id is nullable because phase_change is emitted
-- by the platform, not by a sensor, which is why the natural key is a UNIQUE
-- constraint over nullable columns rather than the primary key.

CREATE TABLE event (
    event_id    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_time  timestamptz(3) NOT NULL,
    ingested_at timestamptz(3) NOT NULL DEFAULT now(),
    mission_id  text NOT NULL REFERENCES mission (mission_id) ON DELETE CASCADE,
    device_id   text NOT NULL REFERENCES device (device_id),
    sensor_id   text,
    event_type  text NOT NULL,
    payload     jsonb NOT NULL DEFAULT '{}'::jsonb,
    latitude    double precision,
    longitude   double precision,
    altitude_m  double precision,
    seq         bigint NOT NULL,
    CONSTRAINT event_natural_key
        UNIQUE NULLS NOT DISTINCT (mission_id, device_id, sensor_id, event_type, seq, event_time)
);

CREATE INDEX event_mission_time_idx ON event (mission_id, event_time DESC);
CREATE INDEX event_type_time_idx    ON event (event_type, event_time DESC);
CREATE INDEX event_payload_idx      ON event USING gin (payload);

COMMENT ON COLUMN event.event_type IS 'lightning_strike, threshold_alarm, phase_change.';
COMMENT ON COLUMN event.latitude   IS 'Derived copy of position at event_time (DEC-05).';

-- --------------------------------------------------------------------------
-- ingest_batch
-- --------------------------------------------------------------------------
-- One row per batch written by the generator or the replayer. Doubles as the
-- pipeline health panel: batch_time vs received_at shows end-to-end lag.

CREATE TABLE ingest_batch (
    batch_id     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    mission_id   text NOT NULL REFERENCES mission (mission_id) ON DELETE CASCADE,
    device_id    text NOT NULL REFERENCES device (device_id),
    source       text NOT NULL,
    seq          bigint NOT NULL,
    batch_time   timestamptz(3) NOT NULL,
    received_at  timestamptz(3) NOT NULL DEFAULT now(),
    record_count integer NOT NULL DEFAULT 0,
    status       text NOT NULL DEFAULT 'ok',
    CONSTRAINT ingest_batch_natural_key UNIQUE (mission_id, device_id, source, seq),
    CONSTRAINT ingest_batch_status_known CHECK (status IN ('ok', 'partial', 'error')),
    CONSTRAINT ingest_batch_count_positive CHECK (record_count >= 0)
);

CREATE INDEX ingest_batch_received_idx ON ingest_batch (received_at DESC);

COMMENT ON COLUMN ingest_batch.source IS 'direct, replay, or export. Which path wrote the batch.';
