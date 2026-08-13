"""Sensor models against the behaviour column of the quantities table."""

from __future__ import annotations

import pytest
from sensors import (
    air_temperature_c,
    build_scalar_sensors,
    isa_density,
    isa_pressure_pa,
    read_scalars,
    relative_humidity_pct,
)

SEED = 20260812


# --- atmosphere -----------------------------------------------------------


def test_temperature_falls_to_the_tropopause_then_inverts() -> None:
    assert air_temperature_c(0.0) == pytest.approx(15.0, abs=0.1)
    # "drops to about -55 C at the 11 km tropopause"
    assert air_temperature_c(11_000.0) == pytest.approx(-56.5, abs=0.5)
    # isothermal band, then the stratospheric rise
    assert air_temperature_c(15_000.0) == pytest.approx(-56.5, abs=0.5)
    assert air_temperature_c(30_000.0) > air_temperature_c(20_000.0)
    assert air_temperature_c(30_000.0) == pytest.approx(-46.5, abs=0.5)


def test_pressure_decays_from_sea_level_to_about_ten_hectopascal() -> None:
    assert isa_pressure_pa(0.0) == pytest.approx(101_325.0, rel=1e-6)
    assert isa_pressure_pa(11_000.0) == pytest.approx(22_632.0, rel=0.01)
    # "1013 hPa to about 10 hPa"
    assert 900.0 < isa_pressure_pa(30_000.0) < 1400.0


def test_pressure_is_strictly_decreasing() -> None:
    pressures = [isa_pressure_pa(h) for h in range(0, 31_000, 500)]
    assert all(b < a for a, b in zip(pressures, pressures[1:]))


def test_density_thins_with_altitude() -> None:
    assert isa_density(0.0) == pytest.approx(1.225, rel=0.01)
    assert isa_density(30_000.0) < isa_density(0.0) / 50


def test_humidity_is_high_in_cloud_and_dry_aloft() -> None:
    # "high in clouds below 3 km"
    assert relative_humidity_pct(2_000.0, 0.0) > 60.0
    # "near zero above 10 km"
    assert relative_humidity_pct(12_000.0, 0.0) < 3.0
    assert relative_humidity_pct(25_000.0, 0.0) < 1.0


def test_humidity_stays_within_physical_bounds() -> None:
    for h in range(0, 30_000, 250):
        value = relative_humidity_pct(float(h), 1234.0)
        assert 0.0 <= value <= 100.0


# --- noise, drift, calibration -------------------------------------------


def test_raw_and_calibrated_differ_but_track_each_other() -> None:
    sensors = build_scalar_sensors(SEED)
    readings = read_scalars(sensors, 5_000.0, 600.0, 600)
    assert {r.metric_key for r in readings} == {"air_temperature", "pressure", "relative_humidity"}

    for reading in readings:
        assert reading.value != reading.value_raw

    temperature = next(r for r in readings if r.metric_key == "air_temperature")
    truth = air_temperature_c(5_000.0)
    # Calibrated must be closer to truth than raw: that is what DEC-05 buys.
    assert abs(temperature.value - truth) < abs(temperature.value_raw - truth)


def test_readings_stay_inside_the_catalog_valid_range() -> None:
    sensors = build_scalar_sensors(SEED)
    bounds = {
        "air_temperature": (-90.0, 60.0),
        "pressure": (100.0, 110_000.0),
        "relative_humidity": (0.0, 100.0),
    }
    for index, altitude in enumerate(range(0, 30_000, 100)):
        for reading in read_scalars(sensors, float(altitude), float(index), index):
            low, high = bounds[reading.metric_key]
            assert low <= reading.value <= high
            assert low <= reading.value_raw <= high


def test_noise_does_not_depend_on_read_order() -> None:
    sensors = build_scalar_sensors(SEED)
    first = read_scalars(sensors, 8_000.0, 900.0, 900)
    read_scalars(sensors, 1_000.0, 100.0, 100)  # a read in between
    second = read_scalars(sensors, 8_000.0, 900.0, 900)
    assert first == second


def test_pressure_noise_scales_with_the_reading() -> None:
    sensors = build_scalar_sensors(SEED)
    pressure = next(s for s in sensors if s.metric_key == "pressure")

    at_sea_level = [abs(pressure.read(isa_pressure_pa(0.0), 0.0, i).value_raw - isa_pressure_pa(0.0)) for i in range(60)]
    aloft = [abs(pressure.read(isa_pressure_pa(30_000.0), 0.0, i).value_raw - isa_pressure_pa(30_000.0)) for i in range(60)]
    assert sum(at_sea_level) / 60 > sum(aloft) / 60


def test_drift_is_slow_relative_to_noise() -> None:
    sensors = build_scalar_sensors(SEED)
    temperature = next(s for s in sensors if s.metric_key == "air_temperature")
    # Over ten seconds the drift barely moves; that is what makes it drift.
    assert abs(temperature.drift_at(1000.0) - temperature.drift_at(1010.0)) < 0.2
