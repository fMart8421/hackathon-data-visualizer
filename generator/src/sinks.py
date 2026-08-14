"""Where batches go.

Phase 2 implements the direct PostgreSQL sink. The NDJSON sink and the
replayer are phase 6; the interface is here so adding them changes this file
and nothing else.

Every insert carries ON CONFLICT DO NOTHING against the natural keys from
DEC-03, so re-running a mission, or restarting mid-flight, never duplicates a
row. ingested_at is left to the database default: it must record when the row
actually landed, not when the generator thought it did.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Protocol

import psycopg

from contract import Batch

INSERT_POSITION = """
INSERT INTO position (
    event_time, mission_id, device_id, latitude, longitude, altitude_m,
    speed_ms, heading_deg, vertical_speed_ms, fix_quality, satellites, hdop, seq
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT DO NOTHING
"""

INSERT_OBSERVATION = """
INSERT INTO observation (
    event_time, mission_id, device_id, sensor_id, metric_key,
    value, value_raw, vx, vy, vz, quality, seq
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT DO NOTHING
"""

INSERT_BATCH = """
INSERT INTO ingest_batch (
    mission_id, device_id, source, seq, batch_time, record_count, status
) VALUES (%s, %s, %s, %s, %s, %s, %s)
ON CONFLICT DO NOTHING
"""


class Sink(Protocol):
    def open_mission(
        self, mission_id: str, name: str, started_at: datetime, description: str, kind: str = "measured"
    ) -> None: ...
    def write(self, batch: Batch) -> None: ...
    def close_mission(self, mission_id: str, ended_at: datetime) -> None: ...
    def close(self) -> None: ...


class PostgresSink:
    """Direct writes, one transaction per batch.

    A transaction per batch keeps what the dashboard sees consistent: a panel
    never catches a fix without its observations.
    """

    source = "direct"

    def __init__(self, dsn: str, time_scale: float = 1.0) -> None:
        """time_scale converts flight time to wall clock: 1/speed.

        t_offset_ms in the contract is an offset in *flight* time, the real gap
        between reading two registers. When the flight is accelerated the
        offsets have to compress with everything else, or a batch's readings
        land after the next batch's fix.
        """
        self.connection = psycopg.connect(dsn, autocommit=False)
        self.time_scale = time_scale
        self.rows_written = 0

    def open_mission(
        self,
        mission_id: str,
        name: str,
        started_at: datetime,
        description: str,
        kind: str = "measured",
    ) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO mission (mission_id, name, started_at, description, kind)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (mission_id) DO NOTHING
                """,
                (mission_id, name, started_at, description, kind),
            )
        self.connection.commit()

    def _position_row(self, batch: Batch) -> tuple:
        position = batch.position
        return (
            batch.batch_time,
            batch.mission_id,
            batch.device_id,
            position.lat,
            position.lon,
            position.alt_m,
            position.speed_ms,
            position.heading_deg % 360.0 if position.heading_deg is not None else None,
            position.vertical_speed_ms,
            position.fix_quality,
            position.satellites,
            position.hdop,
            batch.seq,
        )

    def _observation_rows(self, batch: Batch) -> list[tuple]:
        return [
            (
                batch.batch_time + timedelta(milliseconds=o.t_offset_ms * self.time_scale),
                batch.mission_id,
                batch.device_id,
                o.sensor_id,
                o.metric_key,
                o.value,
                o.value_raw,
                o.vx,
                o.vy,
                o.vz,
                o.quality,
                batch.seq,
            )
            for o in batch.observations
        ]

    def write(self, batch: Batch) -> None:
        with self.connection.cursor() as cursor:
            if batch.position is not None:
                cursor.execute(INSERT_POSITION, self._position_row(batch))
            if batch.observations:
                cursor.executemany(INSERT_OBSERVATION, self._observation_rows(batch))
            cursor.execute(
                INSERT_BATCH,
                (
                    batch.mission_id,
                    batch.device_id,
                    self.source,
                    batch.seq,
                    batch.batch_time,
                    batch.record_count,
                    "ok",
                ),
            )
        self.connection.commit()
        self.rows_written += batch.record_count

    def write_many(self, batches: list[Batch], source: str, batch_seq: int) -> int:
        """Write a whole file in one transaction, as a single ingest_batch.

        The generator's unit of work is one instant; a file loader's is one
        file. Recording an ingest_batch per row would bury the pipeline health
        panel under hundreds of thousands of entries that all say the same
        thing.
        """
        if not batches:
            return 0

        positions = [self._position_row(b) for b in batches if b.position is not None]
        observations = [row for b in batches for row in self._observation_rows(b)]

        # Count what the database actually accepted, not what was offered. With
        # ON CONFLICT DO NOTHING the two differ on every re-run, and a loader
        # that reports the rows it sent would claim to have written a million
        # rows while inserting none.
        inserted = 0
        with self.connection.cursor() as cursor:
            if positions:
                cursor.executemany(INSERT_POSITION, positions)
                inserted += max(cursor.rowcount, 0)
            if observations:
                cursor.executemany(INSERT_OBSERVATION, observations)
                inserted += max(cursor.rowcount, 0)
            cursor.execute(
                INSERT_BATCH,
                (
                    batches[0].mission_id,
                    batches[0].device_id,
                    source,
                    batch_seq,
                    batches[0].batch_time,
                    inserted,
                    "ok",
                ),
            )
        self.connection.commit()
        self.rows_written += inserted
        return inserted

    def close_mission(self, mission_id: str, ended_at: datetime) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                "UPDATE mission SET ended_at = %s WHERE mission_id = %s",
                (ended_at, mission_id),
            )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()
