"""Flight profile: phases, altitude, and trajectory.

The whole flight is precomputed into a table at construction, at a fixed step,
and every query interpolates that table. Two reasons: a given seed always
produces exactly the same flight (DEC-12 wants a reproducible demo), and the
trajectory is an integral of wind over time, which cannot be evaluated at an
arbitrary instant without replaying the path anyway.

Altitude is built per phase:

  ascent   integral of a positive, gently varying climb rate, normalised so it
           lands exactly on the burst altitude at the end of the phase
  descent  integral of parachute terminal velocity, which scales with
           1/sqrt(air density), so the fall is fast up high and slows down
  landing  a mild ease-out onto the ground
"""

from __future__ import annotations

import math
import random
from bisect import bisect_right
from dataclasses import dataclass
from enum import Enum

from sensors import isa_density

# Ovar, Portugal. Launch site from the generator spec.
OVAR_LAT = 40.8586
OVAR_LON = -8.6216

BURST_ALTITUDE_M = 30_000.0
LANDING_START_ALTITUDE_M = 1_000.0

# Phase boundaries as a fraction of total duration, from the profile table:
# 0-90 min ascent, 90-140 descent, 140-150 landing, out of 150.
ASCENT_FRACTION = 90.0 / 150.0
DESCENT_FRACTION = 140.0 / 150.0

METERS_PER_DEGREE_LAT = 111_320.0

SEA_LEVEL_DENSITY = 1.225


class Phase(str, Enum):
    ASCENT = "ascent"
    BURST = "burst"
    DESCENT = "descent"
    LANDING = "landing"


@dataclass(frozen=True)
class FlightState:
    """Where the platform is, and how it is moving, at one instant."""

    mission_time_s: float
    phase: Phase
    altitude_m: float
    latitude: float
    longitude: float
    speed_ms: float
    heading_deg: float
    vertical_speed_ms: float
    fix_quality: int
    satellites: int
    hdop: float


