-- Sensors carried by balloon-01.
--
-- The four ids that appear in the data contract in docs/data-model.md
-- (bme280, mmc5983, icm42688, as3935) are kept verbatim so the generator can
-- emit the documented payload unchanged.

INSERT INTO sensor (device_id, sensor_id, model, manufacturer, mount_position, active_from) VALUES
    ('balloon-01', 'max_m10s',  'MAX-M10S',      'u-blox',          'mast_top',        '2026-08-01T00:00:00Z'),
    ('balloon-01', 'bme280',    'BME280',        'Bosch Sensortec', 'radiation_shield','2026-08-01T00:00:00Z'),
    ('balloon-01', 'mmc5983',   'MMC5983MA',     'MEMSIC',          'boom_tip',        '2026-08-01T00:00:00Z'),
    ('balloon-01', 'icm42688',  'ICM-42688-P',   'TDK InvenSense',  'payload_core',    '2026-08-01T00:00:00Z'),
    ('balloon-01', 'scd41',     'SCD41',         'Sensirion',       'intake_duct',     '2026-08-01T00:00:00Z'),
    ('balloon-01', 'sgp41',     'SGP41',         'Sensirion',       'intake_duct',     '2026-08-01T00:00:00Z'),
    ('balloon-01', 'mics4514',  'MiCS-4514',     'SGX Sensortech',  'intake_duct',     '2026-08-01T00:00:00Z'),
    ('balloon-01', 'ulpsm_o3',  'ULPSM-O3',      'SPEC Sensors',    'intake_duct',     '2026-08-01T00:00:00Z'),
    ('balloon-01', 'veml6075',  'VEML6075',      'Vishay',          'payload_top',     '2026-08-01T00:00:00Z'),
    ('balloon-01', 'as3935',    'AS3935',        'ams',             'boom_mid',        '2026-08-01T00:00:00Z'),

-- Real rigs. The MiCS-6814 is three separate sensing elements in one package,
-- so it is modelled as three sensors: each channel has its own resistance and
-- its own calibration, and collapsing them would make gas_sensor_resistance
-- ambiguous.
    ('arduino-uno-5v', 'mq4',           'MQ-4',       'Winsen',         'breadboard', '2026-05-01T00:00:00Z'),
    ('arduino-uno-5v', 'mics5524',      'MiCS-5524',  'SGX Sensortech', 'breadboard', '2026-05-01T00:00:00Z'),
    ('arduino-uno-5v', 'mics6814_co',   'MiCS-6814',  'SGX Sensortech', 'breadboard', '2026-05-01T00:00:00Z'),
    ('arduino-uno-5v', 'mics6814_nh3',  'MiCS-6814',  'SGX Sensortech', 'breadboard', '2026-05-01T00:00:00Z'),
    ('arduino-uno-5v', 'mics6814_no2',  'MiCS-6814',  'SGX Sensortech', 'breadboard', '2026-05-01T00:00:00Z'),

    ('sps30-logger',  'sps30',          'SPS30',      'Sensirion',      'inlet',      '2025-05-01T00:00:00Z'),

    ('akel-alcohol3', 'mics5524',       'MiCS-5524',  'SGX Sensortech', 'bench',      '2025-07-01T00:00:00Z'),
    ('akel-alcohol3', 'th_probe',       'unspecified temperature and humidity probe', NULL, 'bench', '2025-07-01T00:00:00Z'),

    ('akel-ch4',      'ch4_sensor',     'unspecified methane sensor', NULL,           'bench',      '2025-08-01T00:00:00Z'),
    ('akel-ch4',      'th_probe',       'unspecified temperature and humidity probe', NULL, 'bench', '2025-08-01T00:00:00Z'),

    ('rtk-base',      'gnss_receiver',  'unspecified u-blox receiver', 'u-blox', 'station',     '2025-10-01T00:00:00Z'),
    ('rtk-rover',     'gnss_receiver',  'unspecified u-blox receiver', 'u-blox', 'rover_pole',  '2025-10-01T00:00:00Z'),
    ('gnss-survey',   'gnss_receiver',  'unspecified u-blox receiver', 'u-blox', 'station',     '2026-04-01T00:00:00Z'),

-- Synthetic only. These part numbers are plausible choices for the job, but no
-- such sensor was ever fitted: nothing in data/ measured a magnetic vector, an
-- IMU waveform or a UV index (DEC-18).
    ('synthetic-platform', 'mmc5983',   'MMC5983MA (synthetic)',  'MEMSIC',         'boom_tip',    '2025-06-01T00:00:00Z'),
    ('synthetic-platform', 'icm42688',  'ICM-42688-P (synthetic)','TDK InvenSense', 'frame',       '2025-06-01T00:00:00Z'),
    ('synthetic-platform', 'veml6075',  'VEML6075 (synthetic)',   'Vishay',         'upward_face', '2025-06-01T00:00:00Z')
ON CONFLICT (device_id, sensor_id) DO UPDATE SET
    model          = EXCLUDED.model,
    manufacturer   = EXCLUDED.manufacturer,
    mount_position = EXCLUDED.mount_position,
    active_from    = EXCLUDED.active_from;
