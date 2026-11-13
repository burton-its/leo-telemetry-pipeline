"""Command-line entry point: python -m ingest [NORAD_ID]

Fetches telemetry from SatNOGS and saves it to the raw_frames table in
data/leo.db (duplicates are skipped automatically).

For now it ALSO writes data/raw/<id>.jsonl, because the Decode stage still
reads that file. Remove write_jsonl() once Decode reads from raw_frames.
"""

import json
import logging
import os
import sys
from pathlib import Path

from ingest import db
from ingest.models import RawFrame
from ingest.satnogs_client import SatNOGSAuthError, SatNOGSClient, deduplicate

ISS_NORAD_ID = 25544
OUTPUT_DIR = Path("data/raw")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    token = os.environ.get("SATNOGS_DB_TOKEN")
    if not token:
        print("Set the SATNOGS_DB_TOKEN environment variable first.", file=sys.stderr)
        return 1
    norad_id = int(sys.argv[1]) if len(sys.argv) > 1 else ISS_NORAD_ID

    try:
        frames = SatNOGSClient(token).fetch_frames(norad_id)
    except SatNOGSAuthError as err:
        print(err, file=sys.stderr)
        return 1

    conn = db.connect()
    try:
        inserted = db.save_frames(conn, frames)
    finally:
        conn.close()
    print(f"Saved {inserted} new frames ({len(frames) - inserted} already stored) to {db.DB_PATH}")

    out_path = write_jsonl(norad_id, deduplicate(frames))
    print(f"Also wrote {out_path} for the Decode stage")
    return 0


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
