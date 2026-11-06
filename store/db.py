"""SQLite storage for decoded frames.

One generic `frames` table holds what every satellite shares (time, station, raw
bytes, ok/error) plus a `fields` column with the satellite-specific parse as
JSON. Adding a satellite means adding a view, not migrating the table.

Dashboards should read the v_* views, not the table. Views are dropped and
recreated on every connect(), so editing a view here takes effect on next run.
Requires SQLite with JSON functions (3.38+, which any recent Python has).
"""

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)

DB_PATH = Path("data/leo.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS frames (
    id          INTEGER PRIMARY KEY,
    norad_id    INTEGER NOT NULL,
    ts          TEXT    NOT NULL,              -- ISO 8601, UTC
    ts_unix     INTEGER NOT NULL,              -- epoch seconds (handy for Grafana)
    observer    TEXT,
    station_id  INTEGER,
    raw_hex     TEXT    NOT NULL,
    ok          INTEGER NOT NULL CHECK (ok IN (0, 1)),
    crc_ok      INTEGER CHECK (crc_ok IN (0, 1)),   -- NULL = satellite has no CRC
    error       TEXT,                          -- why a frame was rejected
    packet_type TEXT,
    fields      TEXT    NOT NULL DEFAULT '{}' CHECK (json_valid(fields)),
    UNIQUE (norad_id, ts, raw_hex)             -- re-loading the same file adds nothing
);
CREATE INDEX IF NOT EXISTS idx_frames_sat_time ON frames (norad_id, ts_unix);

DROP VIEW IF EXISTS v_ok_frames;
CREATE VIEW v_ok_frames AS
SELECT * FROM frames WHERE ok = 1;

-- Health: one row per satellite. Reject rate is a useful number for the dev blog.
DROP VIEW IF EXISTS v_ingest_health;
CREATE VIEW v_ingest_health AS
SELECT norad_id,
       COUNT(*)                              AS total,
       SUM(ok)                               AS decoded,
       COUNT(*) - SUM(ok)                    AS rejected,
       ROUND(100.0 * (COUNT(*) - SUM(ok)) / COUNT(*), 1) AS reject_pct,
       MIN(ts)                               AS first_seen,
       MAX(ts)                               AS last_seen
FROM frames GROUP BY norad_id;

DROP VIEW IF EXISTS v_packet_type_counts;
CREATE VIEW v_packet_type_counts AS
SELECT norad_id, packet_type, COUNT(*) AS n
FROM frames WHERE ok = 1 GROUP BY norad_id, packet_type;

-- ISS (APRS digipeater) frames, flattened for plain SQL/Grafana tables.
DROP VIEW IF EXISTS v_iss_frames;
CREATE VIEW v_iss_frames AS
SELECT id, ts, ts_unix, observer, station_id, packet_type,
       json_extract(fields, '$.src')   AS src,
       json_extract(fields, '$.dest')  AS dest,
       json_extract(fields, '$.path')  AS path,
       COALESCE(json_extract(fields, '$.aprs.text'),
                json_extract(fields, '$.aprs.status'),
                json_extract(fields, '$.aprs.comment'),
                json_extract(fields, '$.info')) AS message
FROM frames WHERE norad_id = 25544 AND ok = 1;

-- Where the SENDER of each packet is (APRS position reports). On the ISS
-- digipeater this is usually the ground station or vehicle that transmitted.
-- The ISS's own packets have no real position, so they are filtered out below.
-- Units follow aprslib (speed km/h, altitude metres).
DROP VIEW IF EXISTS v_sender_positions;
CREATE VIEW v_sender_positions AS
SELECT id, ts, ts_unix, norad_id, packet_type,
       json_extract(fields, '$.src')            AS sender,
       json_extract(fields, '$.aprs.latitude')  AS latitude,
       json_extract(fields, '$.aprs.longitude') AS longitude,
       json_extract(fields, '$.aprs.speed')     AS speed,
       json_extract(fields, '$.aprs.course')    AS course,
       json_extract(fields, '$.aprs.altitude')  AS altitude
