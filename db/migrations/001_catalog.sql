-- 001_catalog.sql
-- Layer 1: catalog. Low volume, describes what everything else means.
-- Adding a sensor or a quantity must be an INSERT here, never a schema change.

-- --------------------------------------------------------------------------
-- Enumerations
-- --------------------------------------------------------------------------

CREATE TYPE metric_kind AS ENUM ('scalar', 'vector', 'event', 'waveform');

-- 'none' for quantities without an orientation. Required to be body/ned when
-- kind = 'vector' (DEC-06).
CREATE TYPE metric_frame AS ENUM ('body', 'ned', 'none');

CREATE TYPE observation_quality AS ENUM ('good', 'suspect', 'bad', 'missing');

-- --------------------------------------------------------------------------
-- mission
-- --------------------------------------------------------------------------

CREATE TABLE mission (
    mission_id   text        PRIMARY KEY,
    name         text        NOT NULL,
    started_at   timestamptz(3) NOT NULL,
    ended_at     timestamptz(3),
    description  text,
    CONSTRAINT mission_time_order CHECK (ended_at IS NULL OR ended_at >= started_at)
);

COMMENT ON TABLE  mission IS 'Campaign or flight. Top-level dashboard variable.';
COMMENT ON COLUMN mission.ended_at IS 'NULL while the flight is ongoing.';

-- --------------------------------------------------------------------------
-- device
-- --------------------------------------------------------------------------

CREATE TABLE device (
    device_id        text PRIMARY KEY,
    model            text NOT NULL,
    firmware_version text
);

COMMENT ON TABLE device IS 'Physical platform. Participates in multiple missions.';

-- --------------------------------------------------------------------------
-- sensor
-- --------------------------------------------------------------------------
-- Composite key: sensor ids like "bme280" are only unique within a device, so
-- a second balloon can carry the same part number (OPEN-05).

CREATE TABLE sensor (
    device_id      text NOT NULL REFERENCES device (device_id) ON DELETE CASCADE,
    sensor_id      text NOT NULL,
    model          text NOT NULL,
    manufacturer   text,
    mount_position text,
    active_from    timestamptz(3),
    active_to      timestamptz(3),
    PRIMARY KEY (device_id, sensor_id),
    CONSTRAINT sensor_active_order CHECK (active_to IS NULL OR active_from IS NULL OR active_to >= active_from)
);

COMMENT ON TABLE sensor IS 'Acquisition unit mounted on a device.';

-- --------------------------------------------------------------------------
-- metric
-- --------------------------------------------------------------------------
-- Source of truth for the quantities the platform produces. Dashboard
-- variables query this table; panel queries never hardcode a metric list
-- (DEC-08, requirements 7 and 8).

CREATE TABLE metric (
    metric_key     text         PRIMARY KEY,
    kind           metric_kind  NOT NULL,
    canonical_unit text         NOT NULL,
    display_unit   text,
    valid_min      double precision,
    valid_max      double precision,
    warn_low       double precision,
    warn_high      double precision,
    frame          metric_frame NOT NULL DEFAULT 'none',
    description    text,
    CONSTRAINT metric_vector_needs_frame
        CHECK (kind <> 'vector' OR frame <> 'none'),
    CONSTRAINT metric_valid_range
        CHECK (valid_min IS NULL OR valid_max IS NULL OR valid_min < valid_max)
);

COMMENT ON COLUMN metric.canonical_unit IS
    'Storage unit, SI where practical (DEC-04). Never converted on write.';
COMMENT ON COLUMN metric.display_unit IS
    'Grafana unit id (celsius, pressurehpa, conppm, ...), applied at panel level.';
COMMENT ON COLUMN metric.valid_min IS 'Physically plausible floor, for panel axes and quality checks.';
COMMENT ON COLUMN metric.warn_low IS 'Alert threshold, consumed in phase 6 (OPEN-06).';

-- calibration is deliberately absent: marked optional in docs/data-model.md and
-- only worth adding to demonstrate retroactive recalculation.
