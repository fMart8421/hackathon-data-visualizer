"""Per-metric sensor models: physical truth, then noise and drift.

Two numbers come out of every read (DEC-05):

  value_raw   what the part would report: truth + slow drift + noise
  value       what the platform publishes after compensating for the drift it
              believes it has, and after the light averaging a real driver does

Compensation is deliberately imperfect, so the two series track each other
without ever coinciding. That is what makes a "raw against calibrated" panel
worth looking at.

The atmosphere is the 1976 standard atmosphere up to 32 km, which is what
gives the tropopause kink and the stratospheric inversion the brief asks for.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

GRAVITY = 9.80665
AIR_GAS_CONSTANT = 287.05287

SEA_LEVEL_PRESSURE_PA = 101_325.0
SEA_LEVEL_TEMPERATURE_K = 288.15

TROPOPAUSE_M = 11_000.0
STRATOSPHERE_START_M = 20_000.0
TROPOPAUSE_TEMPERATURE_K = 216.65
TROPOPAUSE_PRESSURE_PA = 22_632.06
STRATOSPHERE_PRESSURE_PA = 5_474.89

KELVIN_OFFSET = 273.15


# --- atmosphere -----------------------------------------------------------


def isa_temperature_k(altitude_m: float) -> float:
    """Standard temperature. -6.5 K/km, isothermal, then +1 K/km inversion."""
    h = max(0.0, altitude_m)
    if h < TROPOPAUSE_M:
        return SEA_LEVEL_TEMPERATURE_K - 0.0065 * h
    if h < STRATOSPHERE_START_M:
        return TROPOPAUSE_TEMPERATURE_K
    return TROPOPAUSE_TEMPERATURE_K + 0.001 * (h - STRATOSPHERE_START_M)


def isa_pressure_pa(altitude_m: float) -> float:
    """Standard pressure, layer by layer."""
    h = max(0.0, altitude_m)
    if h < TROPOPAUSE_M:
        return SEA_LEVEL_PRESSURE_PA * (1.0 - 0.0065 * h / SEA_LEVEL_TEMPERATURE_K) ** 5.255877
    if h < STRATOSPHERE_START_M:
        return TROPOPAUSE_PRESSURE_PA * math.exp(
            -GRAVITY * (h - TROPOPAUSE_M) / (AIR_GAS_CONSTANT * TROPOPAUSE_TEMPERATURE_K)
        )
    temperature = isa_temperature_k(h)
    exponent = GRAVITY / (AIR_GAS_CONSTANT * 0.001)
    return STRATOSPHERE_PRESSURE_PA * (TROPOPAUSE_TEMPERATURE_K / temperature) ** exponent


def isa_density(altitude_m: float) -> float:
    return isa_pressure_pa(altitude_m) / (AIR_GAS_CONSTANT * isa_temperature_k(altitude_m))


def air_temperature_c(altitude_m: float) -> float:
    return isa_temperature_k(altitude_m) - KELVIN_OFFSET


def relative_humidity_pct(altitude_m: float, mission_time_s: float) -> float:
    """Damp near the ground, saturated inside the cloud deck, dry aloft.

    The cloud layer between roughly 1.2 and 2.8 km is what requirement 6 has to
    show as a gradient on the map, so it is modelled explicitly rather than
    falling out of a single decay curve.
    """
    h = max(0.0, altitude_m)
    surface = 68.0
    cloud = 30.0 * math.exp(-(((h - 2000.0) / 900.0) ** 2))
    decay = math.exp(-h / 3200.0)
    humidity = surface * decay + cloud
    # A thin moist layer higher up keeps the profile from looking synthetic.
    humidity += 6.0 * math.exp(-(((h - 5200.0) / 700.0) ** 2))
    if h > 10_000.0:
        humidity = min(humidity, 3.0 * math.exp(-(h - 10_000.0) / 4000.0))
    breathing = 1.0 + 0.02 * math.sin(mission_time_s / 220.0)
    return max(0.0, min(100.0, humidity * breathing))


# --- sensor model ---------------------------------------------------------


@dataclass(frozen=True)
class Reading:
    sensor_id: str
    metric_key: str
    value: float
    value_raw: float
    t_offset_ms: int


class ScalarSensor:
    """A scalar channel with its own drift, noise, and read offset.

    Noise is derived from the sample index rather than drawn from a running
    generator, so a given sample is the same number no matter what order the
    channels are read in.
    """

    def __init__(
        self,
        sensor_id: str,
        metric_key: str,
        noise_sigma: float,
        drift_amplitude: float,
        seed: int,
        t_offset_ms: int = 0,
        relative_noise: float = 0.0,
        relative_drift: float = 0.0,
        clamp: tuple[float, float] | None = None,
        decimals: int = 3,
    ) -> None:
        self.sensor_id = sensor_id
        self.metric_key = metric_key
        self.noise_sigma = noise_sigma
        self.drift_amplitude = drift_amplitude
        self.seed = seed
        self.t_offset_ms = t_offset_ms
        self.relative_noise = relative_noise
        self.relative_drift = relative_drift
        self.clamp = clamp
        self.decimals = decimals

        phases = random.Random(f"{seed}:{metric_key}:phase")
        self._drift_phase_1 = phases.uniform(0, 2 * math.pi)
        self._drift_phase_2 = phases.uniform(0, 2 * math.pi)

    def drift_at(self, mission_time_s: float, truth: float = 0.0) -> float:
        amplitude = self.drift_amplitude + self.relative_drift * abs(truth)
        return amplitude * (
            0.6 * math.sin(2 * math.pi * mission_time_s / 1700.0 + self._drift_phase_1)
            + 0.4 * math.sin(2 * math.pi * mission_time_s / 430.0 + self._drift_phase_2)
        )

    def _noise(self, sample_index: int) -> float:
        return random.Random(f"{self.seed}:{self.metric_key}:{sample_index}").gauss(0.0, 1.0)

    def read(self, truth: float, mission_time_s: float, sample_index: int) -> Reading:
        sigma = self.noise_sigma + self.relative_noise * abs(truth)
        noise = self._noise(sample_index) * sigma
        drift = self.drift_at(mission_time_s, truth)

        value_raw = truth + drift + noise
        # 90 % of the drift is compensated, and the driver's averaging removes
        # most of the noise. What is left is a small, honest residual.
        value = truth + 0.1 * drift + 0.25 * noise

        if self.clamp is not None:
            low, high = self.clamp
            value_raw = max(low, min(high, value_raw))
            value = max(low, min(high, value))

        return Reading(
            sensor_id=self.sensor_id,
            metric_key=self.metric_key,
            value=round(value, self.decimals),
            value_raw=round(value_raw, self.decimals),
            t_offset_ms=self.t_offset_ms,
        )


def build_scalar_sensors(seed: int) -> list[ScalarSensor]:
    """The three phase 2 scalars, all on the BME280 in the radiation shield.

    Offsets mimic reading the three registers in sequence over I2C.
    """
    return [
        ScalarSensor(
            sensor_id="bme280",
            metric_key="air_temperature",
            noise_sigma=0.35,
            drift_amplitude=0.8,
            seed=seed,
            t_offset_ms=0,
            clamp=(-90.0, 60.0),
            decimals=2,
        ),
        ScalarSensor(
            sensor_id="bme280",
            metric_key="pressure",
            noise_sigma=8.0,
            drift_amplitude=0.0,
            seed=seed,
            t_offset_ms=15,
            # Pressure noise and drift are proportional: 45 Pa of scatter is
            # nothing at sea level and absurd at 1000 Pa.
            relative_noise=0.0006,
            relative_drift=0.0015,
            clamp=(100.0, 110_000.0),
            decimals=1,
        ),
        ScalarSensor(
            sensor_id="bme280",
            metric_key="relative_humidity",
            noise_sigma=1.2,
            drift_amplitude=2.5,
            seed=seed,
            t_offset_ms=30,
            clamp=(0.0, 100.0),
            decimals=2,
        ),
    ]


def read_scalars(
    sensors: list[ScalarSensor], altitude_m: float, mission_time_s: float, sample_index: int
) -> list[Reading]:
    truths = {
        "air_temperature": air_temperature_c(altitude_m),
        "pressure": isa_pressure_pa(altitude_m),
        "relative_humidity": relative_humidity_pct(altitude_m, mission_time_s),
    }
    return [
        sensor.read(truths[sensor.metric_key], mission_time_s, sample_index)
        for sensor in sensors
        if sensor.metric_key in truths
    ]
