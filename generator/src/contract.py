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
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class PositionFix:
    lat: float
    lon: float
    alt_m: float
    speed_ms: float
    heading_deg: float
    vertical_speed_ms: float
    fix_quality: int
    satellites: int
    hdop: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "lat": round(self.lat, 6),
            "lon": round(self.lon, 6),
            "alt_m": round(self.alt_m, 1),
            "speed_ms": round(self.speed_ms, 2),
            "heading_deg": round(self.heading_deg, 1),
            "vertical_speed_ms": round(self.vertical_speed_ms, 2),
            "fix_quality": self.fix_quality,
            "satellites": self.satellites,
            "hdop": self.hdop,
        }


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
        return payload


@dataclass(frozen=True)
class Batch:
    """One instant of the flight: a fix plus everything measured around it."""

    device_id: str
    mission_id: str
    seq: int
    batch_time: datetime
    position: PositionFix
    observations: list[ObservationRecord] = field(default_factory=list)
    # Populated in phases 3 and 4. Present now so the shape never changes.
    waveforms: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)

    @property
    def record_count(self) -> int:
        return 1 + len(self.observations) + len(self.waveforms) + len(self.events)

    def to_dict(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "mission_id": self.mission_id,
            "seq": self.seq,
            "batch_time": self.batch_time.isoformat().replace("+00:00", "Z"),
            "position": self.position.to_dict(),
            "observations": [o.to_dict() for o in self.observations],
            "waveforms": list(self.waveforms),
            "events": list(self.events),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"))
