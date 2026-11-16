"""Command-line entry point: python -m ingest [NORAD_ID] [--every MINUTES]

Fetches telemetry from SatNOGS and saves it to the raw_frames table in
data/leo.db (duplicates are skipped automatically). With --every, it keeps
running and fetches again on that interval.

For now it ALSO writes data/raw/<id>.jsonl, because the Decode stage still
reads that file. Remove write_jsonl() once Decode reads from raw_frames.
"""

import argparse
import json
import logging
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from ingest import db
from ingest.models import RawFrame
from ingest.satnogs_client import SatNOGSAuthError, SatNOGSClient, deduplicate
from ingest.scheduler import run_every

ISS_NORAD_ID = 25544
OUTPUT_DIR = Path("data/raw")

OVERLAP = timedelta(hours=6)  # look back this far to catch late uploads
FIRST_RUN_PAGES = 5
CATCH_UP_PAGES = 50
MIN_INTERVAL_MINUTES = 15  # SatNOGS is free and volunteer-run, don't poll it harder than this


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args(argv)

    token = os.environ.get("SATNOGS_DB_TOKEN")
    if not token:
        print("Set the SATNOGS_DB_TOKEN environment variable first.", file=sys.stderr)
        return 1
    client = SatNOGSClient(token)

    try:
        if args.every is None:
            ingest_once(client, args.norad_id)
        else:
            logging.info("Fetching NORAD %s every %g minutes. Press Ctrl+C to stop.", args.norad_id, args.every)
            run_every(args.every * 60, lambda: ingest_once(client, args.norad_id),
                      fatal=(SatNOGSAuthError,), expected=(requests.RequestException,))
    except SatNOGSAuthError as err:
        print(err, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        logging.info("Stopped.")
    return 0


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m ingest", description="Fetch SatNOGS telemetry into data/leo.db.")
    parser.add_argument("norad_id", nargs="?", type=int, default=ISS_NORAD_ID,
                        help=f"satellite to fetch (default: {ISS_NORAD_ID}, the ISS)")
    parser.add_argument("--every", type=float, metavar="MINUTES",
                        help=f"keep running and fetch every MINUTES (at least {MIN_INTERVAL_MINUTES})")
    args = parser.parse_args(argv)
    if args.every is not None and args.every < MIN_INTERVAL_MINUTES:
        parser.error(f"--every must be at least {MIN_INTERVAL_MINUTES} minutes")
    return args


def ingest_once(client: SatNOGSClient, norad_id: int) -> int:
    """One fetch-and-save pass, logged to ingest_runs. Returns how many new frames were saved."""
    started_at = datetime.now(timezone.utc)
    frames: list[RawFrame] = []
    conn = db.connect()
    try:
        frames = fetch_since_checkpoint(client, conn, norad_id)
        inserted = db.save_frames(conn, frames)
        db.record_run(conn, norad_id, started_at, len(frames), inserted)
    except Exception as err:
        db.record_run(conn, norad_id, started_at, len(frames), 0, error=f"{type(err).__name__}: {err}")
        raise
    finally:
        conn.close()
    logging.info("Saved %d new frames (%d already stored) to %s", inserted, len(frames) - inserted, db.DB_PATH)

    out_path = write_jsonl(norad_id, deduplicate(frames))
    logging.info("Also wrote %s for the Decode stage", out_path)
    return inserted


def fetch_since_checkpoint(client: SatNOGSClient, conn: sqlite3.Connection, norad_id: int) -> list[RawFrame]:
    """Fetch frames newer than the last saved one, minus an overlap window."""
    checkpoint = db.get_checkpoint(conn, norad_id)
    if checkpoint is None:
        logging.info("NORAD %s: no checkpoint yet, fetching the newest %d pages", norad_id, FIRST_RUN_PAGES)
        return client.fetch_frames(norad_id, max_pages=FIRST_RUN_PAGES)

    start = (checkpoint - OVERLAP).strftime("%Y-%m-%dT%H:%M:%SZ")
    logging.info("NORAD %s: checkpoint %s, fetching frames since %s", norad_id, checkpoint.isoformat(), start)
    return client.fetch_frames(norad_id, start=start, max_pages=CATCH_UP_PAGES)


def write_jsonl(norad_id: int, frames: list[RawFrame]) -> Path:
    """TEMPORARY: Decode still reads this file. Delete once it reads raw_frames."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / f"{norad_id}.jsonl"
    with out_path.open("w") as f:
        for frame in frames:
            f.write(json.dumps(frame.to_dict()) + "\n")
    return out_path


if __name__ == "__main__":
    sys.exit(main())
