"""GNSS parsing and the ECEF conversion."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from gnss import (
    DOP_UNAVAILABLE,
    discover_gnss,
    ecef_to_geodetic,
    geodetic_to_ecef,
    rtk_source,
)

# Taveiro, from the survey-in captures.
TAVEIRO_ECEF = (4824914.2313, -721944.4166, 4094836.6798)


# --- coordinate conversion ------------------------------------------------


def test_ecef_lands_at_taveiro() -> None:
    latitude, longitude, altitude = ecef_to_geodetic(*TAVEIRO_ECEF)
    assert latitude == pytest.approx(40.1978, abs=0.01)
    assert longitude == pytest.approx(-8.5099, abs=0.01)
    # Ellipsoidal height: about 49 m of geoid separation above the ~41 m
    # orthometric height the NMEA captures report.
    assert 60.0 < altitude < 120.0


def test_ecef_round_trips_to_the_millimetre() -> None:
    latitude, longitude, altitude = ecef_to_geodetic(*TAVEIRO_ECEF)
    x, y, z = geodetic_to_ecef(latitude, longitude, altitude)
    for produced, original in zip((x, y, z), TAVEIRO_ECEF):
        assert produced == pytest.approx(original, abs=1e-3)


def test_ecef_preserves_centimetre_scatter() -> None:
    """The whole dataset is centimetre-scale, so the conversion must not blur it."""
    base = ecef_to_geodetic(*TAVEIRO_ECEF)
    shifted = ecef_to_geodetic(TAVEIRO_ECEF[0] + 0.10, TAVEIRO_ECEF[1], TAVEIRO_ECEF[2])
    moved_m = abs(shifted[0] - base[0]) * 111320
    assert 0.01 < moved_m < 0.15


def test_ecef_handles_the_pole_without_dividing_by_zero() -> None:
    latitude, _longitude, _altitude = ecef_to_geodetic(0.0, 0.0, 6356752.314)
    assert latitude == pytest.approx(90.0, abs=0.01)


# --- RTK export -----------------------------------------------------------

GGA_CSV = """TalkerID,MessageID,UTCTime,Latitude,Longitude,QualityIndicator,NumSatellitesInUse,HDOP,Altitude,GeoidSeparation,AgeOfDifferentialData,DifferentialReferenceStationID,Status,source_file,session
GN,GGA,14:40:23.000,40.1976482,-8.5098275,4,12,0.56,40.777,49.432,0.6,1,0,Data_2_Rove_2025-11-25_14-40-22_Taveiro.mat,RTK_25Nov
GN,GGA,14:40:24.500,40.1976483,-8.5098276,4,12,0.54,40.780,49.432,0.4,1,0,Data_2_Rove_2025-11-25_14-40-22_Taveiro.mat,RTK_25Nov
GN,GGA,14:40:25.000,40.1976484,-8.5098277,1,12,99.99,40.802,49.432,NaN,NaN,0,Data_2_Rove_2025-11-25_14-40-22_Taveiro.mat,RTK_25Nov
"""

GST_CSV = """TalkerID,MessageID,UTCTime,RMSStdDeviationOfRanges,StdDeviationSemiMajorAxis,StdDeviationSemiMinorAxis,OrientationSemiMajorAxis,StdDeviationLatitudeError,StdDeviationLongitudeError,StdDeviationAltitudeError,Status,source_file,session
GN,GST,14:40:23.000,16,1.2,1,5.1,0.010,0.010,0.010,0,Data_2_Rove_2025-11-25_14-40-22_Taveiro.mat,RTK_25Nov
"""


@pytest.fixture()
def rtk_session(tmp_path: Path) -> Path:
    session = tmp_path / "RTK_25Nov"
    session.mkdir()
    (session / "ggaDataValid2.csv").write_text(GGA_CSV, encoding="utf-8")
    (session / "gstDataValid2.csv").write_text(GST_CSV, encoding="utf-8")
    return session


def test_rtk_builds_positions_with_real_timestamps(rtk_session: Path) -> None:
    source = rtk_source(rtk_session / "ggaDataValid2.csv", "RTK_25Nov", "2")
    samples = list(source.samples)
    assert len(samples) == 3
    # Date from the capture filename, time of day from GGA.
    assert samples[0].event_time == datetime(2025, 11, 25, 14, 40, 23, tzinfo=timezone.utc)
    assert samples[1].event_time == datetime(2025, 11, 25, 14, 40, 24, 500000, tzinfo=timezone.utc)


def test_rtk_keeps_fix_quality_and_satellites(rtk_session: Path) -> None:
    samples = list(rtk_source(rtk_session / "ggaDataValid2.csv", "RTK_25Nov", "2").samples)
    assert samples[0].fix_quality == 4     # RTK fixed
    assert samples[2].fix_quality == 1     # standalone
    assert samples[0].satellites == 12


def test_unavailable_hdop_becomes_null(rtk_session: Path) -> None:
    samples = list(rtk_source(rtk_session / "ggaDataValid2.csv", "RTK_25Nov", "2").samples)
    assert samples[0].hdop == 0.56
    # 99.99 is the NMEA "not available" sentinel, not a reading.
    assert samples[2].hdop is None
    assert DOP_UNAVAILABLE == 99.99


def test_gst_errors_are_matched_to_their_epoch(rtk_session: Path) -> None:
    samples = list(rtk_source(rtk_session / "ggaDataValid2.csv", "RTK_25Nov", "2").samples)
    first = {r.metric_key: r.value for r in samples[0].readings}
    assert first["position_error_lat"] == 0.010
    assert first["position_error_alt"] == 0.010
    assert first["satellites_in_use"] == 12
    # The epoch with no GST row still reports its satellite count.
    third = {r.metric_key for r in samples[2].readings}
    assert third == {"satellites_in_use"}


def test_receiver_number_decides_base_or_rover(rtk_session: Path) -> None:
    rover = rtk_source(rtk_session / "ggaDataValid2.csv", "RTK_25Nov", "2")
    assert rover.device_id == "rtk-rover"
    assert rover.mission_id == "rtk-rtk_25nov-rover"

    (rtk_session / "ggaDataValid1.csv").write_text(GGA_CSV, encoding="utf-8")
    base = rtk_source(rtk_session / "ggaDataValid1.csv", "RTK_25Nov", "1")
    assert base.device_id == "rtk-base"
    assert base.mission_id == "rtk-rtk_25nov-base"


def test_unknown_receiver_number_is_ignored(rtk_session: Path) -> None:
    assert rtk_source(rtk_session / "ggaDataValid2.csv", "RTK_25Nov", "7") is None


def test_rows_without_a_parseable_capture_date_are_skipped(tmp_path: Path) -> None:
    session = tmp_path / "RTK_odd"
    session.mkdir()
    broken = GGA_CSV.replace("Data_2_Rove_2025-11-25_14-40-22_Taveiro.mat", "no_date_here.mat")
    (session / "ggaDataValid2.csv").write_text(broken, encoding="utf-8")
    assert list(rtk_source(session / "ggaDataValid2.csv", "RTK_odd", "2").samples) == []


def test_discovery_finds_both_receivers(tmp_path: Path) -> None:
    export = tmp_path / "export" / "RTK_25Nov"
    export.mkdir(parents=True)
    (export / "ggaDataValid1.csv").write_text(GGA_CSV, encoding="utf-8")
    (export / "ggaDataValid2.csv").write_text(GGA_CSV, encoding="utf-8")

    missions = {s.mission_id for s in discover_gnss(tmp_path, tmp_path / "export")}
    assert missions == {"rtk-rtk_25nov-base", "rtk-rtk_25nov-rover"}


def test_discovery_survives_a_missing_export_folder(tmp_path: Path) -> None:
    assert discover_gnss(tmp_path, tmp_path / "nothing_here") == []


def test_static_capture_is_described_as_a_precision_measurement(rtk_session: Path) -> None:
    source = rtk_source(rtk_session / "ggaDataValid2.csv", "RTK_25Nov", "2")
    assert "not a track" in source.description
