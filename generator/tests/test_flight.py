"""The flight profile has to match the table in docs/data-model.md."""

from __future__ import annotations

import math

import pytest
from flight import BURST_ALTITUDE_M, LANDING_START_ALTITUDE_M, FlightProfile, Phase

DURATION_S = 9000.0


@pytest.fixture(scope="module")
def profile() -> FlightProfile:
    return FlightProfile(duration_s=DURATION_S, seed=20260812)


def test_starts_on_the_ground(profile: FlightProfile) -> None:
    assert profile.state_at(0.0).altitude_m == pytest.approx(0.0, abs=1.0)


def test_burst_happens_at_ninety_minutes_and_thirty_kilometres(profile: FlightProfile) -> None:
    assert profile.burst_time_s == pytest.approx(90 * 60)
    assert profile.state_at(profile.burst_time_s).altitude_m == pytest.approx(BURST_ALTITUDE_M, rel=1e-6)


def test_ascent_is_monotonic(profile: FlightProfile) -> None:
    altitudes = [profile.state_at(t).altitude_m for t in range(0, int(profile.burst_time_s), 30)]
    assert all(b > a for a, b in zip(altitudes, altitudes[1:]))


def test_ascent_rate_is_roughly_five_metres_per_second(profile: FlightProfile) -> None:
    rates = [profile.state_at(t).vertical_speed_ms for t in range(60, int(profile.burst_time_s) - 60, 30)]
    average = sum(rates) / len(rates)
    assert 4.5 < average < 6.5
    # "with variation": the rate must not be a flat line.
    assert max(rates) - min(rates) > 1.0


def test_descent_is_monotonic_and_ends_at_landing_altitude(profile: FlightProfile) -> None:
    start = int(profile.burst_time_s) + 5
    altitudes = [profile.state_at(t).altitude_m for t in range(start, int(profile.descent_end_s), 30)]
    assert all(b < a for a, b in zip(altitudes, altitudes[1:]))
    assert profile.state_at(profile.descent_end_s).altitude_m == pytest.approx(
        LANDING_START_ALTITUDE_M, rel=0.02
    )


def test_descent_slows_as_air_thickens(profile: FlightProfile) -> None:
    just_after_burst = abs(profile.state_at(profile.burst_time_s + 120).vertical_speed_ms)
    near_the_ground = abs(profile.state_at(profile.descent_end_s - 120).vertical_speed_ms)
    assert just_after_burst > 4 * near_the_ground


def test_burst_reverses_vertical_speed(profile: FlightProfile) -> None:
    assert profile.state_at(profile.burst_time_s - 60).vertical_speed_ms > 0
    assert profile.state_at(profile.burst_time_s + 60).vertical_speed_ms < 0


def test_lands(profile: FlightProfile) -> None:
    assert profile.state_at(DURATION_S).altitude_m == pytest.approx(0.0, abs=1.0)


def test_phases_cover_the_flight(profile: FlightProfile) -> None:
    assert profile.phase_at(0.0) is Phase.ASCENT
    assert profile.phase_at(profile.burst_time_s) is Phase.BURST
    assert profile.phase_at(profile.burst_time_s + 60) is Phase.DESCENT
    assert profile.phase_at(profile.descent_end_s + 60) is Phase.LANDING


def test_track_drifts_east_and_curves(profile: FlightProfile) -> None:
    start = profile.state_at(0.0)
    end = profile.state_at(DURATION_S)
    assert end.longitude > start.longitude

    headings = [profile.state_at(t).heading_deg for t in range(0, int(DURATION_S), 300)]
    # A straight line east would hold one bearing; the wind rotation has to
    # bend the track, which is what requirement 1 puts on the map.
    assert max(headings) - min(headings) > 20.0


def test_heading_stays_in_range(profile: FlightProfile) -> None:
    for t in range(0, int(DURATION_S), 120):
        assert 0.0 <= profile.state_at(t).heading_deg < 360.0


def test_gnss_quality_is_plausible(profile: FlightProfile) -> None:
    for t in range(0, int(DURATION_S), 300):
        state = profile.state_at(t)
        assert 4 <= state.satellites <= 16
        assert 0.5 <= state.hdop <= 2.0
        assert state.fix_quality in (1, 4)


def test_same_seed_gives_the_same_flight() -> None:
    a = FlightProfile(duration_s=DURATION_S, seed=7)
    b = FlightProfile(duration_s=DURATION_S, seed=7)
    c = FlightProfile(duration_s=DURATION_S, seed=8)
    assert a.state_at(3000.0) == b.state_at(3000.0)
    assert a.state_at(3000.0) != c.state_at(3000.0)


def test_shorter_duration_still_has_every_phase() -> None:
    short = FlightProfile(duration_s=600.0, seed=1)
    assert short.state_at(short.burst_time_s).altitude_m == pytest.approx(BURST_ALTITUDE_M, rel=1e-6)
    assert short.phase_at(short.duration_s - 1) is Phase.LANDING
    assert not math.isnan(short.state_at(short.duration_s).altitude_m)
