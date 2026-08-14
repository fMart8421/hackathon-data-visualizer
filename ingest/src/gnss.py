"""GNSS sources: the RTK NMEA exports and the u-blox survey-in captures.

Two formats, two very different provenance stories:

  data/export/<session>/*.csv   NMEA sentences that MATLAB could read and
                                Python could not, converted by
                                ingest/matlab/export_rtk.m. Real UTC times.
  data/GNSSprecision/*/*.mat    u-blox UBX-NAV-SVIN survey-in records: mean
                                ECEF position, accuracy, and how long the
                                survey has been running.

Nothing in either dataset moves. Every capture is a static occupation, so
requirement 1 is a precision and convergence story rather than a track. The
only real route in the whole project is the geo-referenced particle runs
loaded in phase 3.

Only GGA, GLL, GST and RMC carry a UTC column in the export. GSA and GSV do
not, so per-epoch DOP and satellites-in-view cannot be turned into a series;
satellite count comes from GGA instead.
"""

from __future__ import annotations

import csv
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

from sources import Reading, Sample, Source

# WGS84
_A = 6378137.0
_F = 1.0 / 298.257223563
_B = _A * (1.0 - _F)
_E2 = _F * (2.0 - _F)
_EP2 = (_A * _A - _B * _B) / (_B * _B)

# NMEA writes 99.99 when a DOP value is unavailable. Stored as a number it
# would tower over the real values, which sit under 1.
DOP_UNAVAILABLE = 99.99

# UBX-NAV-SVIN reports mean accuracy in units of 0.1 mm.
SVIN_ACCURACY_TO_M = 1e-4


def ecef_to_geodetic(x: float, y: float, z: float) -> tuple[float, float, float]:
    """ECEF metres to WGS84 latitude, longitude and ellipsoidal height.

    Bowring's closed form, accurate to well under a millimetre for anything
    near the surface, which matters here: the whole point of this dataset is
    centimetre-scale scatter.
    """
    p = math.hypot(x, y)
    if p < 1e-9:
        latitude = math.copysign(math.pi / 2.0, z)
        return math.degrees(latitude), 0.0, abs(z) - _B

    theta = math.atan2(z * _A, p * _B)
    longitude = math.atan2(y, x)
    latitude = math.atan2(
        z + _EP2 * _B * math.sin(theta) ** 3,
        p - _E2 * _A * math.cos(theta) ** 3,
    )
    n = _A / math.sqrt(1.0 - _E2 * math.sin(latitude) ** 2)
    altitude = p / math.cos(latitude) - n
    return math.degrees(latitude), math.degrees(longitude), altitude


def geodetic_to_ecef(latitude: float, longitude: float, altitude: float) -> tuple[float, float, float]:
    """Inverse of ecef_to_geodetic. Used to prove the round trip in tests."""
    lat, lon = math.radians(latitude), math.radians(longitude)
    n = _A / math.sqrt(1.0 - _E2 * math.sin(lat) ** 2)
    return (
        (n + altitude) * math.cos(lat) * math.cos(lon),
        (n + altitude) * math.cos(lat) * math.sin(lon),
        (n * (1.0 - _E2) + altitude) * math.sin(lat),
    )


def _float(text: str | None) -> float | None:
    if text is None:
        return None
    text = text.strip()
    if not text or text.upper() == "NAN":
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    return None if value != value else value


def _int(text: str | None) -> int | None:
    value = _float(text)
    return None if value is None else int(value)


# --- RTK NMEA export ------------------------------------------------------

_CAPTURE_STAMP = re.compile(r"(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})-(\d{2})")


def _capture_start(source_file: str) -> datetime | None:
    """Recover the capture start from a name like
    Data_1_Base_2025-11-25_14-40-22_Taveiro.mat."""
    match = _CAPTURE_STAMP.search(source_file)
    if not match:
        return None
    y, mo, d, h, mi, s = (int(g) for g in match.groups())
    return datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc)


def _utc_time_on(day: datetime, utc_time: str) -> datetime | None:
    """Combine a GGA time-of-day with the date of its capture.

    GGA carries no date. RMC does, but the position fields live in GGA, so the
    date comes from the capture filename instead. A capture that crosses
    midnight would otherwise jump back twelve hours, so a large negative gap is
    read as the following day.
    """
    parts = utc_time.strip().split(":")
    if len(parts) != 3:
        return None
    try:
        hour, minute = int(parts[0]), int(parts[1])
        second = float(parts[2])
    except ValueError:
        return None

    stamped = day.replace(hour=hour, minute=minute, second=int(second), microsecond=0) + timedelta(
        milliseconds=round((second - int(second)) * 1000)
    )
    if (stamped - day).total_seconds() < -43200:
        stamped += timedelta(days=1)
    return stamped


