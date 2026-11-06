"""Command-line entry point: python -m decode [NORAD_ID]

Reads data/raw/<id>.jsonl (written by `python -m ingest`), decodes every frame
through the registry, and writes data/decoded/<id>.jsonl. Every input frame
produces one output line; filter on "ok": true for clean rows.
"""

import json
import logging
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

from decode.registry import decode_frame, get_spec
from ingest.models import RawFrame

ISS_NORAD_ID = 25544
RAW_DIR = Path("data/raw")
OUT_DIR = Path("data/decoded")

logger = logging.getLogger(__name__)


def raw_frame_from_dict(d: dict) -> RawFrame:
    """Inverse of RawFrame.to_dict()."""
    return RawFrame(
        norad_id=int(d["norad_id"]),
        timestamp=datetime.fromisoformat(d["timestamp"]),
        observer=d.get("observer") or "unknown",
        station_id=d.get("station_id"),
        raw_bytes=bytes.fromhex(d["raw_bytes"]),
    )


def load_raw_frames(path: Path) -> tuple[list[RawFrame], int]:
    """Return (frames, number of unreadable lines skipped)."""
    frames, skipped = [], 0
    with path.open() as f:
        for line_no, line in enumerate(f, start=1):
            if not line.strip():
                continue
            try:
                frames.append(raw_frame_from_dict(json.loads(line)))
            except (ValueError, KeyError, TypeError) as err:
                skipped += 1
                logger.warning("%s line %d unreadable (%s)", path, line_no, err)
    return frames, skipped


def run(norad_id: int, raw_dir: Path = RAW_DIR, out_dir: Path = OUT_DIR) -> dict:
    in_path = raw_dir / f"{norad_id}.jsonl"
    frames, skipped = load_raw_frames(in_path)

    decoded = [decode_frame(frame) for frame in frames]
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{norad_id}.jsonl"
    with out_path.open("w") as f:
        for d in decoded:
            f.write(json.dumps(d.to_dict()) + "\n")

    return {
        "out_path": out_path,
        "total": len(decoded),
        "ok": sum(d.ok for d in decoded),
        "unreadable_lines": skipped,
        "errors": Counter(d.error for d in decoded if not d.ok),
        "types": Counter(d.fields.get("packet_type") for d in decoded if d.ok),
    }


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    argv = sys.argv[1:] if argv is None else argv
    norad_id = int(argv[0]) if argv else ISS_NORAD_ID

    if get_spec(norad_id) is None:
        print(f"No decoder registered for NORAD {norad_id}. Add one in decode/registry.py.", file=sys.stderr)
        return 1
    if not (RAW_DIR / f"{norad_id}.jsonl").exists():
        print(f"{RAW_DIR / f'{norad_id}.jsonl'} not found. Run `python -m ingest {norad_id}` first.", file=sys.stderr)
        return 1

    s = run(norad_id)
    print(f"Decoded {s['ok']}/{s['total']} frames to {s['out_path']}")
    for reason, count in s["errors"].most_common():
        print(f"  rejected x{count}: {reason}")
    for kind, count in s["types"].most_common():
        print(f"  {kind}: {count}")
    if s["unreadable_lines"]:
        print(f"  {s['unreadable_lines']} unreadable input line(s) skipped")
    return 0


if __name__ == "__main__":
    sys.exit(main())