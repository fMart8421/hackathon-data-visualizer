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
    value, value_raw, vx, vy, vz, seq
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT DO NOTHING
"""

INSERT_BATCH = """
INSERT INTO ingest_batch (
    mission_id, device_id, source, seq, batch_time, record_count, status
) VALUES (%s, %s, %s, %s, %s, %s, %s)
ON CONFLICT DO NOTHING
"""


class Sink(Protocol):
    def open_mission(self, mission_id: str, name: str, started_at: datetime, description: str) -> None: ...
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

    def open_mission(self, mission_id: str, name: str, started_at: datetime, description: str) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO mission (mission_id, name, started_at, description)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (mission_id) DO NOTHING
                """,
                (mission_id, name, started_at, description),
            )
        self.connection.commit()

    def write(self, batch: Batch) -> None:
        position = batch.position
        with self.connection.cursor() as cursor:
            cursor.execute(
                INSERT_POSITION,
                (
                    batch.batch_time,
                    batch.mission_id,
                    batch.device_id,
                    position.lat,
                    position.lon,
                    position.alt_m,
                    position.speed_ms,
                    position.heading_deg % 360.0,
                    position.vertical_speed_ms,
                    position.fix_quality,
                    position.satellites,
                    position.hdop,
                    batch.seq,
                ),
            )
            if batch.observations:
                cursor.executemany(
                    INSERT_OBSERVATION,
                    [
                        (
                            batch.batch_time
                            + timedelta(milliseconds=observation.t_offset_ms * self.time_scale),
                            batch.mission_id,
                            batch.device_id,
                            observation.sensor_id,
                            observation.metric_key,
                            observation.value,
                            observation.value_raw,
                            observation.vx,
                            observation.vy,
                            observation.vz,
                            batch.seq,
                        )
                        for observation in batch.observations
                    ],
                )
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

    def close_mission(self, mission_id: str, ended_at: datetime) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                "UPDATE mission SET ended_at = %s WHERE mission_id = %s",
                (ended_at, mission_id),
            )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()
