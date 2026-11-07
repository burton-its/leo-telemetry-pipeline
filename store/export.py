"""JSON export for the dashboard. SQLite builds the JSON; Python only writes it to disk.

Files written to data/export/:
    <id>_frames.json     decoded frames, newest first (nested "fields" is real JSON)
    <id>_positions.json  APRS position reports (sender lat/lon), newest first
    summary.json         per-satellite health and packet-type counts
"""

import sqlite3
from pathlib import Path

EXPORT_DIR = Path("data/export")
DEFAULT_LIMIT = 1000


def _json_array(conn: sqlite3.Connection, view: str, norad_id: int, limit: int) -> str:
    # json(doc) re-parses each document so it nests as JSON instead of an escaped string.
    sql = f"""
        SELECT json_group_array(json(doc)) FROM (
            SELECT doc FROM {view} WHERE norad_id = ? ORDER BY ts_unix DESC LIMIT ?
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
                FROM v_packet_type_counts))
        )"""
    return conn.execute(sql).fetchone()[0]


def export_all(conn: sqlite3.Connection, norad_id: int, out_dir: Path = EXPORT_DIR,
               limit: int = DEFAULT_LIMIT) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    files = {
        out_dir / f"{norad_id}_frames.json": _json_array(conn, "v_frames_json", norad_id, limit),
        out_dir / f"{norad_id}_positions.json": _json_array(conn, "v_positions_json", norad_id, limit),
        out_dir / "summary.json": summary_json(conn),
    }
    for path, text in files.items():
        path.write_text(text)
    return list(files)