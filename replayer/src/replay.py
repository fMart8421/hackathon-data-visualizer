"""Replay a file mode export into PostgreSQL at the pace of its timestamps.

    make export FILE=flight.ndjson
    make replay FILE=flight.ndjson
    make replay FILE=flight.ndjson ARGS="--speed 60"

The generator invents a flight once; the replayer puts the same flight on the
dashboard as many times as the demo needs. Same batches, same contract, same
sink, so a replayed row is indistinguishable from the row the generator would
have written live (DEC-20).

By default the flight is re-anchored: the first batch lands *now* and the rest
follow at their recorded spacing, under a fresh mission id. That is what makes
a replay worth watching, because requirement 9 is about data arriving while
the page is open. `--anchor original` keeps the file's own timestamps and
mission id, which is idempotent through the DEC-03 natural keys and therefore
writes nothing on a second run.

ingested_at is left to the database, so the lag panel measures this pipeline
and not the one that produced the file.
"""

from __future__ import annotations

import argparse
import dataclasses
import itertools
import signal
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cli import default_dsn
from contract import Batch
from sinks import PostgresSink, read_ndjson, refresh_aggregates

_stopping = False


def _handle_signal(signum, frame) -> None:  # noqa: ANN001 - signal handler signature
    global _stopping
    _stopping = True


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="replay",
        description="Insert an NDJSON export at the pace of its own timestamps (DEC-20).",
    )
    parser.add_argument("--file", required=True, type=Path, help="NDJSON produced by --sink ndjson")
    parser.add_argument("--speed", type=float, default=1.0, help="acceleration factor, 1 = original pace")
    parser.add_argument(
        "--anchor",
        choices=["now", "original"],
        default="now",
        help="now replays the flight as if it were happening now; original keeps the file's timestamps",
    )
    parser.add_argument("--mission-id", default=None, help="overrides the id the replay writes under")
    parser.add_argument("--device-id", default=None, help="overrides the device the file names")
    parser.add_argument("--dsn", default=None, help="PostgreSQL DSN, defaults to $TELEMETRY_DSN")
    parser.add_argument("--limit", type=int, default=None, help="stop after this many batches")
    parser.add_argument("--quiet", action="store_true", help="only report start and end")
    parser.add_argument("--dry-run", action="store_true", help="read and report, write nothing")
    return parser.parse_args(argv)


def replay_mission_id(original: str, started_at: datetime) -> str:
    """A fresh id per replay, so two replays are two missions.

    Re-anchored batches keep the file's seq numbers, so replaying twice under
    the same id would collide with the DEC-03 natural keys and silently insert
    nothing: the second replay would look like a dead pipeline.
    """
    return f"{original}-replay-{started_at.strftime('%Y-%m-%dT%H%M%S')}"


@dataclasses.dataclass(frozen=True)
class TimeMap:
    """Where a batch lands in time, and when it is inserted.

    Acceleration has to move event_time as well as the insertion pace, exactly
    as direct mode does (DEC-16): the flight is happening *now*, only faster.
    Shifting the timestamps by a constant while inserting 60x faster would run
    event_time an hour ahead of the clock, put the series off the right-hand
    edge of a `now` time range, and turn the lag panel negative.

    anchor=original keeps the file's own timestamps, because there the point
    is to reproduce the recorded instants; only the insertion pace changes.
    """

    first_batch_time: datetime
    anchor_time: datetime
    speed: float
    anchor: str = "now"

    def _elapsed(self, batch_time: datetime) -> timedelta:
        return (batch_time - self.first_batch_time) / self.speed

    def event_time(self, batch_time: datetime) -> datetime:
        if self.anchor == "original":
            return batch_time
        return self.anchor_time + self._elapsed(batch_time)

    def wall_time(self, batch_time: datetime) -> datetime:
        """When to insert. Computed from the first batch, never accumulated,
        so a slow insert cannot drift the rest of the replay."""
        return self.anchor_time + self._elapsed(batch_time)


