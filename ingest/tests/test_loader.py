"""Loader tests: contract mapping, quality flagging, and idempotent writes."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from loader import MetricRule, load
from sources import Reading, Sample, Source

RULES = {
    "ch4_concentration": MetricRule(0.0, 10000.0),
    "co_concentration": MetricRule(0.0, 1000.0),
    "pm2_5": MetricRule(0.0, 1000.0),
    "air_temperature": MetricRule(-90.0, 60.0),
}


class FakeSink:
    """Records what a real sink would have written."""

    def __init__(self) -> None:
        self.missions: list[dict] = []
        self.batches: list = []
        self.labels: list[str] = []
        self.closed: list[tuple[str, datetime]] = []

    def open_mission(self, mission_id, name, started_at, description, kind="measured"):
        self.missions.append({"mission_id": mission_id, "kind": kind, "description": description})

    def write_many(self, batches, source, batch_seq):
        self.batches.extend(batches)
        self.labels.append(source)
        return sum(b.record_count for b in batches)

    def close_mission(self, mission_id, ended_at):
        self.closed.append((mission_id, ended_at))


def make_source(samples: list[Sample], files: list[Path] | None = None) -> Source:
    return Source(
        mission_id="test-mission",
        mission_name="Test",
        device_id="arduino-uno-5v",
        started_at=datetime(2026, 5, 18, tzinfo=timezone.utc),
        description="test",
        files=files or [Path("one.csv")],
        samples=iter(samples),
    )


def sample(index: int, readings: list[Reading], lat=None, lon=None) -> Sample:
    return Sample(
        index=index,
        event_time=datetime(2026, 5, 18, 10, 0, index, tzinfo=timezone.utc),
        readings=readings,
        latitude=lat,
        longitude=lon,
    )


def test_mission_is_labelled_measured() -> None:
    sink = FakeSink()
    load(make_source([sample(0, [Reading("mq4", "ch4_concentration", 1.0, 300)])]), sink, RULES)
    assert sink.missions[0]["kind"] == "measured"


def test_readings_become_observations_with_raw_and_calibrated() -> None:
    sink = FakeSink()
    load(make_source([sample(0, [Reading("mq4", "ch4_concentration", 1.5, 316)])]), sink, RULES)
    observation = sink.batches[0].observations[0]
    assert (observation.sensor_id, observation.metric_key) == ("mq4", "ch4_concentration")
    assert (observation.value, observation.value_raw) == (1.5, 316)


def test_value_outside_the_catalog_range_is_flagged_suspect() -> None:
    sink = FakeSink()
    # 73357 ppm CO is what the uncalibrated MiCS-6814 actually reports.
    report = load(
        make_source([sample(0, [Reading("mics6814_co", "co_concentration", 73357.2, 766)])]),
        sink,
        RULES,
    )
    assert sink.batches[0].observations[0].quality == "suspect"
    assert report.suspect == 1


def test_plausible_value_stays_good() -> None:
    sink = FakeSink()
    report = load(make_source([sample(0, [Reading("mq4", "ch4_concentration", 12.0, 300)])]), sink, RULES)
    assert sink.batches[0].observations[0].quality == "good"
    assert report.suspect == 0


def test_unknown_metric_is_dropped_not_fatal() -> None:
    sink = FakeSink()
    report = load(
        make_source([sample(0, [
            Reading("mq4", "ch4_concentration", 1.0, 300),
            Reading("mq4", "not_in_catalog", 1.0),
        ])]),
        sink,
        RULES,
    )
    assert report.skipped_unknown == 1
    assert len(sink.batches[0].observations) == 1


def test_coordinates_become_a_position_without_inventing_altitude() -> None:
    sink = FakeSink()
    load(
        make_source([sample(0, [Reading("sps30", "pm2_5", 10.0)], lat=40.1978, lon=-8.5093)]),
        sink,
        RULES,
    )
    position = sink.batches[0].position
    assert (position.lat, position.lon) == (40.1978, -8.5093)
    # The particle runs log a bare coordinate; everything else stays null.
    assert position.alt_m is None
    assert position.satellites is None
    assert position.hdop is None


def test_bench_sample_has_no_position_at_all() -> None:
    sink = FakeSink()
    load(make_source([sample(0, [Reading("mq4", "ch4_concentration", 1.0, 300)])]), sink, RULES)
    assert sink.batches[0].position is None


def test_seq_follows_sample_index_so_reruns_are_idempotent() -> None:
    sink = FakeSink()
    load(
        make_source([
            sample(0, [Reading("mq4", "ch4_concentration", 1.0, 300)]),
            sample(1, [Reading("mq4", "ch4_concentration", 1.1, 301)]),
        ]),
        sink,
        RULES,
    )
    assert [b.seq for b in sink.batches] == [0, 1]


def test_record_count_excludes_the_absent_position() -> None:
    sink = FakeSink()
    load(
        make_source([sample(0, [
            Reading("mq4", "ch4_concentration", 1.0, 300),
            Reading("mics5524", "co_concentration", 0.3, 195),
        ])]),
        sink,
        RULES,
    )
    assert sink.batches[0].record_count == 2


def test_single_file_source_is_labelled_by_filename() -> None:
    sink = FakeSink()
    load(make_source([sample(0, [Reading("mq4", "ch4_concentration", 1.0, 300)])]), sink, RULES)
    assert sink.labels[0] == "file:one.csv"


def test_mission_is_closed_at_the_last_sample() -> None:
    sink = FakeSink()
    load(
        make_source([
            sample(0, [Reading("mq4", "ch4_concentration", 1.0, 300)]),
            sample(1, [Reading("mq4", "ch4_concentration", 1.0, 300)]),
        ]),
        sink,
        RULES,
    )
    assert sink.closed[0][1] == datetime(2026, 5, 18, 10, 0, 1, tzinfo=timezone.utc)


def test_chunking_splits_large_sources_into_several_writes() -> None:
    sink = FakeSink()
    samples = [sample(i, [Reading("mq4", "ch4_concentration", 1.0, 300)]) for i in range(5)]
    load(make_source(samples), sink, RULES, chunk_size=2)
    assert len(sink.labels) == 3    # 2 + 2 + 1
    assert len(sink.batches) == 5
