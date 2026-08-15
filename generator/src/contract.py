"""Batch serialisation.

This is the structure documented under "Data contract" in docs/data-model.md.
It is the payload written to NDJSON in file mode, and the internal form the
direct sink consumes, so both paths agree by construction.

Field names here follow the contract (lat, lon, alt_m), not the column names
in the database (latitude, longitude, altitude_m). Mapping between the two is
the sink's job.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class PositionFix:
    # Only lat and lon are always known. The particle runs log a bare
    # coordinate with no altitude, speed or satellite count, and inventing
    # those would be inventing measurements.
    lat: float
    lon: float
    alt_m: float | None = None
    speed_ms: float | None = None
    heading_deg: float | None = None
    vertical_speed_ms: float | None = None
    fix_quality: int | None = None
    satellites: int | None = None
    hdop: float | None = None

    def to_dict(self) -> dict[str, Any]:
        def rounded(value: float | None, places: int) -> float | None:
            return None if value is None else round(value, places)

        return {
            "lat": rounded(self.lat, 6),
            "lon": rounded(self.lon, 6),
            "alt_m": rounded(self.alt_m, 1),
            "speed_ms": rounded(self.speed_ms, 2),
            "heading_deg": rounded(self.heading_deg, 1),
            "vertical_speed_ms": rounded(self.vertical_speed_ms, 2),
            "fix_quality": self.fix_quality,
            "satellites": self.satellites,
            "hdop": self.hdop,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "PositionFix":
        return cls(
            lat=payload["lat"],
            lon=payload["lon"],
            alt_m=payload.get("alt_m"),
            speed_ms=payload.get("speed_ms"),
            heading_deg=payload.get("heading_deg"),
            vertical_speed_ms=payload.get("vertical_speed_ms"),
            fix_quality=payload.get("fix_quality"),
            satellites=payload.get("satellites"),
            hdop=payload.get("hdop"),
        )


@dataclass(frozen=True)
class ObservationRecord:
    sensor_id: str
    metric_key: str
    t_offset_ms: int = 0
    value: float | None = None
    value_raw: float | None = None
    vx: float | None = None
    vy: float | None = None
    vz: float | None = None
    # 'suspect' when a reading falls outside the metric's plausible range.
    # Several of the provided gas channels were never calibrated against clean
    # air and report values that are physically impossible.
    quality: str = "good"

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "sensor_id": self.sensor_id,
            "metric_key": self.metric_key,
            "t_offset_ms": self.t_offset_ms,
        }
        for name in ("value", "value_raw", "vx", "vy", "vz"):
            value = getattr(self, name)
            if value is not None:
                payload[name] = value
        if self.quality != "good":
            payload["quality"] = self.quality
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ObservationRecord":
        return cls(
            sensor_id=payload["sensor_id"],
            metric_key=payload["metric_key"],
            t_offset_ms=payload.get("t_offset_ms", 0),
            value=payload.get("value"),
            value_raw=payload.get("value_raw"),
            vx=payload.get("vx"),
            vy=payload.get("vy"),
            vz=payload.get("vz"),
            quality=payload.get("quality", "good"),
        )


@dataclass(frozen=True)
class WaveformRecord:
    """One block of high-rate samples, never one row per sample (DEC-07).

    Sample timing comes from first_sample_index and sample_rate_hz, not from
    the clock (DEC-02).
    """

    sensor_id: str
    metric_key: str
    sample_rate_hz: float
    first_sample_index: int
    samples_x: list[float]
    samples_y: list[float]
    samples_z: list[float]
    full_scale: float | None = None
    t_offset_ms: int = 0

    @property
    def sample_count(self) -> int:
        return len(self.samples_x)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sensor_id": self.sensor_id,
            "metric_key": self.metric_key,
            "sample_rate_hz": self.sample_rate_hz,
            "first_sample_index": self.first_sample_index,
            "full_scale": self.full_scale,
            "samples_x": self.samples_x,
            "samples_y": self.samples_y,
            "samples_z": self.samples_z,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "WaveformRecord":
        return cls(
            sensor_id=payload["sensor_id"],
            metric_key=payload["metric_key"],
            sample_rate_hz=payload["sample_rate_hz"],
            first_sample_index=payload["first_sample_index"],
            samples_x=list(payload["samples_x"]),
            samples_y=list(payload["samples_y"]),
            samples_z=list(payload["samples_z"]),
            full_scale=payload.get("full_scale"),
            t_offset_ms=payload.get("t_offset_ms", 0),
        )


@dataclass(frozen=True)
class Batch:
    """One instant of the flight: a fix plus everything measured around it."""

    device_id: str
    mission_id: str
    seq: int
    batch_time: datetime
    # None for bench sessions: most of the provided data was recorded indoors
    # with no GNSS at all, and only the _Coord particle runs carry a fix.
    position: PositionFix | None = None
    observations: list[ObservationRecord] = field(default_factory=list)
    waveforms: list[WaveformRecord] = field(default_factory=list)
    # Events remain unused: nothing in the provided data records one.
    events: list[dict[str, Any]] = field(default_factory=list)

    @property
    def record_count(self) -> int:
        positions = 1 if self.position is not None else 0
        return positions + len(self.observations) + len(self.waveforms) + len(self.events)

    def to_dict(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "mission_id": self.mission_id,
            "seq": self.seq,
            "batch_time": self.batch_time.isoformat().replace("+00:00", "Z"),
            "position": self.position.to_dict() if self.position is not None else None,
            "observations": [o.to_dict() for o in self.observations],
            "waveforms": [w.to_dict() for w in self.waveforms],
            "events": list(self.events),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"))

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Batch":
        """Rebuild a batch from its wire form.

        The replayer's half of file mode. to_dict and this must stay exact
        inverses of each other, or a replayed flight stops being the flight
        that was exported (DEC-20).
        """
        position = payload.get("position")
        return cls(
            device_id=payload["device_id"],
            mission_id=payload["mission_id"],
            seq=payload["seq"],
            batch_time=parse_time(payload["batch_time"]),
            position=PositionFix.from_dict(position) if position else None,
            observations=[ObservationRecord.from_dict(o) for o in payload.get("observations", [])],
            waveforms=[WaveformRecord.from_dict(w) for w in payload.get("waveforms", [])],
            events=list(payload.get("events", [])),
        )

    @classmethod
    def from_json(cls, line: str) -> "Batch":
        return cls.from_dict(json.loads(line))


def parse_time(text: str) -> datetime:
    """ISO-8601 as written by to_dict, always UTC (DEC-01)."""
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