def _read_gst(path: Path) -> dict[tuple[str, str], list[Reading]]:
    """Position error estimates, keyed by capture and UTC time.

    GST is 1 Hz and aligned with GGA, so the two are matched on their timestamp
    rather than joined at query time.
    """
    if not path.is_file():
        return {}

    errors: dict[tuple[str, str], list[Reading]] = {}
    with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
        for row in csv.DictReader(handle):
            key = (row.get("source_file", ""), row.get("UTCTime", "").strip())
            readings = []
            for column, metric in (
                ("StdDeviationLatitudeError", "position_error_lat"),
                ("StdDeviationLongitudeError", "position_error_lon"),
                ("StdDeviationAltitudeError", "position_error_alt"),
            ):
                value = _float(row.get(column))
                if value is not None:
                    readings.append(Reading("gnss_receiver", metric, value))
            if readings:
                errors[key] = readings
    return errors


def _rtk_samples(gga_path: Path, gst_path: Path) -> Iterator[Sample]:
    errors = _read_gst(gst_path)
    index = 0

    with gga_path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
        for row in csv.DictReader(handle):
            latitude = _float(row.get("Latitude"))
            longitude = _float(row.get("Longitude"))
            if latitude is None or longitude is None:
                continue

            source_file = row.get("source_file", "")
            day = _capture_start(source_file)
            if day is None:
                continue
            event_time = _utc_time_on(day, row.get("UTCTime", ""))
            if event_time is None:
                continue

            hdop = _float(row.get("HDOP"))
            if hdop is not None and hdop >= DOP_UNAVAILABLE:
                hdop = None

            satellites = _int(row.get("NumSatellitesInUse"))
            readings = list(errors.get((source_file, row.get("UTCTime", "").strip()), []))
            if satellites is not None:
                readings.append(Reading("gnss_receiver", "satellites_in_use", float(satellites)))

            yield Sample(
                index=index,
                event_time=event_time,
                readings=readings,
                latitude=latitude,
                longitude=longitude,
                altitude_m=_float(row.get("Altitude")),
                fix_quality=_int(row.get("QualityIndicator")),
                satellites=satellites,
                hdop=hdop,
            )
            index += 1


# Receiver 1 is the base, receiver 2 the rover. They sit about 19 m apart, so
# merging them would produce a position stream that teleports between two
# points; each gets its own mission and device.
RECEIVER_ROLES = {"1": ("base", "rtk-base"), "2": ("rover", "rtk-rover")}


def rtk_source(gga_path: Path, session: str, receiver: str) -> Source | None:
    role, device_id = RECEIVER_ROLES.get(receiver, (None, None))
    if role is None:
        return None

    gst_path = gga_path.with_name(f"gstDataValid{receiver}.csv")
    started = _first_capture_start(gga_path) or datetime(2025, 11, 1, tzinfo=timezone.utc)

    return Source(
        mission_id=f"rtk-{session.lower()}-{role}",
        mission_name=f"RTK {session} {role}",
        device_id=device_id,
        started_at=started,
        description=(
            f"Ingested from data/export/{session}/{gga_path.name}, exported from the MATLAB NMEA "
            f"captures by ingest/matlab/export_rtk.m. Receiver {receiver} is the {role}. Real UTC "
            "timestamps, from the GGA time of day combined with the date in each capture filename. "
            "HDOP of 99.99 means unavailable in NMEA and is stored as null. The receiver is static: "
            "this is a precision measurement, not a track."
        ),
        files=[gga_path],
        samples=_rtk_samples(gga_path, gst_path),
    )


def _first_capture_start(gga_path: Path) -> datetime | None:
    with gga_path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
        for row in csv.DictReader(handle):
            start = _capture_start(row.get("source_file", ""))
            if start:
                return start
    return None


# --- u-blox survey-in -----------------------------------------------------

_MAT_CREATED = re.compile(rb"Created on: (.+?)\s*$")
_FILENAME_STAMP = re.compile(r"(\d{4})(\d{2})(\d{2})[_-](\d{2})(\d{2})(\d{2})")


