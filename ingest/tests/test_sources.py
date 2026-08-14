"""Parser tests, against the exact shapes the provided files use."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from sources import (
    arduino_gas_source,
    date_from_name,
    particles_source,
    volatiles_source,
)

# --- filename dates -------------------------------------------------------


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Akel_Alcohol3_20250728.txt", datetime(2025, 7, 28, tzinfo=timezone.utc)),
        ("Akel_Alcohol3_20250819_164219.txt", datetime(2025, 8, 19, 16, 42, 19, tzinfo=timezone.utc)),
        ("28June2025_Bike_Taveiro.txt", datetime(2025, 6, 28, tzinfo=timezone.utc)),
        ("31May2025_SerraAcor_1.txt", datetime(2025, 5, 31, tzinfo=timezone.utc)),
        ("24JuneCar0000.txt", datetime(2025, 6, 24, tzinfo=timezone.utc)),
        ("2705teste1ISO5.txt", datetime(2025, 5, 27, tzinfo=timezone.utc)),
        ("CH4Air5Alc3_2026-05-18_10-18-35.csv", None),
    ],
)
def test_date_from_name(name: str, expected: datetime | None) -> None:
    assert date_from_name(name) == expected


def test_date_from_name_gives_up_rather_than_guessing() -> None:
    assert date_from_name("no_date_here.txt") is None


# --- volatiles ------------------------------------------------------------

VOLATILES_CSV = """timestamp,data
2026-05-18 10:18:35,==============================================
2026-05-18 10:18:35,Arduino Uno - 5V Environmental Sensor Suite
2026-05-18 10:18:35,[OK]   MCP3221 found at 0x4D
2026-05-18 10:18:35,time_ms,mq4_raw,mq4_DO,mq4_CH4_ppm,alco_raw,alco_RS_ohm,alco_CO_ppm,alco_EtOH_ppm,aq5_CO_raw,aq5_CO_RS_ohm,aq5_CO_ppm,aq5_NH3_raw,aq5_NH3_RS_ohm,aq5_NH3_ppm,aq5_NO2_raw,aq5_NO2_RS_ohm,aq5_NO2_ppm
2026-05-18 10:18:35,1063,316,0,0.0,195,200051,0.3,0.0,766,43603,73357.2,373,194906,2.3,792,8556,1.82
2026-05-18 10:18:35,2063,337,0,1.5,170,230941,0.2,0.4,770,42857,77064.7,382,187958,2.4,801,8212,1.74
"""


@pytest.fixture()
def volatiles_file(tmp_path: Path) -> Path:
    path = tmp_path / "CH4Air5Alc3_2026-05-18_10-18-35.csv"
    path.write_text(VOLATILES_CSV, encoding="utf-8")
    return path


def test_volatiles_skips_banner_and_header(volatiles_file: Path) -> None:
    samples = list(volatiles_source([volatiles_file]).samples)
    assert len(samples) == 2


def test_volatiles_maps_every_species(volatiles_file: Path) -> None:
    first = list(volatiles_source([volatiles_file]).samples)[0]
    metrics = {(r.sensor_id, r.metric_key) for r in first.readings}
    assert ("mq4", "ch4_concentration") in metrics
    assert ("mics5524", "ethanol_concentration") in metrics
    assert ("mics5524", "co_concentration") in metrics
    assert ("mics6814_nh3", "nh3_concentration") in metrics
    assert ("mics6814_no2", "no2_concentration") in metrics
    # The MiCS-6814 is three sensing elements, so each has its own resistance.
    resistances = {r.sensor_id for r in first.readings if r.metric_key == "gas_sensor_resistance"}
    assert resistances == {"mics5524", "mics6814_co", "mics6814_nh3", "mics6814_no2"}


def test_volatiles_keeps_raw_and_calibrated(volatiles_file: Path) -> None:
    first = list(volatiles_source([volatiles_file]).samples)[0]
    methane = next(r for r in first.readings if r.metric_key == "ch4_concentration")
    assert methane.value == 0.0        # ppm from the firmware
    assert methane.value_raw == 316    # the ADC count behind it


def test_volatiles_uses_time_ms_for_sub_second_resolution(volatiles_file: Path) -> None:
    samples = list(volatiles_source([volatiles_file]).samples)
    # Both rows carry the same wall-clock second; time_ms separates them.
    gap = (samples[1].event_time - samples[0].event_time).total_seconds()
    assert gap == pytest.approx(1.0)
    assert samples[0].event_time == datetime(2026, 5, 18, 10, 18, 35, tzinfo=timezone.utc)


def test_volatiles_is_one_mission_for_the_campaign(volatiles_file: Path) -> None:
    source = volatiles_source([volatiles_file, volatiles_file])
    assert source.mission_id == "volatiles-2026-05-18"
    assert source.device_id == "arduino-uno-5v"


# --- particles ------------------------------------------------------------

PARTICLES_BLOCK = """SPS sensor probing successful
measurements started
PM (ug/cm^3):
1.0 9.49
2.5 10.03
4.0 10.03
10.0 10.03
NC:
0.5 64.54
1.0 75.38
2.5 75.71
4.0 75.74
10.0 75.75
Typical particle size: 0.41
Coordinates: 40.197802 -8.509280

