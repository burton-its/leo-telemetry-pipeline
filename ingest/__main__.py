"""Command-line entry point: python -m ingest [NORAD_ID]

Fetches telemetry from SatNOGS and saves it to the raw_frames table in
data/leo.db (duplicates are skipped automatically).

For now it ALSO writes data/raw/<id>.jsonl, because the Decode stage still
reads that file. Remove write_jsonl() once Decode reads from raw_frames.
"""

import json
import logging
import os
import sqlite3
import sys
from datetime import timedelta
from pathlib import Path

from ingest import db
from ingest.models import RawFrame
from ingest.satnogs_client import SatNOGSAuthError, SatNOGSClient, deduplicate

ISS_NORAD_ID = 25544
OUTPUT_DIR = Path("data/raw")

OVERLAP = timedelta(hours=6)  # look back this far to catch late uploads
FIRST_RUN_PAGES = 5
CATCH_UP_PAGES = 50


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    token = os.environ.get("SATNOGS_DB_TOKEN")
    if not token:
        print("Set the SATNOGS_DB_TOKEN environment variable first.", file=sys.stderr)
        return 1
    norad_id = int(sys.argv[1]) if len(sys.argv) > 1 else ISS_NORAD_ID

    conn = db.connect()
    try:
        frames = fetch_since_checkpoint(SatNOGSClient(token), conn, norad_id)
        inserted = db.save_frames(conn, frames)
    except SatNOGSAuthError as err:
        print(err, file=sys.stderr)
        return 1
    finally:
        conn.close()
    print(f"Saved {inserted} new frames ({len(frames) - inserted} already stored) to {db.DB_PATH}")

    out_path = write_jsonl(norad_id, deduplicate(frames))
    print(f"Also wrote {out_path} for the Decode stage")
    return 0


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
