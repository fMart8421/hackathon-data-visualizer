-- The metric catalog: every quantity in the "Quantities and expected
-- behavior" table of docs/data-model.md, and nothing else.
--
-- This table is what dashboard variables read (requirements 7 and 8), so a new
-- quantity is a row here plus generator support. No panel edits, no migration.
--
-- Unit convention:
--   canonical_unit  what is physically stored, SI where it stays readable
--                   (Pa, not hPa; degC rather than K; uT rather than T)
--   display_unit    the Grafana unit id a panel applies to that value

INSERT INTO metric (metric_key, kind, canonical_unit, display_unit,
                    valid_min, valid_max, warn_low, warn_high, frame, description) VALUES

-- Atmosphere, 1 Hz -----------------------------------------------------------
    ('air_temperature',    'scalar',   'degC',   'celsius',
     -90,     60,      -80,    50,    'none',
     'Outside air temperature. Falls to about -55 C at the tropopause, rises again in the stratosphere.'),

    ('relative_humidity',  'scalar',   '%',      'humidity',
     0,       100,     NULL,   NULL,  'none',
     'Relative humidity. High inside cloud below 3 km, near zero above 10 km.'),

    ('pressure',           'scalar',   'Pa',     'pressurepa',
     100,     110000,  NULL,   NULL,  'none',
     'Barometric pressure. Exponential decay from about 101325 Pa to about 1000 Pa at ceiling.'),

-- Gases, 0.2 Hz --------------------------------------------------------------
    ('co2_concentration',  'scalar',   'ppm',    'conppm',
     0,       5000,    NULL,   1000,  'none',
     'Carbon dioxide. About 420 ppm at surface, decreasing with altitude.'),

-- Changed from ppb to ppm on 2026-08-13: the MiCS-6814 reports ppm, and every
-- other measured gas is in ppm. One unit per metric, no conversion on write
-- (DEC-04).
    ('no2_concentration',  'scalar',   'ppm',    'conppm',
     0,       50,      NULL,   5,     'none',
     'Nitrogen dioxide, MiCS-6814 oxidising channel.'),

    ('o3_concentration',   'scalar',   'ppb',    'conppb',
     0,       15000,   NULL,   200,   'none',
     'Ozone. Maximum in the ozone layer between 20 and 30 km.'),

    ('voc_concentration',  'scalar',   'ppb',    'conppb',
     0,       60000,   NULL,   2000,  'none',
     'Total volatile organic compounds. Noisy, with random spikes near the surface.'),

-- Radiation, 0.2 Hz ----------------------------------------------------------
    ('uv_index',           'scalar',   'index',  'none',
     0,       20,      NULL,   11,    'none',
     'UV index. About 4 at surface, saturating past 12 at altitude. Requirement 5 renders it on a 0 to 12 gauge.'),

-- Geomagnetic vector, 1 Hz ---------------------------------------------------
    ('mag_field',          'vector',   'uT',     'none',
     0,       100,     NULL,   NULL,  'body',
     'Geomagnetic field vector, about 45 uT total. Components rotate with vehicle attitude. value holds the magnitude.'),

-- IMU waveforms, 100 Hz (OPEN-03) --------------------------------------------
    ('acceleration',       'waveform', 'm/s^2',  'accMS2',
     -160,    160,     NULL,   NULL,  'body',
     'Three-axis acceleration in blocks. Quiet during ascent, strong spike at burst, oscillation under parachute.'),

    ('angular_rate',       'waveform', 'deg/s',  'none',
     -2000,   2000,    NULL,   NULL,  'body',
     'Three-axis angular rate in blocks. Slow rotation during ascent, chaotic tumble after burst.'),

-- Discrete ------------------------------------------------------------------
    ('lightning_strike',   'event',    'count',  'none',
     NULL,    NULL,    NULL,   NULL,  'none',
     'Lightning detection. Payload carries strike energy and estimated distance.'),