PM (ug/cm^3):
1.0 9.54
2.5 10.08
4.0 10.08
10.0 10.08
NC:
0.5 64.88
1.0 75.76
2.5 76.11
4.0 76.13
10.0 76.15
Typical particle size: 0.40
Coordinates: 40.197804 -8.509282
"""


@pytest.fixture()
def particles_file(tmp_path: Path) -> Path:
    path = tmp_path / "28June2025_Bike_Taveiro_Coord.txt"
    path.write_text(PARTICLES_BLOCK, encoding="utf-8")
    return path


def test_particles_splits_blocks(particles_file: Path) -> None:
    samples = list(particles_source(particles_file).samples)
    assert len(samples) == 2


def test_particles_reads_every_channel(particles_file: Path) -> None:
    first = list(particles_source(particles_file).samples)[0]
    values = {r.metric_key: r.value for r in first.readings}
    assert values["pm1_0"] == 9.49
    assert values["pm2_5"] == 10.03
    assert values["pm10"] == 10.03
    assert values["nc0_5"] == 64.54
    assert values["nc10"] == 75.75
    assert values["typical_particle_size"] == 0.41
    assert len(first.readings) == 10


def test_particles_carries_coordinates(particles_file: Path) -> None:
    samples = list(particles_source(particles_file).samples)
    assert samples[0].latitude == pytest.approx(40.197802)
    assert samples[0].longitude == pytest.approx(-8.509280)
    assert samples[1].latitude == pytest.approx(40.197804)


def test_particles_synthesises_a_time_axis(particles_file: Path) -> None:
    source = particles_source(particles_file, rate_hz=1.0)
    samples = list(source.samples)
    assert samples[0].event_time == datetime(2025, 6, 28, tzinfo=timezone.utc)
    assert (samples[1].event_time - samples[0].event_time).total_seconds() == 1.0
    # The assumption has to be visible to whoever reads the axis.
    assert "assumed 1 Hz" in source.description
    assert "not measured time" in source.description


def test_particles_rate_is_configurable(particles_file: Path) -> None:
    samples = list(particles_source(particles_file, rate_hz=2.0).samples)
    assert (samples[1].event_time - samples[0].event_time).total_seconds() == 0.5


def test_particles_without_coordinates_still_parses(tmp_path: Path) -> None:
    path = tmp_path / "24JuneCar0000.txt"
    path.write_text(PARTICLES_BLOCK.replace("Coordinates: 40.197802 -8.509280\n", ""), encoding="utf-8")
    samples = list(particles_source(path).samples)
    assert samples[0].latitude is None
    assert len(samples) == 2


# --- arduino gas logs -----------------------------------------------------

ALCOHOL_LOG = """Arduino: Pronto para conectar. Aguardando 'START' do PC...
Arduino: Timeout! Nao recebeu 'START'. Reiniciando...
---------- INICIANDO SENSOR ALCOHOL 3 ---------
ValorADC [1], Tensao [V], Resistencia medida [kOhm], Etanol [ppm], T [ÂºC], Humidade [%]
15, 0.02, 1278.40, 0.36, 27.90, 83.94
13, 0.02, 1475.80, 0.29, 27.90, 83.80
"""


@pytest.fixture()
def alcohol_file(tmp_path: Path) -> Path:
    path = tmp_path / "Akel_Alcohol3_20250728.txt"
    path.write_text(ALCOHOL_LOG, encoding="utf-8")
    return path


def _alcohol(path: Path):
    return arduino_gas_source(
        path,
        device_id="akel-alcohol3",
        gas_sensor="mics5524",
        gas_metric="ethanol_concentration",
        label="alcohol",
    )


def test_arduino_log_skips_chatter_and_header(alcohol_file: Path) -> None:
    assert len(list(_alcohol(alcohol_file).samples)) == 2


def test_arduino_log_maps_columns(alcohol_file: Path) -> None:
    first = list(_alcohol(alcohol_file).samples)[0]
    values = {(r.sensor_id, r.metric_key): r.value for r in first.readings}
    assert values[("th_probe", "air_temperature")] == 27.90
    assert values[("th_probe", "relative_humidity")] == 83.94
    assert values[("mics5524", "ethanol_concentration")] == 0.36
    # kOhm in the file, ohm in the catalog (DEC-04).
    assert values[("mics5524", "gas_sensor_resistance")] == 1278400.0


def test_arduino_log_keeps_the_adc_as_raw(alcohol_file: Path) -> None:
    first = list(_alcohol(alcohol_file).samples)[0]
    ethanol = next(r for r in first.readings if r.metric_key == "ethanol_concentration")
    assert ethanol.value_raw == 15


def test_ch4_records_the_contradiction(tmp_path: Path) -> None:
    path = tmp_path / "Akel_CH4_20250815.txt"
    path.write_text(ALCOHOL_LOG, encoding="utf-8")
    source = arduino_gas_source(
        path,
        device_id="akel-ch4",
        gas_sensor="ch4_sensor",
        gas_metric="ch4_concentration",
        label="ch4",
        note="OPEN-12 note",
    )
    assert source.mission_id == "ch4-Akel_CH4_20250815"
    assert "OPEN-12" in source.description
    first = list(source.samples)[0]
    assert any(r.metric_key == "ch4_concentration" for r in first.readings)


def test_nan_reading_becomes_absent_rather_than_a_float(tmp_path: Path) -> None:
    # Three rows in Akel_Alcohol3_20250728.txt log a literal nan. Kept as a
    # float it would make min, max and avg return NaN for the whole metric.
    path = tmp_path / "Akel_Alcohol3_20250728.txt"
    path.write_text("15, 0.02, 1278.40, nan, 27.90, 83.94\n", encoding="utf-8")
    first = list(_alcohol(path).samples)[0]
    ethanol = [r for r in first.readings if r.metric_key == "ethanol_concentration"]
    # The reading is dropped entirely, and the row's other channels survive.
    assert ethanol == []
    assert {r.metric_key for r in first.readings} == {
        "air_temperature", "relative_humidity", "gas_sensor_resistance",
    }


def test_temperature_survives_a_mangled_degree_sign(alcohol_file: Path) -> None:
    # The header is mojibake in every one of these files; the numbers are not.
    first = list(_alcohol(alcohol_file).samples)[0]
    assert any(r.metric_key == "air_temperature" for r in first.readings)
