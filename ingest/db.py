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

CREATE TABLE IF NOT EXISTS ingest_runs (
    id            INTEGER PRIMARY KEY,
    norad_id      INTEGER NOT NULL,
    started_at    TEXT    NOT NULL,      -- ISO 8601 UTC
    started_unix  INTEGER NOT NULL,      -- epoch seconds, for Grafana
    finished_at   TEXT    NOT NULL,
    status        TEXT    NOT NULL CHECK (status IN ('ok', 'error')),
    fetched       INTEGER NOT NULL,      -- frames SatNOGS sent us
    inserted      INTEGER NOT NULL,      -- frames that were new
    error         TEXT
);
CREATE INDEX IF NOT EXISTS idx_ingest_runs_sat_time ON ingest_runs (norad_id, started_unix);

-- One row per satellite: is ingestion healthy?
DROP VIEW IF EXISTS ingest_status;
CREATE VIEW ingest_status AS
SELECT norad_id,
       MAX(started_at)                                  AS last_run,
       MAX(CASE WHEN status = 'ok' THEN started_at END) AS last_success,
       COUNT(*)                                         AS total_runs,
       SUM(status = 'error')                            AS failed_runs,
       SUM(fetched)                                     AS frames_fetched,
       SUM(inserted)                                    AS frames_saved
FROM ingest_runs GROUP BY norad_id;
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


INSERT_RUN_SQL = """
INSERT INTO ingest_runs (norad_id, started_at, started_unix, finished_at, status, fetched, inserted, error)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
"""


def record_run(conn: sqlite3.Connection, norad_id: int, started_at: datetime,
               fetched: int, inserted: int, error: str | None = None) -> None:
    """Log one ingest run. Pass `error` if the run failed."""
    finished_at = datetime.now(timezone.utc)
    status = "error" if error else "ok"
    with conn:
        conn.execute(INSERT_RUN_SQL, (norad_id, started_at.isoformat(), int(started_at.timestamp()),
                                      finished_at.isoformat(), status, fetched, inserted, error))


def get_checkpoint(conn: sqlite3.Connection, norad_id: int) -> datetime | None:
    """Time of the newest frame saved for this satellite, or None if there are none yet.

    Frames stamped later than the moment we downloaded them come from a ground
    station with a wrong clock. They are ignored here so one bad timestamp
    can't push the checkpoint into the future.
    """
    row = conn.execute(
        "SELECT MAX(ts) FROM raw_frames WHERE norad_id = ? AND ts <= fetched_at",
        (norad_id,),
    ).fetchone()
    newest = row[0]
    if newest is None:
        return None
    return datetime.fromisoformat(newest)