def _mat_created_at(header: bytes) -> datetime | None:
    match = _MAT_CREATED.search(header.strip())
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1).decode().strip(), "%a %b %d %H:%M:%S %Y").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return None


def _survey_samples(path: Path, started_at: datetime) -> Iterator[Sample]:
    from scipy.io import loadmat    # imported lazily: only this source needs it

    mat = loadmat(path)
    x = mat["Xpos"].ravel()
    y = mat["Ypos"].ravel()
    z = mat["Zpos"].ravel()
    duration = mat["Dur"].ravel()
    accuracy = mat["MeanAcc"].ravel()

    first_duration = float(duration[0]) if duration.size else 0.0
    index = 0

    for i in range(x.size):
        # Records written before the receiver had a position at all. Converting
        # (0, 0, 0) would put the antenna at the centre of the Earth.
        if abs(x[i]) < 1000.0 and abs(y[i]) < 1000.0 and abs(z[i]) < 1000.0:
            continue

        latitude, longitude, altitude = ecef_to_geodetic(float(x[i]), float(y[i]), float(z[i]))
        event_time = started_at + timedelta(seconds=float(duration[i]) - first_duration)

        yield Sample(
            index=index,
            event_time=event_time,
            readings=[
                Reading("gnss_receiver", "position_accuracy", float(accuracy[i]) * SVIN_ACCURACY_TO_M)
            ],
            latitude=latitude,
            longitude=longitude,
            altitude_m=altitude,
        )
        index += 1


def survey_source(path: Path) -> Source:
    """One mission per survey-in capture.

    Start time comes from the filename where the capture has one. Where it does
    not, it is estimated as the MATLAB file's creation time minus the survey
    duration, which is an assumption and is recorded as such.
    """
    from scipy.io import loadmat

    stamp = _FILENAME_STAMP.search(path.name)
    estimated = False

    if stamp:
        y, mo, d, h, mi, s = (int(g) for g in stamp.groups())
        started = datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc)
    else:
        mat = loadmat(path)
        duration = mat["Dur"].ravel()
        span = float(duration.max() - duration.min()) if duration.size else 0.0
        created = _mat_created_at(mat.get("__header__", b"")) or datetime(2026, 4, 1, tzinfo=timezone.utc)
        started = created - timedelta(seconds=span)
        estimated = True

    return Source(
        mission_id=f"survey-{path.parent.name.lower()}-{path.stem.lower()}",
        mission_name=f"GNSS survey-in {path.parent.name}/{path.stem}",
        device_id="gnss-survey",
        started_at=started,
        description=(
            f"Ingested from data/GNSSprecision/{path.parent.name}/{path.name}. u-blox UBX-NAV-SVIN "
            "survey-in: mean ECEF position converted to WGS84, with mean accuracy in metres. "
            "event_time advances by the record's own survey duration, not by wall clock. "
            + (
                "Start time is ESTIMATED as the MATLAB file's creation time minus the survey "
                "duration, because the filename carries no date."
                if estimated
                else "Start time comes from the capture filename."
            )
            + " Records with no position fix yet are skipped. Valid is 0 throughout every capture: "
            "no survey reached the receiver's validity threshold."
        ),
        files=[path],
        samples=_survey_samples(path, started),
    )


# --- discovery ------------------------------------------------------------

# Curated per DEC-19: Data1's captures are the only ones whose filenames carry
# a date, and Data0/File12 is the long convergence run worth showing.
SURVEY_SELECTION = ("Data1/*.mat", "Data0/File12.mat")


def discover_gnss(data_root: Path, export_root: Path | None = None) -> list[Source]:
    sources: list[Source] = []

    export_root = export_root or (data_root / "export")
    if export_root.is_dir():
        for session_dir in sorted(p for p in export_root.iterdir() if p.is_dir()):
            for gga_path in sorted(session_dir.glob("ggaDataValid*.csv")):
                receiver = gga_path.stem.replace("ggaDataValid", "")
                source = rtk_source(gga_path, session_dir.name, receiver)
                if source is not None:
                    sources.append(source)

    precision_root = data_root / "GNSSprecision"
    if precision_root.is_dir():
        for pattern in SURVEY_SELECTION:
            for path in sorted(precision_root.glob(pattern)):
                sources.append(survey_source(path))

    return sources
