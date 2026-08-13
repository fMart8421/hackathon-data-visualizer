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

    ('no2_concentration',  'scalar',   'ppb',    'conppb',
     0,       500,     NULL,   100,   'none',
     'Nitrogen dioxide. Peaks in the urban boundary layer below 2 km.'),

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
     'Lightning detection. Payload carries strike energy and estimated distance.')

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
