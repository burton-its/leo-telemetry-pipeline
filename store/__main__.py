"""Command-line entry point: python -m store [NORAD_ID]

Loads data/decoded/<id>.jsonl into data/leo.db (safe to re-run: duplicates are
ignored), then writes the dashboard JSON files to data/export/.
"""

import logging
import sys
from pathlib import Path

from store.db import DB_PATH, connect, load_decoded_jsonl
from store.export import export_all

ISS_NORAD_ID = 25544
DECODED_DIR = Path("data/decoded")


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    argv = sys.argv[1:] if argv is None else argv
    norad_id = int(argv[0]) if argv else ISS_NORAD_ID

    in_path = DECODED_DIR / f"{norad_id}.jsonl"
    if not in_path.exists():
        print(f"{in_path} not found. Run `python -m decode {norad_id}` first.", file=sys.stderr)
        return 1

    conn = connect()
    inserted, duplicates, unreadable = load_decoded_jsonl(conn, in_path)
    total = conn.execute("SELECT COUNT(*) FROM frames WHERE norad_id = ?", (norad_id,)).fetchone()[0]
    print(f"Loaded {inserted} new frames ({duplicates} already in the database) into {DB_PATH}; "
          f"{total} total for NORAD {norad_id}")
    if unreadable:
        print(f"  {unreadable} unreadable line(s) skipped")

    for path in export_all(conn, norad_id):
        print(f"  wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())