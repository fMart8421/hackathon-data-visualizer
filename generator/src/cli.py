"""Arguments and main loop.

Direct mode runs the flight at clock pace with an acceleration factor: the
balloon is flying *now*, just faster. event_time is therefore real wall-clock
time, and a 150 minute flight at 60x fills 2.5 minutes of the dashboard's time
axis. ingested_at is set by the database, so the gap between the two is the
genuine pipeline lag (DEC-01).

File mode (`--sink ndjson`) writes the same flight to a file instead, as fast
as the machine can produce it and with no acceleration applied: pacing belongs
to whoever replays it (DEC-20).
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import time
from datetime import datetime, timedelta, timezone

from contract import Batch, ObservationRecord, PositionFix
from flight import OVAR_LAT, OVAR_LON, FlightProfile
from sensors import build_scalar_sensors, read_scalars
from sinks import NdjsonSink, PostgresSink, Sink

DEFAULT_SEED = 20260812
SAMPLE_PERIOD_S = 1.0  # 1 Hz for position and the phase 2 scalars

_stopping = False


def _handle_signal(signum, frame) -> None:  # noqa: ANN001 - signal handler signature
    global _stopping
    _stopping = True


def default_dsn() -> str:
    dsn = os.environ.get("TELEMETRY_DSN")
    if dsn:
        return dsn
    user = os.environ.get("POSTGRES_USER", "telemetry")
    password = os.environ.get("POSTGRES_PASSWORD", "telemetry")
    host = os.environ.get("POSTGRES_HOST", "db")
    port = os.environ.get("POSTGRES_PORT", "5432")
    database = os.environ.get("POSTGRES_DB", "telemetry")
    return f"postgresql://{user}:{password}@{host}:{port}/{database}"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="generator",
        description="Stratospheric balloon telemetry generator (DEC-11, DEC-12).",
    )
    parser.add_argument("--speed", type=float, default=1.0, help="acceleration factor, 1 = real time")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="random seed, fixed for a reproducible demo")
    parser.add_argument("--duration-min", type=float, default=150.0, help="nominal flight duration in minutes")
    parser.add_argument("--start-lat", type=float, default=OVAR_LAT)
    parser.add_argument("--start-lon", type=float, default=OVAR_LON)
    parser.add_argument("--mission-id", default=None, help="defaults to flight-<UTC timestamp>")
    parser.add_argument("--device-id", default="balloon-01")
    parser.add_argument("--dsn", default=None, help="PostgreSQL DSN, defaults to $TELEMETRY_DSN")
    parser.add_argument(
        "--sink",
        choices=["postgres", "ndjson"],
        default="postgres",
        help="postgres writes live, ndjson writes a file for the replayer",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="output file for --sink ndjson, e.g. /exports/flight.ndjson",
    )
    parser.add_argument(
        "--on-finish",
        choices=["stop", "restart"],
        default="stop",
        help="what to do at the end of the flight",
    )
    parser.add_argument("--quiet", action="store_true", help="only report start and end")
    return parser.parse_args(argv)


def build_batch(
    profile: FlightProfile,
    sensors: list,
    mission_id: str,
    device_id: str,
    sample_index: int,
    event_time: datetime,
) -> Batch:
    mission_time = sample_index * SAMPLE_PERIOD_S
    state = profile.state_at(mission_time)
    readings = read_scalars(sensors, state.altitude_m, mission_time, sample_index)

    return Batch(
        device_id=device_id,
        mission_id=mission_id,
        seq=sample_index,
        batch_time=event_time,
        position=PositionFix(
            lat=state.latitude,
            lon=state.longitude,
            alt_m=state.altitude_m,
            speed_ms=state.speed_ms,
            heading_deg=state.heading_deg,
            vertical_speed_ms=state.vertical_speed_ms,
            fix_quality=state.fix_quality,
            satellites=state.satellites,
            hdop=state.hdop,
        ),
        observations=[
            ObservationRecord(
                sensor_id=reading.sensor_id,
                metric_key=reading.metric_key,
                t_offset_ms=reading.t_offset_ms,
                value=reading.value,
                value_raw=reading.value_raw,
            )
            for reading in readings
        ],
    )


def run_flight(args: argparse.Namespace, sink: Sink, flight_number: int) -> bool:
    """Fly once. Returns False if interrupted."""
    live = args.sink == "postgres"
    duration_s = args.duration_min * 60.0
    profile = FlightProfile(
        duration_s=duration_s,
        seed=args.seed + flight_number,
        start_lat=args.start_lat,
        start_lon=args.start_lon,
    )
    sensors = build_scalar_sensors(args.seed + flight_number)

    started_at = datetime.now(timezone.utc)
    mission_id = args.mission_id or f"flight-{started_at.strftime('%Y-%m-%dT%H%M%S')}"
    if flight_number > 0 and args.mission_id:
        mission_id = f"{args.mission_id}-{flight_number}"

    sink.open_mission(
        mission_id,
        name=f"Stratospheric balloon flight {started_at.strftime('%Y-%m-%d %H:%M')} UTC",
        started_at=started_at,
        description=(
            "SYNTHETIC. Not measured. Generated balloon flight, seed "
            f"{args.seed + flight_number}, {args.duration_min:g} min at {args.speed:g}x from "
            f"{args.start_lat:.4f}, {args.start_lon:.4f}. The retired DEC-12 scenario, kept "
            "under DEC-18 as one synthetic session among the real captures."
        ),
        # DEC-18: nothing the generator writes may be mistakable for a
        # measurement, whichever sink it goes to.
        kind="synthetic",
    )

    total_samples = int(duration_s / SAMPLE_PERIOD_S) + 1
    if live:
        wall_duration = duration_s / args.speed
        print(
            f"mission {mission_id}: {total_samples} samples, {args.duration_min:g} min of flight "
            f"in {wall_duration / 60:.1f} min of wall clock ({args.speed:g}x)",
            flush=True,
        )
    else:
        print(
            f"mission {mission_id}: {total_samples} samples, {args.duration_min:g} min of flight "
            f"to {args.out} as fast as it can be written",
            flush=True,
        )

    report_every = max(1, int(total_samples / 30))
    for sample_index in range(total_samples):
        if _stopping:
            break

        # Wall clock instant this sample belongs to. Computed from the start
        # rather than accumulated, so scheduling jitter cannot drift the series.
        # In file mode the flight keeps its own pace, one second per sample:
        # compressing it would bake the acceleration into the file.
        scale = 1.0 / args.speed if live else 1.0
        event_time = started_at + timedelta(seconds=sample_index * SAMPLE_PERIOD_S * scale)
        if live:
            delay = (event_time - datetime.now(timezone.utc)).total_seconds()
            if delay > 0:
                time.sleep(delay)

        batch = build_batch(profile, sensors, mission_id, args.device_id, sample_index, event_time)
        sink.write(batch)

        if not args.quiet and sample_index % report_every == 0:
            state = profile.state_at(sample_index * SAMPLE_PERIOD_S)
            temperature = batch.observations[0].value
            print(
                f"  {_clock(sample_index * SAMPLE_PERIOD_S)} {state.phase.value:<7} "
                f"alt {state.altitude_m:8.1f} m  vs {state.vertical_speed_ms:+6.2f} m/s  "
                f"{state.latitude:.4f},{state.longitude:.4f}  T {temperature:+6.2f} C",
                flush=True,
            )

    ended_at = datetime.now(timezone.utc)
    sink.close_mission(mission_id, ended_at)
    print(f"mission {mission_id}: {sink.rows_written} rows written", flush=True)
    return not _stopping


def _clock(seconds: float) -> str:
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    if args.sink == "ndjson":
        if not args.out:
            print("--sink ndjson needs --out FILE", file=sys.stderr)
            return 2
        if args.on_finish == "restart":
            # A file holds one mission: its header is line 1. Two flights in
            # one file would put a second header in the middle of the batches.
            print("--sink ndjson cannot be combined with --on-finish restart", file=sys.stderr)
            return 2
        sink: Sink = NdjsonSink(args.out)
    else:
        sink = PostgresSink(args.dsn or default_dsn(), time_scale=1.0 / args.speed)
    try:
        flight_number = 0
        while True:
            completed = run_flight(args, sink, flight_number)
            if not completed or args.on_finish == "stop":
                break
            flight_number += 1
    finally:
        sink.close()

    if isinstance(sink, NdjsonSink):
        size_mb = sink.path.stat().st_size / 1e6
        print(
            f"wrote {sink.path} ({sink.batches_written} batches, {size_mb:.1f} MB). "
            f"Replay it with: make replay FILE={sink.path.name}",
            flush=True,
        )
    if _stopping:
        print("interrupted, mission closed", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
