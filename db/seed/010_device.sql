-- Platform. One device for now (OPEN-05); the schema carries device_id
-- everywhere, so a second balloon is another row here plus a dashboard
-- variable, never a migration.

-- The rigs that produced the measurements in data/ (DEC-17), plus balloon-01,
-- which is kept for the synthetic missions of phase 6.

INSERT INTO device (device_id, model, firmware_version) VALUES
    ('balloon-01', 'HAB-1 stratospheric payload', '1.4.2'),
    ('arduino-uno-5v',
     'Arduino Uno 5V suite: Methane Click MQ-4, Alcohol 3 Click MiCS-5524, Air Quality 5 Click MiCS-6814',
     NULL),
    ('sps30-logger',
     'Sensirion SPS30 particulate logger, carried on foot, bicycle and in vehicles',
     NULL),
    ('akel-alcohol3',
     'Akel bench rig: Alcohol 3 gas sensor with a temperature and humidity probe',
     NULL),
    ('akel-ch4',
     'Akel bench rig: methane sensor with a temperature and humidity probe. See OPEN-12',
     NULL),
    ('rtk-base',
     'RTK base station at Taveiro, receiver 1 of the paired captures',
     NULL),
    ('rtk-rover',
     'RTK rover at Taveiro, receiver 2, about 19 m from the base',
     NULL),
    ('gnss-survey',
     'u-blox receiver logging UBX-NAV-SVIN survey-in records',
     NULL),
    ('synthetic-platform',
     'SYNTHETIC. Not a real device. Carries the channels nothing measured: magnetometer, IMU, UV (DEC-18)',
     NULL)
ON CONFLICT (device_id) DO UPDATE SET
    model            = EXCLUDED.model,
    firmware_version = EXCLUDED.firmware_version;
