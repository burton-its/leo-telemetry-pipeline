"""JSON export for the dashboard. SQLite builds the JSON; Python only writes it to disk.

Files written to data/export/:
    <id>_frames.json     decoded frames, newest first (nested "fields" is real JSON)
    <id>_positions.json  APRS position reports (sender lat/lon), newest first
    summary.json         per-satellite health, packet-type counts, latest computed position
    <id>_track.json      computed (TLE/SGP4) positions, OLDEST first so a map can draw the path
"""

import sqlite3
from pathlib import Path

EXPORT_DIR = Path("data/export")
DEFAULT_LIMIT = 1000


def _json_array(conn: sqlite3.Connection, view: str, norad_id: int, limit: int,
                oldest_first: bool = False) -> str:
    # Take the newest `limit` rows, then order them for the reader.
    # json(doc) re-parses each document so it nests as JSON instead of an escaped string.
    order = "ASC" if oldest_first else "DESC"
    sql = f"""
        SELECT json_group_array(json(doc)) FROM (
            SELECT doc FROM (
                SELECT doc, ts_unix FROM {view} WHERE norad_id = ? ORDER BY ts_unix DESC LIMIT ?
            ) ORDER BY ts_unix {order}
        )"""
    return conn.execute(sql, (norad_id, limit)).fetchone()[0]


def summary_json(conn: sqlite3.Connection) -> str:
    sql = """
        SELECT json_object(
            'generated_at', strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),
            'satellites', json((SELECT json_group_array(json_object(
                'norad_id', norad_id, 'total', total, 'decoded', decoded,
                'rejected', rejected, 'reject_pct', reject_pct,
                'first_seen', first_seen, 'last_seen', last_seen))
                FROM v_ingest_health)),
            'packet_types', json((SELECT json_group_array(json_object(
                'norad_id', norad_id, 'packet_type', packet_type, 'count', n))
                FROM v_packet_type_counts)),
            'tle_latest', json((SELECT json_group_array(json_object(
                'norad_id', norad_id, 'sat_name', sat_name, 'ts', ts,
                'latitude', latitude, 'longitude', longitude,
                'altitude_km', altitude_km, 'speed_ground_kms', speed_ground_kms,
                'tle_age_hours', tle_age_hours))
                FROM v_tle_latest))
        )"""
    return conn.execute(sql).fetchone()[0]


def export_all(conn: sqlite3.Connection, norad_id: int, out_dir: Path = EXPORT_DIR,
               limit: int = DEFAULT_LIMIT) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    files = {
        out_dir / f"{norad_id}_frames.json": _json_array(conn, "v_frames_json", norad_id, limit),
        out_dir / f"{norad_id}_positions.json": _json_array(conn, "v_positions_json", norad_id, limit),
        out_dir / "summary.json": summary_json(conn),
        out_dir / f"{norad_id}_track.json": _json_array(conn, "v_tle_track_json", norad_id, limit,
                                                        oldest_first=True),
    }
    for path, text in files.items():
        path.write_text(text)
    return list(files)