FROM frames
WHERE ok = 1
  AND json_extract(fields, '$.aprs.latitude') IS NOT NULL
  -- The ISS's own packets (RS0ISS, NA1SS) carry a dummy position near 0N 0E. Skip it.
  AND NOT (ABS(json_extract(fields, '$.aprs.latitude'))  < 0.5
       AND ABS(json_extract(fields, '$.aprs.longitude')) < 0.5);

-- JSON views: one ready-made JSON document per row, built by SQLite itself.
DROP VIEW IF EXISTS v_frames_json;
CREATE VIEW v_frames_json AS
SELECT norad_id, ts_unix,
       json_object('id', id, 'norad_id', norad_id, 'ts', ts, 'ts_unix', ts_unix,
                   'observer', observer, 'station_id', station_id,
                   'packet_type', packet_type, 'raw_hex', raw_hex,
                   'fields', json(fields)) AS doc
FROM frames WHERE ok = 1;

DROP VIEW IF EXISTS v_positions_json;
CREATE VIEW v_positions_json AS
SELECT norad_id, ts_unix,
       json_object('ts', ts, 'ts_unix', ts_unix, 'sender', sender,
                   'packet_type', packet_type, 'latitude', latitude,
                   'longitude', longitude, 'speed', speed, 'course', course,
                   'altitude', altitude) AS doc
FROM v_sender_positions;
"""

INSERT_SQL = """
INSERT OR IGNORE INTO frames
    (norad_id, ts, ts_unix, observer, station_id, raw_hex, ok, crc_ok, error, packet_type, fields)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def connect(path: Path | str = DB_PATH) -> sqlite3.Connection:
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("SELECT json('{}')")
    except sqlite3.OperationalError as err:
        raise RuntimeError("This Python's SQLite has no JSON support (needs SQLite 3.38+).") from err
    conn.executescript(SCHEMA)
    return conn


def _to_params(row: dict) -> tuple:
    """One line of data/decoded/<id>.jsonl -> INSERT parameters."""
    ts = datetime.fromisoformat(row["timestamp"]).astimezone(timezone.utc)
    fields = row.get("fields") or {}
    crc_ok = row.get("crc_ok")
    return (
        int(row["norad_id"]),
        ts.isoformat(),
        int(ts.timestamp()),
        row.get("observer"),
        row.get("station_id"),
        row["raw_hex"],
        int(bool(row["ok"])),
        None if crc_ok is None else int(bool(crc_ok)),
        row.get("error"),
        fields.get("packet_type"),
        json.dumps(fields),
    )


def _insert(conn: sqlite3.Connection, params: list[tuple]) -> int:
    before = conn.total_changes
    with conn:
        conn.executemany(INSERT_SQL, params)
    return conn.total_changes - before  # rows actually added (duplicates are ignored)


def insert_decoded(conn: sqlite3.Connection, rows: Iterable[dict]) -> tuple[int, int]:
    """Insert decoded-frame dicts. Returns (inserted, duplicates)."""
    params = [_to_params(r) for r in rows]
    inserted = _insert(conn, params)
    return inserted, len(params) - inserted


def load_decoded_jsonl(conn: sqlite3.Connection, path: Path) -> tuple[int, int, int]:
    """Load data/decoded/<id>.jsonl. Returns (inserted, duplicates, unreadable_lines)."""
    params, unreadable = [], 0
    with Path(path).open() as f:
        for line_no, line in enumerate(f, start=1):
            if not line.strip():
                continue
            try:
                params.append(_to_params(json.loads(line)))
            except (ValueError, KeyError, TypeError) as err:
                unreadable += 1
                logger.warning("%s line %d unreadable (%s)", path, line_no, err)
    inserted = _insert(conn, params)
    return inserted, len(params) - inserted, unreadable