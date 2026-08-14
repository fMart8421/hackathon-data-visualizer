"""Synthetic supplement models, against DEC-18's rules and real physics."""

from __future__ import annotations

import math

import pytest
from route import RoutePoint, bearing_deg, distance_m, smooth
from supplement import (
    FIELD_TOTAL_UT,
    GRAVITY,
    HARD_IRON_UT,
    Imu,
    Magnetometer,
    UvSensor,
    clear_sky_uv_index,
    field_ned,
    solar_declination_deg,
    solar_elevation_deg,
)

TAVEIRO_LAT = 40.1968
TAVEIRO_LON = -8.5095
JUNE_28 = 179


# --- geomagnetic field ----------------------------------------------------


def test_field_matches_the_site_intensity() -> None:
    north, east, down = field_ned()
    assert math.sqrt(north**2 + east**2 + down**2) == pytest.approx(FIELD_TOTAL_UT, rel=1e-6)


def test_field_dips_steeply_at_this_latitude() -> None:
    north, east, down = field_ned()
    horizontal = math.hypot(north, east)
    # 55 degrees of inclination puts most of the field into the vertical.
    assert down > horizontal
    assert math.degrees(math.atan2(down, horizontal)) == pytest.approx(55.0, abs=0.1)


def test_magnitude_is_constant_whatever_the_heading() -> None:
    magnetometer = Magnetometer()
    magnitudes = [magnetometer.read(h, i).magnitude for i, h in enumerate(range(0, 360, 15))]
    # The platform turning cannot change how strong Earth's field is.
    assert max(magnitudes) - min(magnitudes) < 1.5


def test_components_rotate_with_heading() -> None:
    """Requirement 3's arrows come from this and nothing else."""
    magnetometer = Magnetometer()
    facing_north = magnetometer.read(0.0, 0)
    facing_east = magnetometer.read(90.0, 0)

    # Pointing north puts the horizontal field on the forward axis; turning
    # east swings it onto the right-hand axis.
    assert facing_north.vx > 20.0
    assert abs(facing_north.vy) < 5.0
    assert abs(facing_east.vx) < 5.0
    assert facing_east.vy < -20.0


def test_vertical_component_is_unaffected_by_turning() -> None:
    magnetometer = Magnetometer()
    assert magnetometer.read(0.0, 0).vz == pytest.approx(magnetometer.read(180.0, 0).vz, abs=1.0)


def test_raw_carries_the_hard_iron_offset_and_calibrated_does_not() -> None:
    magnetometer = Magnetometer()
    reading = magnetometer.read(45.0, 7)
    assert reading.vx_raw - reading.vx == pytest.approx(HARD_IRON_UT[0], abs=1e-6)
    assert reading.vy_raw - reading.vy == pytest.approx(HARD_IRON_UT[1], abs=1e-6)
    # The uncalibrated magnitude is wrong, which is the point of storing both.
    assert reading.magnitude_raw != reading.magnitude


def test_readings_stay_inside_the_catalog_range() -> None:
    magnetometer = Magnetometer()
    for i, heading in enumerate(range(0, 360, 5)):
        reading = magnetometer.read(float(heading), i)
        assert 0.0 <= reading.magnitude <= 100.0
        assert 0.0 <= reading.magnitude_raw <= 100.0


# --- solar and UV ---------------------------------------------------------


def test_declination_peaks_at_the_solstice() -> None:
    assert solar_declination_deg(172) == pytest.approx(23.4, abs=0.2)
    assert solar_declination_deg(355) == pytest.approx(-23.4, abs=0.5)


def test_sun_is_high_at_noon_and_below_the_horizon_at_night() -> None:
    noon = solar_elevation_deg(TAVEIRO_LAT, TAVEIRO_LON, JUNE_28, 12.6)
    midnight = solar_elevation_deg(TAVEIRO_LAT, TAVEIRO_LON, JUNE_28, 0.0)
    assert noon > 70.0
    assert midnight < 0.0


def test_solar_noon_is_shifted_by_longitude() -> None:
    """8.5 degrees west puts solar noon about 34 minutes after 12:00 UTC."""
    at_twelve = solar_elevation_deg(TAVEIRO_LAT, TAVEIRO_LON, JUNE_28, 12.0)
    at_shifted = solar_elevation_deg(TAVEIRO_LAT, TAVEIRO_LON, JUNE_28, 12.57)
    assert at_shifted > at_twelve


def test_uv_index_is_zero_in_the_dark() -> None:
    assert clear_sky_uv_index(-5.0) == 0.0
    sensor = UvSensor()
    assert sensor.read(TAVEIRO_LAT, TAVEIRO_LON, JUNE_28, 2.0, 0).value == 0.0


def test_uv_peaks_at_a_plausible_summer_value() -> None:
    sensor = UvSensor()
    peak = max(
        sensor.read(TAVEIRO_LAT, TAVEIRO_LON, JUNE_28, 12.0 + m / 60.0, m).value
        for m in range(0, 120)
    )
    # Late June at 40 N reaches about 9. Not 3, and not 15.
    assert 7.0 < peak < 11.0


