"""Command-line entry point: python -m ingest [NORAD_ID]

Fetches telemetry, removes duplicates, and saves frames as JSON Lines
(one JSON object per line) so the Decode stage can read them.
"""

import json
import logging
import os
import sys
from pathlib import Path

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

    unique = deduplicate(frames)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / f"{norad_id}.jsonl"
    with out_path.open("w") as f:
        for frame in unique:
            f.write(json.dumps(frame.to_dict()) + "\n")

    print(f"Saved {len(unique)} unique frames ({len(frames) - len(unique)} duplicates) to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
