-- 004_mission_kind.sql
-- DEC-18: generated data must never be mistakable for measured data.
--
-- Real measurements arrived in data/ on 2026-08-13, so the database now holds
-- both. Without a label on the mission, the only way to tell them apart would
-- be to recognise mission ids by eye, which is exactly the kind of thing that
-- goes wrong in front of an audience.

CREATE TYPE mission_kind AS ENUM ('measured', 'synthetic', 'mixed');

ALTER TABLE mission
    ADD COLUMN kind mission_kind NOT NULL DEFAULT 'measured';

COMMENT ON COLUMN mission.kind IS
    'measured: ingested from data/. synthetic: produced by the generator. '
    'mixed: a measured session extended with generated channels (DEC-18).';

-- Everything already in the table came from the phase 2 balloon generator.
UPDATE mission SET kind = 'synthetic' WHERE mission_id LIKE 'flight-%';

CREATE INDEX mission_kind_idx ON mission (kind);
