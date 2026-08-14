"""Write the synthetic supplement: the four channels nothing measured.

    make supplement
    make supplement ARGS="--dry-run"

One mission, labelled kind = 'synthetic' (DEC-18), covering a single day at
Taveiro:

  all day, 0.2 Hz     uv_index, from the sun's real position over the site
  a 79 minute window  the platform rides a real recorded route, adding
                      mag_field at 1 Hz and 100 Hz acceleration and
                      angular_rate in one-second blocks (DEC-07)

The UV sensor runs the whole day so requirement 5 has a diurnal curve to show;
the rest only exists while the platform is moving, because a magnetometer
reading is meaningless without an attitude and an IMU trace is meaningless
without motion.

Idempotent through the DEC-03 natural keys, so re-running writes nothing.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta, timezone

import psycopg
from cli import default_dsn
from contract import Batch, ObservationRecord, PositionFix, WaveformRecord
from route import load_route, smooth
from sinks import PostgresSink
from supplement import Imu, Magnetometer, UvSensor

DEVICE_ID = "synthetic-platform"
DEFAULT_ROUTE_MISSION = "particles-28June2025_Bike_TaveiroLoop_Coord"
UV_PERIOD_S = 5.0        # 0.2 Hz, the cadence the spec gives for UV
CHUNK = 500


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="supplement",
        description="Generate the synthetic supplement for requirements 3, 4, 5 and 8 (DEC-18).",
    )
    parser.add_argument("--mission-id", default=None, help="defaults to synthetic-<site>-<date>")
    parser.add_argument(
        "--route-mission",
        default=DEFAULT_ROUTE_MISSION,
        help="measured mission whose positions the platform follows",
    )
    parser.add_argument("--date", default="2025-06-28", help="UTC date for the synthetic day")
    parser.add_argument(
        "--ride-start",
        default="10:00",
        help="UTC time the ride begins. Daylight, so the UV reading along it is not zero",
    )
    parser.add_argument("--seed", type=int, default=20260628)
    parser.add_argument("--imu", action="store_true", default=True, help="write IMU blocks")
    parser.add_argument("--no-imu", dest="imu", action="store_false")
    parser.add_argument("--dsn", default=None)
    parser.add_argument("--dry-run", action="store_true", help="report what would be written")
    return parser.parse_args(argv)


def _day_of_year(day: datetime) -> int:
    return day.timetuple().tm_yday


def build_uv_batches(
    sensor: UvSensor, day: datetime, latitude: float, longitude: float, mission_id: str
) -> list[Batch]:
    """A full day of UV at the site, whether or not anyone is riding."""
    batches: list[Batch] = []
    doy = _day_of_year(day)
    samples = int(24 * 3600 / UV_PERIOD_S)

    for index in range(samples):
        event_time = day + timedelta(seconds=index * UV_PERIOD_S)
        utc_hours = event_time.hour + event_time.minute / 60.0 + event_time.second / 3600.0
        reading = sensor.read(latitude, longitude, doy, utc_hours, index)
        batches.append(
            Batch(
                device_id=DEVICE_ID,
                mission_id=mission_id,
                seq=index,
                batch_time=event_time,
                observations=[
                    ObservationRecord(
                        sensor_id=sensor.sensor_id,
                        metric_key=sensor.metric_key,
                        value=reading.value,
                        value_raw=reading.value_raw,
                    )
                ],
            )
        )
    return batches


def build_ride_batches(
    points,
    magnetometer: Magnetometer,
    imu: Imu | None,
    ride_start: datetime,
    mission_id: str,
    seq_offset: int,
) -> list[Batch]:
    """The moving window: position, magnetic vector and IMU blocks."""
    batches: list[Batch] = []

    for index, point in enumerate(points):
        event_time = ride_start + timedelta(seconds=index)
        vector = magnetometer.read(point.heading_deg, index)

        observations = [
            ObservationRecord(
                sensor_id=magnetometer.sensor_id,
                metric_key=magnetometer.metric_key,
                # For a vector metric, value carries the magnitude and the
                # components live in vx/vy/vz (DEC-06).
                value=vector.magnitude,
                value_raw=vector.magnitude_raw,
                vx=vector.vx,
                vy=vector.vy,
                vz=vector.vz,
            )
        ]

        waveforms: list[WaveformRecord] = []
        if imu is not None:
            first_sample = index * int(imu.sample_rate_hz)
            accel = imu.acceleration_block(index, point.speed_ms, point.yaw_rate_dps)
            gyro = imu.angular_rate_block(index, point.yaw_rate_dps)
            waveforms = [
                WaveformRecord(
                    sensor_id=imu.sensor_id,
                    metric_key=imu.accel_metric,
                    sample_rate_hz=imu.sample_rate_hz,
                    first_sample_index=first_sample,
                    full_scale=imu.accel_full_scale,
                    samples_x=accel.samples_x,
                    samples_y=accel.samples_y,
                    samples_z=accel.samples_z,
                ),
                WaveformRecord(
                    sensor_id=imu.sensor_id,
                    metric_key=imu.gyro_metric,
                    sample_rate_hz=imu.sample_rate_hz,
                    first_sample_index=first_sample,
                    full_scale=imu.gyro_full_scale,
                    samples_x=gyro.samples_x,
                    samples_y=gyro.samples_y,
                    samples_z=gyro.samples_z,
                ),
            ]

        batches.append(
            Batch(
                device_id=DEVICE_ID,
                mission_id=mission_id,
                seq=seq_offset + index,
                batch_time=event_time,
                position=PositionFix(
                    lat=point.latitude,
                    lon=point.longitude,
                    speed_ms=round(point.speed_ms, 2),
                    heading_deg=round(point.heading_deg, 1),
                ),
                observations=observations,
                waveforms=waveforms,
            )
        )
    return batches


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    dsn = args.dsn or default_dsn()

    day = datetime.fromisoformat(args.date).replace(tzinfo=timezone.utc)
    hour, minute = (int(part) for part in args.ride_start.split(":"))
    ride_start = day + timedelta(hours=hour, minutes=minute)
    mission_id = args.mission_id or f"synthetic-taveiro-{args.date}"

    connection = psycopg.connect(dsn)
    try:
        points = smooth(load_route(connection, args.route_mission))
    finally:
        connection.close()

    if not points:
        print(f"no route found in mission {args.route_mission}", file=sys.stderr)
        return 1

    latitude = sum(p.latitude for p in points) / len(points)
    longitude = sum(p.longitude for p in points) / len(points)

    magnetometer = Magnetometer(seed=args.seed)
    uv = UvSensor(seed=args.seed)
    imu = Imu(seed=args.seed) if args.imu else None

    uv_batches = build_uv_batches(uv, day, latitude, longitude, mission_id)
    ride_batches = build_ride_batches(
        points, magnetometer, imu, ride_start, mission_id, seq_offset=len(uv_batches)
    )

    observations = sum(len(b.observations) for b in uv_batches + ride_batches)
    waveforms = sum(len(b.waveforms) for b in ride_batches)
    print(
        f"mission {mission_id} (synthetic)\n"
        f"  route      {args.route_mission}: {len(points)} points\n"
        f"  uv         {len(uv_batches)} samples over 24 h at {1 / UV_PERIOD_S:g} Hz\n"
        f"  ride       {len(ride_batches)} samples from {ride_start:%H:%M} UTC\n"
        f"  observations {observations}\n"
        f"  waveform blocks {waveforms}"
    )
    if args.dry_run:
        return 0

    sink = PostgresSink(dsn)
    started = time.monotonic()
    written = 0
    try:
        sink.open_mission(
            mission_id,
            name=f"Synthetic supplement, Taveiro {args.date}",
            started_at=day,
            description=(
                "SYNTHETIC. Not measured. Generated under DEC-18 for the four requirements the "
                "provided data cannot serve: geomagnetic vector, accelerometer, gyroscope and UV "
                "index. The route is real, taken from "
                f"{args.route_mission}, and the magnetic field and solar position are the real "
                "ones for this site and date; the platform, the sensors and every value here are "
                "not. The ride is re-anchored to "
                f"{args.ride_start} UTC so the UV reading along it is a daylight one."
            ),
            kind="synthetic",
        )

        for group in (uv_batches, ride_batches):
            for start in range(0, len(group), CHUNK):
                chunk = group[start : start + CHUNK]
                written += sink.write_many(chunk, "generator:supplement", chunk[0].seq)
    finally:
        sink.close()

    print(f"\nwrote {written} rows in {time.monotonic() - started:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
