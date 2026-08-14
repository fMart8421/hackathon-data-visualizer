"""Synthetic models for the four quantities nothing in data/ measured.

DEC-18 governs everything here: generated data must obey the rules the real
data sets. In practice that means three things.

  Catalog first. Only metric keys that already exist are produced, in their
  canonical units: mag_field in uT, acceleration in m/s^2, angular_rate in
  deg/s, uv_index dimensionless.

  Raw and calibrated, the way the real loggers do it (DEC-05). Every real file
  in data/ records both an uncorrected reading and a corrected one: an ADC
  count beside a ppm, a resistance beside a concentration. So value_raw here
  carries the sensor's own error (hard-iron offset on the magnetometer, bias
  and dark current on the UV photodiode) and value carries the compensated
  figure. Not noise sprinkled on a number, but the specific error each part
  actually has.

  Anchored to something real. The magnetic field is the field at Taveiro, the
  attitude comes from a route that was actually ridden, and the UV curve comes
  from the sun's real position over that site on that date. Nothing here is a
  free-running random walk.

None of it is a measurement, and the mission carrying it is labelled
kind = 'synthetic' so nothing downstream can mistake it for one.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

# --- geomagnetic field at Taveiro -----------------------------------------
# IGRF-ish values for 40.2 N, 8.5 W, mid-2025. Total intensity about 44.5 uT,
# dipping steeply into the ground at these latitudes.
FIELD_TOTAL_UT = 44.5
FIELD_INCLINATION_DEG = 55.0
FIELD_DECLINATION_DEG = -1.5

# Hard-iron offset: a fixed magnetic bias from the ferrous parts of the
# platform itself. It is the dominant magnetometer error and the reason a raw
# reading needs calibrating at all.
HARD_IRON_UT = (2.6, -1.4, 0.9)

GRAVITY = 9.80665

# --- solar model ----------------------------------------------------------
# Clear-sky UV index from solar elevation. The exponent and scale are fitted so
# noon in late June at this latitude peaks around 9, which is what Coimbra
# actually sees.
UV_CLEAR_SKY_PEAK = 10.4
UV_ELEVATION_EXPONENT = 2.5


def solar_declination_deg(day_of_year: int) -> float:
    """Cheap but adequate: within about 0.5 degrees across the year."""
    return 23.44 * math.sin(math.radians(360.0 / 365.0 * (day_of_year - 81)))


def solar_elevation_deg(
    latitude: float, longitude: float, day_of_year: int, utc_hours: float
) -> float:
    """Sun's angle above the horizon. Negative before sunrise and after sunset."""
    declination = math.radians(solar_declination_deg(day_of_year))
    phi = math.radians(latitude)
    # Longitude shifts solar noon away from 12:00 UTC: 4 minutes per degree.
    solar_hours = utc_hours + longitude / 15.0
    hour_angle = math.radians(15.0 * (solar_hours - 12.0))
    sin_elevation = (
        math.sin(phi) * math.sin(declination)
        + math.cos(phi) * math.cos(declination) * math.cos(hour_angle)
    )
    return math.degrees(math.asin(max(-1.0, min(1.0, sin_elevation))))


def clear_sky_uv_index(elevation_deg: float) -> float:
    if elevation_deg <= 0.0:
        return 0.0
    return UV_CLEAR_SKY_PEAK * math.sin(math.radians(elevation_deg)) ** UV_ELEVATION_EXPONENT


# --- magnetometer ---------------------------------------------------------


@dataclass(frozen=True)
class VectorReading:
    vx: float
    vy: float
    vz: float
    magnitude: float
    vx_raw: float
    vy_raw: float
    vz_raw: float
    magnitude_raw: float


def field_ned() -> tuple[float, float, float]:
    """Earth's field at the site, in north/east/down microtesla."""
    inclination = math.radians(FIELD_INCLINATION_DEG)
    declination = math.radians(FIELD_DECLINATION_DEG)
    horizontal = FIELD_TOTAL_UT * math.cos(inclination)
    return (
        horizontal * math.cos(declination),
        horizontal * math.sin(declination),
        FIELD_TOTAL_UT * math.sin(inclination),
    )


class Magnetometer:
    """Body-frame magnetic vector for a platform on a known heading.

    The field itself is fixed in the ground frame, so every change the panel
    shows comes from the platform turning. That is what makes requirement 3's
    arrows rotate along the route: they are not animated, they are the real
    geometry of a real ride against a real field.
    """

    metric_key = "mag_field"

    def __init__(self, sensor_id: str = "mmc5983", seed: int = 20260628) -> None:
        self.sensor_id = sensor_id
        self.seed = seed
        self.north, self.east, self.down = field_ned()

    def _noise(self, index: int, axis: str) -> float:
        return random.Random(f"{self.seed}:mag:{axis}:{index}").gauss(0.0, 0.25)

    def read(self, heading_deg: float, index: int) -> VectorReading:
        heading = math.radians(heading_deg)
        # Rotate the ground-frame field into the body frame: x forward along
        # the heading, y to the right, z down.
        forward = self.north * math.cos(heading) + self.east * math.sin(heading)
        right = -self.north * math.sin(heading) + self.east * math.cos(heading)
        down = self.down

        vx = forward + self._noise(index, "x")
        vy = right + self._noise(index, "y")
        vz = down + self._noise(index, "z")

        # The uncalibrated part carries the platform's own hard-iron field.
        vx_raw = vx + HARD_IRON_UT[0]
        vy_raw = vy + HARD_IRON_UT[1]
        vz_raw = vz + HARD_IRON_UT[2]

        return VectorReading(
            vx=round(vx, 3),
            vy=round(vy, 3),
            vz=round(vz, 3),
            magnitude=round(math.sqrt(vx * vx + vy * vy + vz * vz), 3),
            vx_raw=round(vx_raw, 3),
            vy_raw=round(vy_raw, 3),
            vz_raw=round(vz_raw, 3),
            magnitude_raw=round(math.sqrt(vx_raw**2 + vy_raw**2 + vz_raw**2), 3),
        )