def test_uv_stays_inside_the_catalog_range() -> None:
    sensor = UvSensor()
    for i in range(0, 24 * 12):
        value = sensor.read(TAVEIRO_LAT, TAVEIRO_LON, JUNE_28, i / 12.0, i).value
        assert 0.0 <= value <= 20.0


def test_uv_raw_sits_on_a_dark_offset() -> None:
    sensor = UvSensor()
    night = sensor.read(TAVEIRO_LAT, TAVEIRO_LON, JUNE_28, 1.0, 0)
    # A real photodiode reads something even in the dark; the calibrated value
    # does not.
    assert night.value == 0.0
    assert night.value_raw > 0.0


def test_uv_curve_rises_then_falls() -> None:
    sensor = UvSensor()
    morning = sensor.read(TAVEIRO_LAT, TAVEIRO_LON, JUNE_28, 8.0, 1).value
    noon = sensor.read(TAVEIRO_LAT, TAVEIRO_LON, JUNE_28, 12.5, 2).value
    evening = sensor.read(TAVEIRO_LAT, TAVEIRO_LON, JUNE_28, 18.0, 3).value
    assert morning < noon
    assert evening < noon


# --- IMU ------------------------------------------------------------------


def test_accelerometer_block_is_one_second_at_the_sample_rate() -> None:
    imu = Imu()
    block = imu.acceleration_block(0, speed_ms=5.0, yaw_rate_dps=0.0)
    assert len(block.samples_x) == 100
    assert len(block.samples_y) == 100
    assert len(block.samples_z) == 100


def test_gravity_sits_on_the_vertical_axis() -> None:
    imu = Imu()
    block = imu.acceleration_block(3, speed_ms=4.0, yaw_rate_dps=0.0)
    mean_z = sum(block.samples_z) / len(block.samples_z)
    mean_x = sum(block.samples_x) / len(block.samples_x)
    assert mean_z == pytest.approx(GRAVITY, abs=1.5)
    assert abs(mean_x) < 1.5


def test_turning_shows_up_as_lateral_acceleration() -> None:
    imu = Imu()
    straight = imu.acceleration_block(5, speed_ms=6.0, yaw_rate_dps=0.0)
    turning = imu.acceleration_block(5, speed_ms=6.0, yaw_rate_dps=40.0)
    mean_straight = sum(straight.samples_y) / len(straight.samples_y)
    mean_turning = sum(turning.samples_y) / len(turning.samples_y)
    assert mean_turning > mean_straight + 2.0


def test_gyro_yaw_follows_the_route_turn_rate() -> None:
    imu = Imu()
    block = imu.angular_rate_block(2, yaw_rate_dps=25.0)
    mean_z = sum(block.samples_z) / len(block.samples_z)
    assert mean_z == pytest.approx(25.0, abs=2.0)


def test_imu_stays_inside_the_catalog_range() -> None:
    imu = Imu()
    for second in range(0, 30):
        accel = imu.acceleration_block(second, speed_ms=12.0, yaw_rate_dps=60.0)
        gyro = imu.angular_rate_block(second, yaw_rate_dps=60.0)
        for axis in (accel.samples_x, accel.samples_y, accel.samples_z):
            assert all(-160.0 <= s <= 160.0 for s in axis)
        for axis in (gyro.samples_x, gyro.samples_y, gyro.samples_z):
            assert all(-2000.0 <= s <= 2000.0 for s in axis)


def test_same_seed_gives_the_same_block() -> None:
    a = Imu(seed=5).acceleration_block(1, 5.0, 0.0)
    b = Imu(seed=5).acceleration_block(1, 5.0, 0.0)
    c = Imu(seed=6).acceleration_block(1, 5.0, 0.0)
    assert a.samples_z == b.samples_z
    assert a.samples_z != c.samples_z


# --- route ----------------------------------------------------------------


def test_bearing_cardinal_directions() -> None:
    assert bearing_deg(40.0, -8.0, 41.0, -8.0) == pytest.approx(0.0, abs=0.5)
    assert bearing_deg(40.0, -8.0, 40.0, -7.0) == pytest.approx(90.0, abs=0.5)
    assert bearing_deg(40.0, -8.0, 39.0, -8.0) == pytest.approx(180.0, abs=0.5)


def test_distance_is_metres() -> None:
    # One minute of latitude is about 1852 m.
    assert distance_m(40.0, -8.0, 40.0 + 1 / 60, -8.0) == pytest.approx(1852, rel=0.01)


def test_smoothing_flattens_gps_jitter_without_moving_the_track() -> None:
    points = [
        RoutePoint(i, 40.0 + i * 1e-5, -8.0, 90.0, 5.0 if i % 2 else 11.0, 40.0 if i % 2 else -40.0)
        for i in range(11)
    ]
    smoothed = smooth(points, window=5)
    middle = smoothed[5]
    # Speed and turn rate settle; the coordinates are untouched, because the
    # positions are the one genuinely measured thing in this pipeline.
    assert 6.0 < middle.speed_ms < 10.0
    assert abs(middle.yaw_rate_dps) < 20.0
    assert middle.latitude == points[5].latitude
    assert middle.longitude == points[5].longitude
