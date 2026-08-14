"""Discovery rules over the layout of data/."""

from __future__ import annotations

from pathlib import Path

from sources import discover

BLOCK = """SPS sensor probing successful
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
"""

COORD_BLOCK = BLOCK + "Coordinates: 40.197802 -8.509280\n"


def _data_root(tmp_path: Path) -> Path:
    for folder in ("Volatiles", "Particles", "Alcohol", "CH4"):
        (tmp_path / folder).mkdir()
    return tmp_path


def test_coord_twin_supersedes_the_plain_capture(tmp_path: Path) -> None:
    root = _data_root(tmp_path)
    (root / "Particles" / "28June2025_Bike_Taveiro.txt").write_text(BLOCK, encoding="utf-8")
    (root / "Particles" / "28June2025_Bike_Taveiro_Coord.txt").write_text(COORD_BLOCK, encoding="utf-8")

    missions = [s.mission_id for s in discover(root)]
    assert "particles-28June2025_Bike_Taveiro_Coord" in missions
    assert "particles-28June2025_Bike_Taveiro" not in missions


def test_capture_without_a_twin_is_kept(tmp_path: Path) -> None:
    root = _data_root(tmp_path)
    (root / "Particles" / "24JuneCar0000.txt").write_text(BLOCK, encoding="utf-8")

    missions = [s.mission_id for s in discover(root)]
    assert missions == ["particles-24JuneCar0000"]


def test_missing_folders_are_not_fatal(tmp_path: Path) -> None:
    # Only Particles exists; the others are absent entirely.
    (tmp_path / "Particles").mkdir()
    (tmp_path / "Particles" / "24JuneCar0000.txt").write_text(BLOCK, encoding="utf-8")
    assert len(discover(tmp_path)) == 1


def test_each_source_maps_to_the_right_device(tmp_path: Path) -> None:
    root = _data_root(tmp_path)
    (root / "Particles" / "24JuneCar0000.txt").write_text(BLOCK, encoding="utf-8")
    (root / "Alcohol" / "Akel_Alcohol3_20250728.txt").write_text("15, 0.02, 1278.40, 0.36, 27.90, 83.94\n", encoding="utf-8")
    (root / "CH4" / "Akel_CH4_20250815.txt").write_text("20, 0.02, 957.62, 0.57, 30.90, 83.71\n", encoding="utf-8")

    devices = {s.mission_id: s.device_id for s in discover(root)}
    assert devices["particles-24JuneCar0000"] == "sps30-logger"
    assert devices["alcohol-Akel_Alcohol3_20250728"] == "akel-alcohol3"
    assert devices["ch4-Akel_CH4_20250815"] == "akel-ch4"