# --- IMU ------------------------------------------------------------------


@dataclass(frozen=True)
class Block:
    samples_x: list[float]
    samples_y: list[float]
    samples_z: list[float]


class Imu:
    """Accelerometer and gyroscope for a bicycle, in blocks of one second.

    Two things drive the signal and both come from outside this class: gravity,
    which sits on the z axis because the frame is level, and the route, whose
    curvature sets the yaw rate and lateral acceleration. Pedalling adds a
    steady cadence oscillation, and the road surface adds broadband noise.
    """

    accel_metric = "acceleration"
    gyro_metric = "angular_rate"
    accel_full_scale = 156.9   # +/- 16 g
    gyro_full_scale = 2000.0   # deg/s

    def __init__(
        self,
        sensor_id: str = "icm42688",
        sample_rate_hz: float = 100.0,
        cadence_hz: float = 1.4,
        seed: int = 20260628,
    ) -> None:
        self.sensor_id = sensor_id
        self.sample_rate_hz = sample_rate_hz
        self.cadence_hz = cadence_hz
        self.seed = seed

    def _rng(self, second: int, channel: str) -> random.Random:
        return random.Random(f"{self.seed}:imu:{channel}:{second}")

    def acceleration_block(self, second: int, speed_ms: float, yaw_rate_dps: float) -> Block:
        rng = self._rng(second, "accel")
        count = int(self.sample_rate_hz)
        # Rougher road at speed, and the pedal stroke pushes the frame twice
        # per crank revolution.
        roughness = 0.25 + 0.06 * speed_ms
        stroke = 0.9 + 0.05 * speed_ms

        x, y, z = [], [], []
        for i in range(count):
            t = second + i / self.sample_rate_hz
            phase = 2 * math.pi * self.cadence_hz * t
            # Centripetal acceleration is the turn the route actually took.
            lateral = math.radians(yaw_rate_dps) * speed_ms

            x.append(round(0.35 * math.sin(phase) + rng.gauss(0.0, roughness), 4))
            y.append(round(lateral + 0.20 * math.sin(phase + 1.1) + rng.gauss(0.0, roughness), 4))
            z.append(round(GRAVITY + stroke * math.sin(phase * 2) + rng.gauss(0.0, roughness * 1.6), 4))
        return Block(x, y, z)

    def angular_rate_block(self, second: int, yaw_rate_dps: float) -> Block:
        rng = self._rng(second, "gyro")
        count = int(self.sample_rate_hz)

        x, y, z = [], [], []
        for i in range(count):
            t = second + i / self.sample_rate_hz
            phase = 2 * math.pi * self.cadence_hz * t
            # Roll rocks with the pedal stroke, pitch barely moves, and yaw is
            # the route's own turn rate.
            x.append(round(3.2 * math.sin(phase) + rng.gauss(0.0, 0.8), 4))
            y.append(round(0.9 * math.sin(phase + 0.6) + rng.gauss(0.0, 0.6), 4))
            z.append(round(yaw_rate_dps + rng.gauss(0.0, 1.2), 4))
        return Block(x, y, z)


# --- UV -------------------------------------------------------------------


@dataclass(frozen=True)
class ScalarReading:
    value: float
    value_raw: float


class UvSensor:
    """UV index from the sun's real position, dimmed by passing cloud.

    value_raw is what a photodiode front end would hand over before
    compensation: a slightly scaled reading sitting on a dark-current offset
    that never reaches zero, even at night. value is the corrected index.
    """

    metric_key = "uv_index"
    dark_offset = 0.32
    gain_error = 1.06

    def __init__(self, sensor_id: str = "veml6075", seed: int = 20260628) -> None:
        self.sensor_id = sensor_id
        self.seed = seed

    def cloud_factor(self, utc_hours: float) -> float:
        # Two slow cloud banks over the day, never a full blackout.
        slow = 0.90 + 0.10 * math.sin(2 * math.pi * utc_hours / 7.3 + 1.2)
        fast = 0.96 + 0.04 * math.sin(2 * math.pi * utc_hours / 1.7 + 0.4)
        return slow * fast

    def read(
        self, latitude: float, longitude: float, day_of_year: int, utc_hours: float, index: int
    ) -> ScalarReading:
        elevation = solar_elevation_deg(latitude, longitude, day_of_year, utc_hours)
        clear = clear_sky_uv_index(elevation)
        true_index = clear * self.cloud_factor(utc_hours)

        noise = random.Random(f"{self.seed}:uv:{index}").gauss(0.0, 0.05)
        value = max(0.0, true_index + noise)
        value_raw = max(0.0, value * self.gain_error + self.dark_offset)
        return ScalarReading(value=round(value, 3), value_raw=round(value_raw, 3))
