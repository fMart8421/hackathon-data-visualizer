"""Parsers, one per provided file format.

Each parser turns a file into a Source: mission metadata plus a stream of
Samples. Nothing here talks to the database; mapping a Sample onto the contract
and writing it is loader.py's job.

Three of the four formats record sample order and no timestamps, so their time
axis is synthesised from the date in the filename plus the sample index at an
assumed rate (OPEN-10). The assumed rate travels into mission.description, so
the axis can never be silently mistaken for measured time.

Encoding: these are Arduino serial captures written on a Windows box. Some are
UTF-8, one is doubly-encoded UTF-8, and the degree signs are mangled either
way. Only ASCII markers and numbers are parsed, so every file is read with
errors='replace' and the mangled headers are ignored.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Iterator

DEFAULT_RATE_HZ = 1.0


@dataclass(frozen=True)
class Reading:
    sensor_id: str
    metric_key: str
    value: float | None = None
    value_raw: float | None = None


@dataclass(frozen=True)
class Sample:
    index: int
    event_time: datetime
    readings: list[Reading]
    # Position, where the source has one. The particle runs carry a bare
    # coordinate; the GNSS sources fill in the rest.
    latitude: float | None = None
    longitude: float | None = None
    altitude_m: float | None = None
    speed_ms: float | None = None
    heading_deg: float | None = None
    fix_quality: int | None = None
    satellites: int | None = None
    hdop: float | None = None


@dataclass
class Source:
    mission_id: str
    mission_name: str
    device_id: str
    started_at: datetime
    description: str
    files: list[Path]
    samples: Iterator[Sample] = field(repr=False, default_factory=lambda: iter(()))


# --- helpers --------------------------------------------------------------

MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def date_from_name(name: str, fallback_year: int = 2025) -> datetime | None:
    """Recover a capture date from the filename.

    The provided files use at least four conventions:
      Akel_Alcohol3_20250728.txt          YYYYMMDD
      Akel_Alcohol3_20250819_164219.txt   YYYYMMDD_HHMMSS
      28June2025_Bike_Taveiro.txt         DDMonthYYYY
      24JuneCar0000.txt                   DDMonth, year implied
      2705teste1ISO5.txt                  DDMM, year implied
    """
    stamp = re.search(r"(\d{4})(\d{2})(\d{2})[_-](\d{2})(\d{2})(\d{2})", name)
    if stamp:
        y, mo, d, h, mi, s = (int(g) for g in stamp.groups())
        return datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc)

    ymd = re.search(r"(20\d{2})(\d{2})(\d{2})", name)
    if ymd:
        y, mo, d = (int(g) for g in ymd.groups())
        return datetime(y, mo, d, tzinfo=timezone.utc)

    named = re.search(r"(\d{1,2})(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*(20\d{2})?", name, re.I)
    if named:
        day = int(named.group(1))
        month = MONTHS[named.group(2).lower()[:3]]
        year = int(named.group(3)) if named.group(3) else fallback_year
        return datetime(year, month, day, tzinfo=timezone.utc)

    ddmm = re.match(r"^(\d{2})(\d{2})\D", name)
    if ddmm:
        day, month = int(ddmm.group(1)), int(ddmm.group(2))
        if 1 <= day <= 31 and 1 <= month <= 12:
            return datetime(fallback_year, month, day, tzinfo=timezone.utc)

    return None


def _read_lines(path: Path) -> Iterator[str]:
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            yield line.rstrip("\n").rstrip("\r")


def _to_float(text: str) -> float | None:
    try:
        value = float(text.strip())
    except (ValueError, AttributeError):
        return None
    # A few logged samples are literally "nan". Stored as a float, NaN poisons
    # every aggregate downstream: min, max and avg all come back NaN, and it
    # slips past range checks because every comparison with NaN is false.
    # It is an absent reading, so it becomes one.
    if value != value or value in (float("inf"), float("-inf")):
        return None
    return value


# --- Volatiles: wide CSV, real timestamps ---------------------------------

# Column order after the leading timestamp, from the header row the logger
# writes partway down the file.
VOLATILE_COLUMNS = [
    "time_ms", "mq4_raw", "mq4_DO", "mq4_CH4_ppm",
    "alco_raw", "alco_RS_ohm", "alco_CO_ppm", "alco_EtOH_ppm",
    "aq5_CO_raw", "aq5_CO_RS_ohm", "aq5_CO_ppm",
    "aq5_NH3_raw", "aq5_NH3_RS_ohm", "aq5_NH3_ppm",
    "aq5_NO2_raw", "aq5_NO2_RS_ohm", "aq5_NO2_ppm",
]


def _volatile_readings(row: dict[str, float | None]) -> list[Reading]:
    """Map one logged row onto catalog metrics.

    Each species carries the raw ADC count as value_raw and the firmware's ppm
    as value (DEC-05). Sensing element resistance is kept as its own metric:
    it is what a metal-oxide sensor physically produces, and it stays
    meaningful even where the ppm conversion is uncalibrated.
    """
    readings = [
        Reading("mq4", "ch4_concentration", row["mq4_CH4_ppm"], row["mq4_raw"]),
        Reading("mics5524", "co_concentration", row["alco_CO_ppm"], row["alco_raw"]),
        Reading("mics5524", "ethanol_concentration", row["alco_EtOH_ppm"], row["alco_raw"]),
        Reading("mics5524", "gas_sensor_resistance", row["alco_RS_ohm"], row["alco_raw"]),
    ]
    for channel, metric in (("CO", "co_concentration"), ("NH3", "nh3_concentration"), ("NO2", "no2_concentration")):
        sensor = f"mics6814_{channel.lower()}"
        readings.append(Reading(sensor, metric, row[f"aq5_{channel}_ppm"], row[f"aq5_{channel}_raw"]))
        readings.append(
            Reading(sensor, "gas_sensor_resistance", row[f"aq5_{channel}_RS_ohm"], row[f"aq5_{channel}_raw"])
        )
    return readings


def _volatile_samples(paths: list[Path]) -> Iterator[Sample]:
    index = 0
    for path in paths:
        anchor: datetime | None = None
        first_ms: float | None = None

        with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
            for fields in csv.reader(handle):
                # The logger writes an unquoted banner as two columns and the
                # measurements as eighteen, so the field count is the
                # discriminator.
                if len(fields) != len(VOLATILE_COLUMNS) + 1:
                    continue
                if fields[1].strip() == "time_ms":
                    continue    # the embedded header row

                values = {name: _to_float(fields[i + 1]) for i, name in enumerate(VOLATILE_COLUMNS)}
                if values["time_ms"] is None:
                    continue

                if anchor is None:
                    try:
                        anchor = datetime.strptime(fields[0].strip(), "%Y-%m-%d %H:%M:%S").replace(
                            tzinfo=timezone.utc
                        )
                    except ValueError:
                        continue
                    first_ms = values["time_ms"]

                # The wall-clock column has one second of resolution while the
                # board samples faster than that, so time_ms carries the
                # sub-second offset (DEC-01 wants milliseconds).
                event_time = anchor + timedelta(milliseconds=values["time_ms"] - first_ms)
                yield Sample(index=index, event_time=event_time, readings=_volatile_readings(values))
                index += 1


def volatiles_source(paths: list[Path]) -> Source:
    """One mission for the whole campaign.

    The 58 files are contiguous ten-minute chunks of a single day on one rig,
    not 58 separate experiments, so they belong to one mission.
    """
    paths = sorted(paths)
    started = None
    for path in paths:
        started = date_from_name(path.name)
        if started:
            break
    started = started or datetime(2026, 5, 18, tzinfo=timezone.utc)

    return Source(
        mission_id=f"volatiles-{started.date().isoformat()}",
        mission_name=f"Volatiles bench campaign {started.date().isoformat()}",
        device_id="arduino-uno-5v",
        started_at=started,
        description=(
            f"Ingested from {len(paths)} files in data/Volatiles. MQ-4, MiCS-5524 and MiCS-6814 "
            "logged at 1 Hz with real timestamps. Sub-second resolution comes from the board's "
            "time_ms column. The MiCS-6814 CO channel was never calibrated against clean air and "
            "reports implausible ppm; those rows are stored with quality = suspect."
        ),
        files=paths,
        samples=_volatile_samples(paths),
    )


# --- Particles: repeating text blocks, no timestamps ----------------------

PM_KEYS = {"1.0": "pm1_0", "2.5": "pm2_5", "4.0": "pm4_0", "10.0": "pm10"}
NC_KEYS = {"0.5": "nc0_5", "1.0": "nc1_0", "2.5": "nc2_5", "4.0": "nc4_0", "10.0": "nc10"}


def _particle_samples(path: Path, started_at: datetime, rate_hz: float) -> Iterator[Sample]:
    section: str | None = None
    readings: list[Reading] = []
    latitude: float | None = None
    longitude: float | None = None
    index = 0
    period = timedelta(seconds=1.0 / rate_hz)

    def flush(readings, latitude, longitude, index):
        return Sample(
            index=index,
            event_time=started_at + index * period,
            readings=list(readings),
            latitude=latitude,
            longitude=longitude,
        )

    for line in _read_lines(path):
        stripped = line.strip()

        if stripped.startswith("PM"):
            # A new block starts here; emit whatever the previous one gathered.
            if readings:
                yield flush(readings, latitude, longitude, index)
                index += 1
                readings, latitude, longitude = [], None, None
            section = "pm"
            continue
        if stripped.startswith("NC"):
            section = "nc"
            continue
        if stripped.startswith("Typical particle size"):
            value = _to_float(stripped.split(":", 1)[-1])
            if value is not None:
                readings.append(Reading("sps30", "typical_particle_size", value))
            section = None
            continue
        if stripped.startswith("Coordinates"):
            parts = stripped.split(":", 1)[-1].split()
            if len(parts) >= 2:
                latitude, longitude = _to_float(parts[0]), _to_float(parts[1])
            continue

        if section in ("pm", "nc"):
            parts = stripped.split()
            if len(parts) == 2:
                table = PM_KEYS if section == "pm" else NC_KEYS
                metric = table.get(parts[0])
                value = _to_float(parts[1])
                if metric and value is not None:
                    readings.append(Reading("sps30", metric, value))

    if readings:
        yield flush(readings, latitude, longitude, index)


def particles_source(path: Path, rate_hz: float = DEFAULT_RATE_HZ) -> Source:
    """One mission per file: each is a separate outing, on foot, bike or car."""
    started = date_from_name(path.name) or datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    guessed = date_from_name(path.name) is None

    return Source(
        mission_id=f"particles-{path.stem}",
        mission_name=f"Particulates: {path.stem}",
        device_id="sps30-logger",
        started_at=started,
        description=(
            f"Ingested from data/Particles/{path.name}. The SPS30 log records sample order and no "
            f"timestamps, so event_time is the capture date advanced at an assumed {rate_hz:g} Hz "
            "(OPEN-10); it is not measured time. Source labels mass concentration ug/cm^3, which is "
            "wrong by a factor of a million: stored as ug/m3."
            + (" Capture date could not be read from the filename and falls back to the file mtime."
               if guessed else "")
        ),
        files=[path],
        samples=_particle_samples(path, started, rate_hz),
    )


# --- Alcohol and CH4: Arduino serial logs, no timestamps ------------------

def _arduino_gas_samples(
    path: Path, started_at: datetime, rate_hz: float, gas_sensor: str, gas_metric: str | None
) -> Iterator[Sample]:
    index = 0
    period = timedelta(seconds=1.0 / rate_hz)

    for line in _read_lines(path):
        fields = [f for f in line.split(",")]
        if len(fields) != 6:
            continue    # Arduino chatter and the mangled header
        adc, _volts, resistance_kohm, gas_ppm, temperature, humidity = (_to_float(f) for f in fields)
        if adc is None or temperature is None or humidity is None:
            continue

        readings = [
            Reading("th_probe", "air_temperature", temperature),
            Reading("th_probe", "relative_humidity", humidity),
        ]
        if resistance_kohm is not None:
            readings.append(
                Reading(gas_sensor, "gas_sensor_resistance", resistance_kohm * 1000.0, adc)
            )
        if gas_metric and gas_ppm is not None:
            readings.append(Reading(gas_sensor, gas_metric, gas_ppm, adc))

        yield Sample(index=index, event_time=started_at + index * period, readings=readings)
        index += 1


def arduino_gas_source(
    path: Path,
    device_id: str,
    gas_sensor: str,
    gas_metric: str | None,
    label: str,
    rate_hz: float = DEFAULT_RATE_HZ,
    note: str = "",
) -> Source:
    """One mission per file: each is a separate bench session on its own date."""
    started = date_from_name(path.name) or datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)

    return Source(
        mission_id=f"{label}-{path.stem}",
        mission_name=f"{label.capitalize()} bench session: {path.stem}",
        device_id=device_id,
        started_at=started,
        description=(
            f"Ingested from {path.parent.name}/{path.name}. The logger records sample order and no "
            f"timestamps, so event_time is the capture date advanced at an assumed {rate_hz:g} Hz "
            "(OPEN-10); it is not measured time. " + note
        ),
        files=[path],
        samples=_arduino_gas_samples(path, started, rate_hz, gas_sensor, gas_metric),
    )


# --- discovery ------------------------------------------------------------

CH4_NOTE = (
    "Filed as methane on the strength of the folder and filename. The logger header in these files "
    "says 'INICIANDO SENSOR ALCOHOL 3' and labels the column 'Etanol [ppm]', which contradicts it "
    "(OPEN-12)."
)


def discover(data_root: Path, rate_hz: float = DEFAULT_RATE_HZ) -> list[Source]:
    """Every chemistry source in data/, in a stable order.

    Driven by directory patterns rather than a file list (DEC-19), so widening
    the ingest is a matter of dropping files in.
    """
    sources: list[Source] = []

    volatiles = sorted((data_root / "Volatiles").glob("*.csv"))
    if volatiles:
        sources.append(volatiles_source(volatiles))

    # Three bike runs were saved twice, once plain and once with a Coordinates
    # line appended to each block. The measurements are identical down to the
    # sample count, so the _Coord file supersedes its twin; ingesting both
    # would put the same ride in the database under two mission ids.
    particle_paths = sorted((data_root / "Particles").glob("*.txt"))
    superseded = {p.stem[: -len("_Coord")] for p in particle_paths if p.stem.endswith("_Coord")}
    for path in particle_paths:
        if path.stem in superseded:
            continue
        sources.append(particles_source(path, rate_hz))

    for path in sorted((data_root / "Alcohol").glob("*.txt")):
        sources.append(
            arduino_gas_source(
                path,
                device_id="akel-alcohol3",
                gas_sensor="mics5524",
                gas_metric="ethanol_concentration",
                label="alcohol",
                rate_hz=rate_hz,
            )
        )

    for path in sorted((data_root / "CH4").glob("*.txt")):
        sources.append(
            arduino_gas_source(
                path,
                device_id="akel-ch4",
                gas_sensor="ch4_sensor",
                gas_metric="ch4_concentration",
                label="ch4",
                rate_hz=rate_hz,
                note=CH4_NOTE,
            )
        )

    return sources