def shift(batch: Batch, clock: TimeMap, mission_id: str, device_id: str | None) -> Batch:
    return dataclasses.replace(
        batch,
        batch_time=clock.event_time(batch.batch_time),
        mission_id=mission_id,
        device_id=device_id or batch.device_id,
    )


def _clock(delta: timedelta) -> str:
    seconds = int(delta.total_seconds())
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    if not args.file.is_file():
        print(f"no such file: {args.file}", file=sys.stderr)
        return 1
    if args.speed <= 0:
        print("--speed must be positive", file=sys.stderr)
        return 2

    header, batches = read_ndjson(args.file)

    # The time map needs the first batch, and the stream is consumed lazily so
    # a 150 minute flight never has to sit in memory. Peek, then put it back.
    try:
        first = next(batches)
    except StopIteration:
        print(f"{args.file} holds no batches", file=sys.stderr)
        return 1
    batches = itertools.chain([first], batches)

    started_at = datetime.now(timezone.utc)
    clock = TimeMap(
        first_batch_time=first.batch_time,
        anchor_time=started_at,
        speed=args.speed,
        anchor=args.anchor,
    )
    source_mission = header["mission_id"] if header else first.mission_id
    if args.mission_id:
        mission_id = args.mission_id
    elif args.anchor == "now":
        mission_id = replay_mission_id(source_mission, started_at)
    else:
        mission_id = source_mission

    # A file with no header came from somewhere this tool cannot vouch for.
    # Under DEC-18 the safe assumption is the one that cannot mislabel
    # generated data as measured.
    kind = header["kind"] if header else "synthetic"
    name = header["name"] if header else f"Replay of {args.file.name}"

    print(
        f"replaying {args.file.name}\n"
        f"  source mission  {source_mission} ({kind})\n"
        f"  writing as      {mission_id}\n"
        f"  anchor          {args.anchor}"
        + (f", first batch at {started_at:%H:%M:%S} UTC" if args.anchor == "now" else "")
        + f"\n  speed           {args.speed:g}x",
        flush=True,
    )

    if args.dry_run:
        count = 0
        rows = 0
        last = first.batch_time
        for batch in batches:
            count += 1
            rows += batch.record_count
            last = batch.batch_time
            if args.limit and count >= args.limit:
                break
        span = last - first.batch_time
        print(
            f"\n{count} batches, {rows} rows, {_clock(span)} of flight, "
            f"{_clock(span / args.speed)} of wall clock at {args.speed:g}x. Nothing written."
        )
        return 0

    sink = PostgresSink(args.dsn or default_dsn(), time_scale=1.0 / args.speed, source="replay")
    written = 0
    count = 0
    try:
        sink.open_mission(
            mission_id,
            name=name if args.anchor == "original" else f"{name} (replay)",
            started_at=clock.event_time(first.batch_time),
            description=(
                f"Replayed from {args.file.name} at {args.speed:g}x, anchor {args.anchor}. "
                f"Source mission {source_mission}. "
                + (header["description"] if header else "No mission header in the file.")
            ),
            kind=kind,
        )

        for batch in batches:
            if _stopping:
                break

            shifted = shift(batch, clock, mission_id, args.device_id)
            delay = (clock.wall_time(batch.batch_time) - datetime.now(timezone.utc)).total_seconds()
            if delay > 0:
                time.sleep(delay)

            sink.write(shifted)
            written += shifted.record_count
            count += 1

            if not args.quiet and count % 100 == 0:
                print(
                    f"  {_clock(batch.batch_time - first.batch_time)} of flight  "
                    f"{count} batches  {written} rows",
                    flush=True,
                )
            if args.limit and count >= args.limit:
                break

        sink.close_mission(mission_id, datetime.now(timezone.utc))
    finally:
        sink.close()

    if written:
        refresh_aggregates(args.dsn or default_dsn())

    print(
        f"\nmission {mission_id}: {written} rows from {count} batches"
        + (" (interrupted)" if _stopping else ""),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
