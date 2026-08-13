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
    ('balloon-01', 'as3935',    'AS3935',        'ams',             'boom_mid',        '2026-08-01T00:00:00Z')
ON CONFLICT (device_id, sensor_id) DO UPDATE SET
    model          = EXCLUDED.model,
    manufacturer   = EXCLUDED.manufacturer,
    mount_position = EXCLUDED.mount_position,
    active_from    = EXCLUDED.active_from;
