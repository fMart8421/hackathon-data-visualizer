-- Platform. One device for now (OPEN-05); the schema carries device_id
-- everywhere, so a second balloon is another row here plus a dashboard
-- variable, never a migration.

INSERT INTO device (device_id, model, firmware_version) VALUES
    ('balloon-01', 'HAB-1 stratospheric payload', '1.4.2')
ON CONFLICT (device_id) DO UPDATE SET
    model            = EXCLUDED.model,
    firmware_version = EXCLUDED.firmware_version;
