"""File mode and replay: a replayed flight must be the flight that was exported.

The round trip is the contract's own guarantee (DEC-20): whatever to_dict
writes, from_dict has to give back, or the file is a lossy copy of the flight
and the replay is a different flight wearing its name.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from contract import Batch, ObservationRecord, PositionFix, WaveformRecord
from replay import TimeMap, replay_mission_id, shift
from sinks import NdjsonSink, read_ndjson

T0 = datetime(2026, 8, 15, 10, 0, 0, tzinfo=timezone.utc)
NOW = datetime(2026, 8, 15, 12, 0, 0, tzinfo=timezone.utc)


def _batch(seq: int) -> Batch:
    return Batch(
        device_id="balloon-01",
        mission_id="flight-test",
        seq=seq,
        batch_time=T0 + timedelta(seconds=seq),
        position=PositionFix(
            lat=40.8586,
            lon=-8.6216,
            alt_m=1200.5,
            speed_ms=18.4,
            heading_deg=87.0,
            vertical_speed_ms=5.1,
            fix_quality=4,
            satellites=11,
            hdop=0.9,
        ),
        observations=[
            ObservationRecord(
                sensor_id="bme280",
                metric_key="air_temperature",
                t_offset_ms=15,
                value=-52.1,
                value_raw=-52.34,
            ),
            ObservationRecord(
                sensor_id="mmc5983",
                metric_key="mag_field",
                t_offset_ms=20,
                value=44.5,
                vx=21.4,
                vy=-3.2,
                vz=43.8,
                quality="suspect",
            ),
        ],
        waveforms=[
            WaveformRecord(
                sensor_id="icm42688",
                metric_key="acceleration",
                sample_rate_hz=100.0,
                first_sample_index=seq * 100,
                full_scale=16.0,
                samples_x=[0.1, 0.2],
                samples_y=[-0.1, 0.0],
                samples_z=[9.8, 9.81],
            )
        ],
    )


def _export(path) -> None:
    sink = NdjsonSink(path)
    sink.open_mission(
        "flight-test",
        name="Test flight",
        started_at=T0,
        description="SYNTHETIC. Not measured.",
        kind="synthetic",
    )
    for seq in range(3):
        sink.write(_batch(seq))
    sink.close_mission("flight-test", T0 + timedelta(seconds=2))
    sink.close()


def test_export_writes_one_header_then_one_line_per_batch(tmp_path) -> None:
    path = tmp_path / "flight.ndjson"
    _export(path)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 4
    assert lines[0].startswith('{"mission"')


def test_header_carries_the_mission_kind(tmp_path) -> None:
    """DEC-18: the file has to say it is synthetic, or the replay cannot."""
    path = tmp_path / "flight.ndjson"
    _export(path)
    header, _ = read_ndjson(path)
    assert header["kind"] == "synthetic"
    assert header["mission_id"] == "flight-test"


def test_round_trip_preserves_every_batch_field(tmp_path) -> None:
    path = tmp_path / "flight.ndjson"
    _export(path)
    _, batches = read_ndjson(path)
    restored = list(batches)

    assert len(restored) == 3
    for seq, batch in enumerate(restored):
        assert batch.to_dict() == _batch(seq).to_dict()


def test_round_trip_keeps_waveform_samples_and_quality(tmp_path) -> None:
    path = tmp_path / "flight.ndjson"
    _export(path)
    _, batches = read_ndjson(path)
    batch = next(iter(batches))

    assert batch.waveforms[0].samples_z == [9.8, 9.81]
    assert batch.waveforms[0].sample_count == 2
    assert batch.observations[1].quality == "suspect"
    assert batch.observations[1].vx == 21.4
    # A vector observation carries no scalar raw value, and must not gain one.
    assert batch.observations[1].value_raw is None


def test_a_file_without_a_header_still_replays(tmp_path) -> None:
    path = tmp_path / "bare.ndjson"
    path.write_text("\n".join(_batch(seq).to_json() for seq in range(2)) + "\n", encoding="utf-8")
    header, batches = read_ndjson(path)
    assert header is None
    assert [b.seq for b in batches] == [0, 1]


def test_anchor_now_starts_the_flight_at_the_replay_instant() -> None:
    clock = TimeMap(first_batch_time=T0, anchor_time=NOW, speed=1.0)
    assert clock.event_time(T0) == NOW
    assert clock.event_time(T0 + timedelta(minutes=10)) == NOW + timedelta(minutes=10)


def test_acceleration_compresses_event_time_not_just_the_pace() -> None:
    """DEC-16 again: at 60x the flight is happening now, only faster.

    Shifting the timestamps by a constant while inserting 60x faster would put
    event_time ahead of the clock, which is how the first version of this
    replayer produced a lag of minus five minutes.
    """
    clock = TimeMap(first_batch_time=T0, anchor_time=NOW, speed=60.0)
    ten_minutes_in = T0 + timedelta(minutes=10)

    assert clock.event_time(ten_minutes_in) == NOW + timedelta(seconds=10)
    # Insertion instant and event instant coincide, so the lag panel measures
    # the pipeline rather than the acceleration factor.
    assert clock.wall_time(ten_minutes_in) == clock.event_time(ten_minutes_in)


def test_anchor_original_leaves_the_timestamps_alone() -> None:
    clock = TimeMap(first_batch_time=T0, anchor_time=NOW, speed=60.0, anchor="original")
    assert clock.event_time(T0 + timedelta(minutes=10)) == T0 + timedelta(minutes=10)
    # The pace is still accelerated, only the recorded instants are preserved.
    assert clock.wall_time(T0 + timedelta(minutes=10)) == NOW + timedelta(seconds=10)


def test_each_replay_gets_its_own_mission_id() -> None:
    """Two replays under one id would collide on the DEC-03 natural key."""
    first = replay_mission_id("flight-test", T0)
    second = replay_mission_id("flight-test", T0 + timedelta(seconds=1))
    assert first == "flight-test-replay-2026-08-15T100000"
    assert first != second


def test_shift_rewrites_the_mission_and_the_clock_and_nothing_else() -> None:
    original = _batch(7)
    clock = TimeMap(first_batch_time=T0, anchor_time=NOW, speed=1.0)
    moved = shift(original, clock, "flight-test-replay-x", None)

    assert moved.mission_id == "flight-test-replay-x"
    assert moved.batch_time == NOW + timedelta(seconds=7)
    # seq is part of the natural key and identifies the sample within the
    # flight: moving the flight in time must not renumber it.
    assert moved.seq == original.seq
    assert moved.device_id == original.device_id
    assert moved.observations == original.observations
    assert moved.waveforms == original.waveforms


def test_shift_can_retarget_the_device() -> None:
    clock = TimeMap(first_batch_time=T0, anchor_time=NOW, speed=1.0)
    moved = shift(_batch(1), clock, "m", "balloon-02")
    assert moved.device_id == "balloon-02"