class FlightProfile:
    def __init__(
        self,
        duration_s: float = 9000.0,
        seed: int = 20260812,
        start_lat: float = OVAR_LAT,
        start_lon: float = OVAR_LON,
        step_s: float = 1.0,
    ) -> None:
        self.duration_s = duration_s
        self.step_s = step_s
        self.seed = seed
        self.burst_time_s = duration_s * ASCENT_FRACTION
        self.descent_end_s = duration_s * DESCENT_FRACTION

        rng = random.Random(seed)
        # Phase offsets for the climb-rate wobble and the wind rotation. Drawn
        # once so the flight is a pure function of the seed.
        self._climb_phase_1 = rng.uniform(0, 2 * math.pi)
        self._climb_phase_2 = rng.uniform(0, 2 * math.pi)
        self._wind_phase = rng.uniform(0, 2 * math.pi)
        self._gnss_phase = rng.uniform(0, 2 * math.pi)

        self._build_altitude()
        self._build_trajectory(start_lat, start_lon)

    # -- altitude ----------------------------------------------------------

    def _climb_rate_shape(self, t: float) -> float:
        """Unnormalised climb rate. Stays positive, so altitude stays monotonic."""
        return (
            1.0
            + 0.18 * math.sin(2 * math.pi * t / 1300.0 + self._climb_phase_1)
            + 0.09 * math.sin(2 * math.pi * t / 470.0 + self._climb_phase_2)
        )

    def _climb_integral(self, t: float) -> float:
        """Closed-form integral of _climb_rate_shape from 0 to t."""
        a1, p1 = 0.18, 1300.0
        a2, p2 = 0.09, 470.0
        return (
            t
            - a1 * p1 / (2 * math.pi) * (math.cos(2 * math.pi * t / p1 + self._climb_phase_1) - math.cos(self._climb_phase_1))
            - a2 * p2 / (2 * math.pi) * (math.cos(2 * math.pi * t / p2 + self._climb_phase_2) - math.cos(self._climb_phase_2))
        )

    def _build_descent_table(self) -> tuple[list[float], list[float]]:
        """Altitude against time under a parachute, from burst to landing start.

        Terminal velocity goes as 1/sqrt(density), so the balloon falls at tens
        of m/s in the stratosphere and only a few m/s near the ground. The
        reference speed is chosen so the fall fills the descent phase exactly.
        """
        step_m = 25.0
        altitudes: list[float] = []
        weights: list[float] = []
        h = BURST_ALTITUDE_M
        while h > LANDING_START_ALTITUDE_M:
            altitudes.append(h)
            # dt = dh / v(h), and v(h) = v_ref / sqrt(density_ratio)
            weights.append(math.sqrt(isa_density(h) / SEA_LEVEL_DENSITY))
            h -= step_m
        altitudes.append(LANDING_START_ALTITUDE_M)

        descent_duration = self.descent_end_s - self.burst_time_s
        unscaled_total = sum(w * step_m for w in weights)
        v_ref = unscaled_total / descent_duration

        times = [0.0]
        for weight in weights:
            times.append(times[-1] + (weight * step_m) / v_ref)
        return times, altitudes

    def _altitude_at(self, t: float, descent_times: list[float], descent_alts: list[float]) -> float:
        if t <= 0.0:
            return 0.0
        if t < self.burst_time_s:
            return BURST_ALTITUDE_M * self._climb_integral(t) / self._climb_integral(self.burst_time_s)
        if t < self.descent_end_s:
            # This table is spaced by altitude, not by time, so its time axis
            # is deliberately non-uniform: fast at the top, slow at the bottom.
            return _interpolate_irregular(descent_times, descent_alts, t - self.burst_time_s)
        if t < self.duration_s:
            # Ease-out onto the ground: still moving, but slowing.
            progress = (t - self.descent_end_s) / (self.duration_s - self.descent_end_s)
            return LANDING_START_ALTITUDE_M * (1.0 - progress) ** 1.15
        return 0.0

    def _build_altitude(self) -> None:
        descent_times, descent_alts = self._build_descent_table()
        self._times = [i * self.step_s for i in range(int(self.duration_s / self.step_s) + 1)]
        self._altitude = [self._altitude_at(t, descent_times, descent_alts) for t in self._times]

        # Forward difference, so the burst shows up as an abrupt sign change
        # rather than being smeared across the transition.
        self._vertical_speed = []
        for i in range(len(self._times)):
            j = min(i + 1, len(self._times) - 1)
            k = i if j > i else i - 1
            self._vertical_speed.append((self._altitude[j] - self._altitude[k]) / self.step_s)

    # -- trajectory --------------------------------------------------------

    def _wind_at(self, altitude_m: float, t: float) -> tuple[float, float]:
        """Wind as (speed m/s, bearing the platform is carried towards).

        Speed peaks in the jet stream around 11 km. The bearing swings through
        the flight, which is what curves the ground track instead of drawing a
        straight line east.
        """
        jet = math.exp(-(((altitude_m - 11_000.0) / 6_000.0) ** 2))
        speed = 4.0 + 34.0 * jet + 0.00035 * altitude_m
        bearing = (
            80.0
            + 45.0 * math.sin(math.pi * altitude_m / BURST_ALTITUDE_M)
            + 9.0 * math.sin(2 * math.pi * t / 2400.0 + self._wind_phase)
        )
        return speed, bearing

    def _build_trajectory(self, start_lat: float, start_lon: float) -> None:
        self._latitude = [start_lat]
        self._longitude = [start_lon]
        self._speed = []
        self._heading = []

        lat, lon = start_lat, start_lon
        for i, t in enumerate(self._times):
            speed, bearing = self._wind_at(self._altitude[i], t)
            east = speed * math.sin(math.radians(bearing))
            north = speed * math.cos(math.radians(bearing))

            self._speed.append(speed)
            self._heading.append(bearing % 360.0)

            if i + 1 < len(self._times):
                lat += north * self.step_s / METERS_PER_DEGREE_LAT
                lon += east * self.step_s / (METERS_PER_DEGREE_LAT * math.cos(math.radians(lat)))
                self._latitude.append(lat)
                self._longitude.append(lon)

    # -- GNSS quality ------------------------------------------------------

    def _gnss_quality(self, t: float, altitude_m: float) -> tuple[int, int, float]:
        wobble = math.sin(2 * math.pi * t / 610.0 + self._gnss_phase)
        satellites = int(round(12 + 2 * wobble))
        # Fewer usable satellites once the antenna is above most of the
        # constellation's intended service volume.
        if altitude_m > 20_000.0:
            satellites -= 2
        satellites = max(4, satellites)
        hdop = round(0.7 + 0.35 * abs(wobble) + (0.3 if altitude_m > 25_000.0 else 0.0), 2)
        fix_quality = 4 if satellites >= 6 else 1
        return fix_quality, satellites, hdop

    # -- public ------------------------------------------------------------

    def phase_at(self, t: float) -> Phase:
        if t < self.burst_time_s:
            return Phase.ASCENT
        if t < self.burst_time_s + self.step_s:
            return Phase.BURST
        if t < self.descent_end_s:
            return Phase.DESCENT
        return Phase.LANDING

    def state_at(self, t: float) -> FlightState:
        t = max(0.0, min(t, self.duration_s))
        altitude = _interpolate(self._times, self._altitude, t)
        index = min(int(t / self.step_s), len(self._times) - 1)
        fix_quality, satellites, hdop = self._gnss_quality(t, altitude)
        return FlightState(
            mission_time_s=t,
            phase=self.phase_at(t),
            altitude_m=altitude,
            latitude=_interpolate(self._times, self._latitude, t),
            longitude=_interpolate(self._times, self._longitude, t),
            speed_ms=_interpolate(self._times, self._speed, t),
            # Heading is taken from the nearest sample: interpolating degrees
            # across the 359 to 1 wrap would invent a spin.
            heading_deg=self._heading[index],
            vertical_speed_ms=_interpolate(self._times, self._vertical_speed, t),
            fix_quality=fix_quality,
            satellites=satellites,
            hdop=hdop,
        )


def _interpolate_irregular(xs: list[float], ys: list[float], x: float) -> float:
    """Linear interpolation on a table with arbitrary spacing."""
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    i = min(bisect_right(xs, x) - 1, len(xs) - 2)
    span = xs[i + 1] - xs[i]
    if span <= 0.0:
        return ys[i]
    weight = (x - xs[i]) / span
    return ys[i] * (1.0 - weight) + ys[i + 1] * weight


def _interpolate(xs: list[float], ys: list[float], x: float) -> float:
    """Linear interpolation on an evenly spaced table."""
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    step = xs[1] - xs[0]
    i = int((x - xs[0]) / step)
    i = min(i, len(xs) - 2)
    weight = (x - xs[i]) / step
    return ys[i] * (1.0 - weight) + ys[i + 1] * weight
