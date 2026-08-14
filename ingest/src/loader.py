"""Turn parsed Samples into contract Batches and write them.

The ingest deliberately reuses the generator's contract and PostgresSink: a
panel must not be able to tell whether a row arrived from a file or from the
generator (DEC-18), and the surest way to guarantee that is to have both take
the same path into the database.

Idempotency comes from the DEC-03 natural keys. seq is the sample index within
the mission, which is deterministic given a stable file order, so re-running an
ingest inserts nothing the second time rather than duplicating.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from contract import Batch, ObservationRecord, PositionFix
from sources import Sample, Source

CHUNK_SIZE = 2000


@dataclass
class MetricRule:
    valid_min: float | None
    valid_max: float | None

    def quality_of(self, value: float | None) -> str:
        if value is None:
            return "missing"
        if self.valid_min is not None and value < self.valid_min:
            return "suspect"
        if self.valid_max is not None and value > self.valid_max:
            return "suspect"
        return "good"


def load_metric_rules(connection) -> dict[str, MetricRule]:
    with connection.cursor() as cursor:
        cursor.execute("SELECT metric_key, valid_min, valid_max FROM metric")
        return {key: MetricRule(lo, hi) for key, lo, hi in cursor.fetchall()}


@dataclass
class LoadReport:
    mission_id: str
    samples: int = 0
    rows: int = 0
    suspect: int = 0
    skipped_unknown: int = 0


def _batch_from_sample(sample: Sample, source: Source, rules: dict[str, MetricRule], report: LoadReport) -> Batch:
    observations = []
    for reading in sample.readings:
        rule = rules.get(reading.metric_key)
        if rule is None:
            # A metric the catalog does not know about would violate the
            # foreign key. Better to drop it loudly than to fail the file.
            report.skipped_unknown += 1
            continue
        quality = rule.quality_of(reading.value)
        if quality == "suspect":
            report.suspect += 1
        observations.append(
            ObservationRecord(
                sensor_id=reading.sensor_id,
                metric_key=reading.metric_key,
                value=reading.value,
                value_raw=reading.value_raw,
                quality=quality,
            )
        )

    position = None
    if sample.latitude is not None and sample.longitude is not None:
        # Whatever the source did not record stays null rather than being
        # invented: the particle runs log a bare coordinate, the GNSS receivers
        # log fix quality and satellite counts as well.
        position = PositionFix(
            lat=sample.latitude,
            lon=sample.longitude,
            alt_m=sample.altitude_m,
            speed_ms=sample.speed_ms,
            heading_deg=sample.heading_deg,
            vertical_speed_ms=None,
            fix_quality=sample.fix_quality,
            satellites=sample.satellites,
            hdop=sample.hdop,
        )

    return Batch(
        device_id=source.device_id,
        mission_id=source.mission_id,
        seq=sample.index,
        batch_time=sample.event_time,
        position=position,
        observations=observations,
    )


def load(source: Source, sink, rules: dict[str, MetricRule], chunk_size: int = CHUNK_SIZE) -> LoadReport:
    report = LoadReport(mission_id=source.mission_id)

    sink.open_mission(
        source.mission_id,
        name=source.mission_name,
        started_at=source.started_at,
        description=source.description,
        kind="measured",
    )

    label = f"file:{source.files[0].name}" if len(source.files) == 1 else f"files:{source.files[0].parent.name}"

    pending: list[Batch] = []
    chunk_index = 0
    last_time: datetime | None = None

    for sample in source.samples:
        pending.append(_batch_from_sample(sample, source, rules, report))
        report.samples += 1
        last_time = sample.event_time

        if len(pending) >= chunk_size:
            report.rows += sink.write_many(pending, label, chunk_index)
            pending.clear()
            chunk_index += 1

    if pending:
        report.rows += sink.write_many(pending, label, chunk_index)

    if last_time is not None:
        sink.close_mission(source.mission_id, last_time)

    return report
