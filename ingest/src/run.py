"""Load the provided measurements into PostgreSQL.

Runs as a Compose one-off, like the generator (DEC-15):

    make ingest
    make ingest ARGS="--only volatiles --dry-run"

Idempotent: re-running inserts nothing new, so widening the selection and
loading again is safe (DEC-19).
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import psycopg
from cli import default_dsn
from gnss import discover_gnss
from loader import load, load_metric_rules
from sinks import PostgresSink, refresh_aggregates
from sources import DEFAULT_RATE_HZ, discover


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="ingest",
        description="Load the measurements in data/ into PostgreSQL (DEC-17).",
    )
    parser.add_argument("--data-root", default="/data", type=Path, help="folder holding the provided data")
    parser.add_argument(
        "--only",
        action="append",
        default=None,
        help="substring filter on mission id, repeatable, e.g. --only volatiles --only particles",
    )
    parser.add_argument(
        "--rate-hz",
        type=float,
        default=DEFAULT_RATE_HZ,
        help="assumed sample rate for the sources that carry no timestamps (OPEN-10)",
    )
    parser.add_argument("--dry-run", action="store_true", help="parse and report, write nothing")
    parser.add_argument("--limit", type=int, default=None, help="stop after this many samples per source")
    parser.add_argument(
        "--export-root",
        default=None,
        type=Path,
        help="folder holding the MATLAB RTK export, defaults to <data-root>/export",
    )
    parser.add_argument("--skip-gnss", action="store_true", help="chemistry sources only")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if not args.data_root.is_dir():
        print(f"no such folder: {args.data_root}", file=sys.stderr)
        return 1

    sources = discover(args.data_root, args.rate_hz)
    if not args.skip_gnss:
        sources += discover_gnss(args.data_root, args.export_root)
    if args.only:
        sources = [s for s in sources if any(fragment in s.mission_id for fragment in args.only)]

    if not sources:
        print("nothing to ingest", file=sys.stderr)
        return 1

    print(f"{len(sources)} source(s) under {args.data_root}\n")

    if args.dry_run:
        for source in sources:
            count = 0
            metrics: set[str] = set()
            positioned = 0
            for sample in source.samples:
                count += 1
                metrics.update(r.metric_key for r in sample.readings)
                if sample.latitude is not None:
                    positioned += 1
                if args.limit and count >= args.limit:
                    break
            print(
                f"  {source.mission_id:<44} {count:>7} samples  "
                f"{len(metrics):>2} metrics  {positioned:>7} with position"
            )
        return 0

    connection = psycopg.connect(default_dsn())
    rules = load_metric_rules(connection)
    connection.close()
    print(f"catalog: {len(rules)} metrics\n")

    sink = PostgresSink(default_dsn())
    started = time.monotonic()
    total_rows = 0
    total_suspect = 0
    try:
        for source in sources:
            if args.limit:
                source.samples = _take(source.samples, args.limit)
            begin = time.monotonic()
            report = load(source, sink, rules)
            total_rows += report.rows
            total_suspect += report.suspect
            print(
                f"  {report.mission_id:<44} {report.samples:>7} samples  {report.rows:>8} rows  "
                f"{report.suspect:>7} suspect  {time.monotonic() - begin:6.1f}s"
                + (f"  {report.skipped_unknown} unknown metric" if report.skipped_unknown else "")
            )
    finally:
        sink.close()

    # The load has finished arriving, which is exactly when the minute rollup
    # is allowed to be rebuilt (migration 005).
    elapsed = refresh_aggregates(default_dsn())

    print(
        f"\ndone: {total_rows} rows in {time.monotonic() - started:.1f}s, "
        f"{total_suspect} outside the catalog's plausible range, "
        f"observation_1min refreshed in {elapsed:.1f}s"
    )
    return 0


def _take(iterator, count: int):
    for index, item in enumerate(iterator):
        if index >= count:
            return
        yield item


if __name__ == "__main__":
    sys.exit(main())
