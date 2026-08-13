"""The batch payload must match the documented contract exactly."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from cli import SAMPLE_PERIOD_S, build_batch, parse_args
from contract import Batch, ObservationRecord, PositionFix
from flight import FlightProfile
from sensors import build_scalar_sensors


def _batch() -> Batch:
    profile = FlightProfile(duration_s=9000.0, seed=1)
    sensors = build_scalar_sensors(1)
    return build_batch(
        profile,
        sensors,
        mission_id="flight-test",
        device_id="balloon-01",
        sample_index=1042,
        event_time=datetime(2026, 8, 12, 10, 23, 45, 120000, tzinfo=timezone.utc),
    )


def test_batch_has_the_documented_top_level_keys() -> None:
    payload = _batch().to_dict()
    assert set(payload) == {
        "device_id",
        "mission_id",
        "seq",
        "batch_time",
        "position",
        "observations",
        "waveforms",
        "events",
    }


def test_batch_time_is_iso_utc_with_milliseconds() -> None:
    payload = _batch().to_dict()
    assert payload["batch_time"] == "2026-08-12T10:23:45.120000Z"


def test_position_uses_contract_names_not_column_names() -> None:
    position = _batch().to_dict()["position"]
    assert set(position) == {
        "lat",
        "lon",
        "alt_m",
        "speed_ms",
        "heading_deg",
        "vertical_speed_ms",
        "fix_quality",
        "satellites",
        "hdop",
    }


def test_observations_carry_raw_and_calibrated_and_an_offset() -> None:
    observations = _batch().to_dict()["observations"]
    assert len(observations) == 3
    for observation in observations:
        assert observation["sensor_id"] == "bme280"
        assert "value" in observation and "value_raw" in observation
        assert "t_offset_ms" in observation
        # Scalars must not carry vector components.
        assert "vx" not in observation
    assert {o["t_offset_ms"] for o in observations} == {0, 15, 30}


def test_vector_observations_omit_scalar_fields() -> None:
    record = ObservationRecord(sensor_id="mmc5983", metric_key="mag_field", vx=21.4, vy=-3.2, vz=43.8)
    payload = record.to_dict()
    assert payload["vx"] == 21.4
    assert "value" not in payload and "value_raw" not in payload


def test_record_count_matches_what_lands_in_the_database() -> None:
    batch = _batch()
    assert batch.record_count == 1 + len(batch.observations)


def test_batch_is_json_serialisable() -> None:
    assert json.loads(_batch().to_json())["seq"] == 1042


def test_seq_follows_the_sample_index() -> None:
    assert _batch().seq == 1042


def test_sample_period_is_one_hertz() -> None:
    assert SAMPLE_PERIOD_S == 1.0


def test_defaults_match_the_spec() -> None:
    args = parse_args([])
    assert args.speed == 1.0
    assert args.duration_min == 150.0
    assert args.device_id == "balloon-01"
    assert args.on_finish == "stop"
    assert (args.start_lat, args.start_lon) == (40.8586, -8.6216)


def test_position_fix_rounds_for_the_wire() -> None:
    fix = PositionFix(
        lat=40.123456789,
        lon=-8.987654321,
        alt_m=12345.6789,
        speed_ms=18.4444,
        heading_deg=87.6543,
        vertical_speed_ms=5.1111,
        fix_quality=4,
        satellites=11,
        hdop=0.9,
    )
    payload = fix.to_dict()
    assert payload["lat"] == 40.123457
    assert payload["alt_m"] == 12345.7
