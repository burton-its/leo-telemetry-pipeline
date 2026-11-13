"""SQLite storage for the Ingestion stage: the raw frame archive.

Frames are saved exactly as SatNOGS sent them and never changed afterward,
so the Decode stage can always re-run on old data.
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from ingest.models import RawFrame
from store.db import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_frames (
    id          INTEGER PRIMARY KEY,
    norad_id    INTEGER NOT NULL,
    ts          TEXT    NOT NULL,      -- when the station heard it, ISO 8601 UTC
    observer    TEXT,
    station_id  INTEGER,
    raw_hex     TEXT    NOT NULL,      -- the frame exactly as received
    fetched_at  TEXT    NOT NULL,      -- when WE downloaded it
    decoded_at  TEXT,                  -- NULL = Decode hasn't processed it yet
    UNIQUE (norad_id, ts, raw_hex)     -- prevent duplicate frames
);
CREATE INDEX IF NOT EXISTS idx_raw_frames_pending ON raw_frames (norad_id, decoded_at);
"""


def connect(path: Path | str = DB_PATH) -> sqlite3.Connection:
    """Open the database and make sure the raw_frames table exists."""
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


INSERT_SQL = """
INSERT OR IGNORE INTO raw_frames (norad_id, ts, observer, station_id, raw_hex, fetched_at)
VALUES (?, ?, ?, ?, ?, ?)
"""


def save_frames(conn: sqlite3.Connection, frames: list[RawFrame]) -> int:
    """Save frames to raw_frames. Returns how many were new (duplicates are skipped)."""
    fetched_at = datetime.now(timezone.utc).isoformat()
    rows = [
        (
            frame.norad_id,
            frame.timestamp.astimezone(timezone.utc).isoformat(),
            frame.observer,
            frame.station_id,
            frame.raw_bytes.hex(),
            fetched_at,
        )
        for frame in frames
    ]
    before = conn.total_changes
    with conn:
        conn.executemany(INSERT_SQL, rows)
    return conn.total_changes - before