-- Measured gases, from data/Volatiles, data/Alcohol and data/CH4 (DEC-17).
-- value_raw holds the ADC count the board actually read, value holds the ppm
-- the firmware derived from it (DEC-05).
    ('ch4_concentration',  'scalar',   'ppm',    'conppm',
     0,       10000,   NULL,   1000,  'none',
     'Methane, MQ-4. Needs 48 h burn-in for accuracy, per the logger banner.'),

    ('co_concentration',   'scalar',   'ppm',    'conppm',
     0,       1000,    NULL,   50,    'none',
     'Carbon monoxide. Measured by both the MiCS-5524 and the MiCS-6814 reducing channel; the two disagree because neither was calibrated against a clean-air reference.'),

    ('nh3_concentration',  'scalar',   'ppm',    'conppm',
     0,       500,     NULL,   25,    'none',
     'Ammonia, MiCS-6814 NH3 channel.'),

    ('ethanol_concentration', 'scalar', 'ppm',   'conppm',
     0,       1000,    NULL,   100,   'none',
     'Ethanol, MiCS-5524. The Alcohol 3 bench logs report this directly.'),

    ('gas_sensor_resistance', 'scalar', 'ohm',   'ohm',
     0,       20000000, NULL,  NULL,  'none',
     'Sensing element resistance. The physical quantity a metal-oxide sensor actually produces, before any ppm conversion.'),

-- Particulates, from data/Particles. The SPS30 logs are labelled ug/cm^3,
-- which is wrong by a factor of a million: the part reports ug/m3 for mass and
-- 1/cm3 for number concentration. Stored under the correct units.
    ('pm1_0',              'scalar',   'ug/m3',  'conugm3',
     0,       1000,    NULL,   50,    'none',
     'Particulate matter up to 1.0 um, SPS30.'),

    ('pm2_5',              'scalar',   'ug/m3',  'conugm3',
     0,       1000,    NULL,   25,    'none',
     'Particulate matter up to 2.5 um, SPS30. The fraction air quality limits are written against.'),

    ('pm4_0',              'scalar',   'ug/m3',  'conugm3',
     0,       1000,    NULL,   50,    'none',
     'Particulate matter up to 4.0 um, SPS30.'),

    ('pm10',               'scalar',   'ug/m3',  'conugm3',
     0,       1000,    NULL,   50,    'none',
     'Particulate matter up to 10 um, SPS30.'),

    ('nc0_5',              'scalar',   '1/cm3',  'none',
     0,       100000,  NULL,   NULL,  'none',
     'Number concentration of particles above 0.5 um, SPS30.'),

    ('nc1_0',              'scalar',   '1/cm3',  'none',
     0,       100000,  NULL,   NULL,  'none',
     'Number concentration of particles above 1.0 um, SPS30.'),

    ('nc2_5',              'scalar',   '1/cm3',  'none',
     0,       100000,  NULL,   NULL,  'none',
     'Number concentration of particles above 2.5 um, SPS30.'),

    ('nc4_0',              'scalar',   '1/cm3',  'none',
     0,       100000,  NULL,   NULL,  'none',
     'Number concentration of particles above 4.0 um, SPS30.'),

    ('nc10',               'scalar',   '1/cm3',  'none',
     0,       100000,  NULL,   NULL,  'none',
     'Number concentration of particles above 10 um, SPS30.'),

    ('typical_particle_size', 'scalar', 'um',    'lengthum',
     0,       20,      NULL,   NULL,  'none',
     'Typical particle size reported by the SPS30 alongside each measurement.'),

-- GNSS quality, from data/export (NMEA GGA and GST) and data/GNSSprecision
-- (u-blox survey-in). Position itself lives in the position table, not here;
-- these are the quantities that describe how good that position is.
    ('satellites_in_use',   'scalar',   'count',  'none',
     0,       60,      4,      NULL,  'none',
     'Satellites used in the navigation solution, from NMEA GGA.'),

    ('position_accuracy',   'scalar',   'm',      'lengthm',
     0,       1000,    NULL,   1.0,   'none',
     'u-blox survey-in mean accuracy. Converges from decimetres to millimetres as the survey runs.'),

    ('position_error_lat',  'scalar',   'm',      'lengthm',
     0,       100,     NULL,   1.0,   'none',
     'One-sigma latitude error, NMEA GST. Centimetre-level under an RTK fix, metre-level without one.'),

    ('position_error_lon',  'scalar',   'm',      'lengthm',
     0,       100,     NULL,   1.0,   'none',
     'One-sigma longitude error, NMEA GST.'),

    ('position_error_alt',  'scalar',   'm',      'lengthm',
     0,       100,     NULL,   2.0,   'none',
     'One-sigma altitude error, NMEA GST.')

ON CONFLICT (metric_key) DO UPDATE SET
    kind           = EXCLUDED.kind,
    canonical_unit = EXCLUDED.canonical_unit,
    display_unit   = EXCLUDED.display_unit,
    valid_min      = EXCLUDED.valid_min,
    valid_max      = EXCLUDED.valid_max,
    warn_low       = EXCLUDED.warn_low,
    warn_high      = EXCLUDED.warn_high,
    frame          = EXCLUDED.frame,
    description    = EXCLUDED.description;
