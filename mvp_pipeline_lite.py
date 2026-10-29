"""Lite telemetry pipeline: parse -> positions -> export -> summary.

Works on an existing telemetry.db that already holds raw_frames (ingestion
and plotting live in mvp_pipeline.py).

Usage:
    python mvp_pipeline_lite.py parse
    python mvp_pipeline_lite.py positions
    python mvp_pipeline_lite.py export --out positions.csv
    python mvp_pipeline_lite.py export-frames --out frames.csv
    python mvp_pipeline_lite.py summary

Stages:
    parse         - CRC check (crcmod), AX.25 header/payload extraction (ax253),
                    and APRS decoding of the payload (aprslib)
    positions     - flatten lat/lon/speed/course/altitude from the APRS JSON
                    into a `positions` table (export reads from it)
    export        - positions CSV (deduplicated; the ISS's placeholder
                    positions are excluded by default)
    export-frames - frame log CSV: reception time, station, sender, path, format
    summary       - counts, CRC pass rate, top senders, APRS formats, frames per day

Stored frames carry no FCS (SatNOGS strips it upstream). Frames whose CRC
does pass have the FCS removed before parsing; the rest are parsed whole.
"""

import argparse
import csv
import json
import sqlite3
import sys

import aprslib
import ax253
import crcmod.predefined

DB_PATH = "telemetry.db"

# CRC-16/X-25 is the AX.25 frame check sequence.
_crc_x25 = crcmod.predefined.mkCrcFun("x-25")

# raw_frames is only declared so queries work on an empty database;
# this script never writes to it.
SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_frames (
    id            INTEGER PRIMARY KEY,
    norad         INTEGER NOT NULL,
    observation_id INTEGER,
    station_id    INTEGER,
    timestamp     TEXT NOT NULL,
    frame_hex     TEXT NOT NULL,
    decoded_text  TEXT,
    ingested_at   TEXT NOT NULL,
    UNIQUE (norad, timestamp, frame_hex, station_id)
);

CREATE TABLE IF NOT EXISTS parsed_frames (
    frame_id    INTEGER PRIMARY KEY REFERENCES raw_frames (id),
    timestamp   TEXT NOT NULL,
    crc_ok      INTEGER,
    dest        TEXT,
    src         TEXT,
    src_ssid    INTEGER,
    via         TEXT,
    control     INTEGER,
    pid         INTEGER,
    info_len    INTEGER,
    info_text   TEXT,
    aprs_format TEXT,
    aprs_json   TEXT,
    parse_error TEXT
);
CREATE INDEX IF NOT EXISTS idx_parsed_ts ON parsed_frames (timestamp);

CREATE TABLE IF NOT EXISTS positions (
    frame_id    INTEGER PRIMARY KEY REFERENCES raw_frames (id),
    timestamp   TEXT NOT NULL,
    src         TEXT,
    src_ssid    INTEGER,
    via         TEXT,
    aprs_format TEXT,
    latitude    REAL,
    longitude   REAL,
    speed_kmh   REAL,
    course_deg  REAL,
    altitude_m  REAL,
    comment     TEXT
);
CREATE INDEX IF NOT EXISTS idx_pos_ts ON positions (timestamp);
CREATE INDEX IF NOT EXISTS idx_pos_src ON positions (src);
"""


def connect():
    db = sqlite3.connect(DB_PATH)
    db.executescript(SCHEMA)
    return db


# ---------- parse ----------

def printable(b: bytes) -> str:
    return "".join(chr(c) if 32 <= c < 127 else "." for c in b)


def check_fcs(frame: bytes):
    """True/False if the last two bytes match the CRC of the rest, None if too short."""
    if len(frame) <= 2:
        return None
    return _crc_x25(frame[:-2]) == int.from_bytes(frame[-2:], "little")


def parse_aprs(f: "ax253.Frame"):
    """Decode a UI frame's payload as APRS. Returns (format, json) or (None, None)."""
    if f.control.v != b"\x03" or f.pid != b"\xf0":
        return None, None
    path = "".join("," + str(p) for p in f.path)
    tnc2 = f"{f.source}>{f.destination}{path}:{f.info.decode('latin-1')}"
    try:
        result = aprslib.parse(tnc2)
    except Exception:  # aprslib raises its own ParseError/UnknownFormat family
        return None, None
    return result.get("format"), json.dumps(result, default=str)


def cmd_parse(args):
    db = connect()
    rows = db.execute(
        "SELECT id, timestamp, frame_hex FROM raw_frames "
        "WHERE id NOT IN (SELECT frame_id FROM parsed_frames)"
    ).fetchall()
    print(f"Parsing {len(rows)} unparsed frames...")

    ok = errors = 0
    for fid, ts, hexstr in rows:
        try:
            raw = bytes.fromhex(hexstr)
        except ValueError:
            db.execute(
                "INSERT INTO parsed_frames (frame_id, timestamp, parse_error) VALUES (?,?,?)",
                (fid, ts, "invalid hex"),
            )
            errors += 1
            continue

        crc = check_fcs(raw)
        crc_ok = None if crc is None else int(crc)
        body = raw[:-2] if crc else raw  # drop the FCS once we know it's real

        try:
            f = ax253.Frame.from_bytes(body)
        except ValueError as e:
            db.execute(
                "INSERT INTO parsed_frames (frame_id, timestamp, crc_ok, parse_error) VALUES (?,?,?,?)",
                (fid, ts, crc_ok, str(e) or "not an AX.25 frame"),
            )
            errors += 1
            continue

        aprs_format, aprs_json = parse_aprs(f)
        db.execute(
            "INSERT INTO parsed_frames "
            "(frame_id, timestamp, crc_ok, dest, src, src_ssid, via, control, pid, "
            " info_len, info_text, aprs_format, aprs_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                fid, ts, crc_ok,
                f.destination.callsign.decode("latin-1").strip(),
                f.source.callsign.decode("latin-1").strip(),
                f.source.ssid,
                ",".join(str(p) for p in f.path) or None,
                int.from_bytes(f.control.v, "big"),
                f.pid[0] if f.pid else None,
                len(f.info),
                printable(f.info),
                aprs_format,
                aprs_json,
            ),
        )
        ok += 1
    db.commit()
    print(f"Parsed {ok} frames, {errors} errors.")


# ---------- positions / export ----------

def cmd_positions(args):
    """Flatten position data from aprs_json into the positions table."""
    db = connect()
    rows = db.execute(
        "SELECT frame_id, timestamp, src, src_ssid, via, aprs_format, aprs_json "
        "FROM parsed_frames "
        "WHERE aprs_json IS NOT NULL AND frame_id NOT IN (SELECT frame_id FROM positions)"
    ).fetchall()

    added = skipped = 0
    for fid, ts, src, ssid, via, fmt, js in rows:
        try:
            d = json.loads(js)
        except ValueError:
            skipped += 1
            continue
        lat, lon = d.get("latitude"), d.get("longitude")
        if lat is None or lon is None:
            skipped += 1  # APRS frame with no position (message, status, ...)
            continue
        db.execute(
            "INSERT INTO positions (frame_id, timestamp, src, src_ssid, via, aprs_format, "
            "latitude, longitude, speed_kmh, course_deg, altitude_m, comment) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (fid, ts, src, ssid, via, fmt, lat, lon,
             d.get("speed"), d.get("course"), d.get("altitude"), d.get("comment") or None),
        )
        added += 1
    db.commit()
    print(f"Added {added} position rows ({skipped} APRS frames had no position).")


# The ISS's own APRS beacon (RS0ISS) carries a placeholder position near
# 0N 0E with zero speed/course, so its rows are excluded unless requested.
ISS_SRC = "RS0ISS"


def write_csv(path, cols, rows):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        w.writerows(rows)
    print(f"Wrote {len(rows)} rows to {path}")


def cmd_export(args):
    """Positions CSV: real positions reported by other stations the ISS relayed."""
    db = connect()
    where, params = [], []
    if args.src:
        where.append("src = ?")
        params.append(args.src)
    elif not args.include_iss:
        where.append("src != ?")
        params.append(ISS_SRC)
    clause = ("WHERE " + " AND ".join(where)) if where else ""

    # The same packet is often heard by several stations within seconds, so
    # collapse identical readings from one sender inside the same minute.
    rows = db.execute(
        "SELECT MIN(timestamp), src, src_ssid, aprs_format, latitude, longitude, "
        "       speed_kmh, course_deg, altitude_m "
        f"FROM positions {clause} "
        "GROUP BY src, src_ssid, latitude, longitude, speed_kmh, course_deg, altitude_m, "
        "         substr(timestamp, 1, 16) "
        "ORDER BY MIN(timestamp)",
        params,
    ).fetchall()
    write_csv(
        args.out,
        ["timestamp", "src", "src_ssid", "aprs_format", "latitude", "longitude",
         "speed_kmh", "course_deg", "altitude_m"],
        rows,
    )


def cmd_export_frames(args):
    """Frame log CSV: one row per successfully parsed frame, with reception time."""
    db = connect()
    where, params = ["p.parse_error IS NULL"], []
    if args.src:
        where.append("p.src = ?")
        params.append(args.src)
    rows = db.execute(
        "SELECT r.timestamp, r.station_id, r.observation_id, p.src, p.src_ssid, "
        "       p.dest, p.via, p.aprs_format, p.info_len "
        "FROM parsed_frames p JOIN raw_frames r ON r.id = p.frame_id "
        f"WHERE {' AND '.join(where)} ORDER BY r.timestamp",
        params,
    ).fetchall()
    write_csv(
        args.out,
        ["timestamp_utc", "station_id", "observation_id", "src", "src_ssid",
         "dest", "via", "aprs_format", "payload_bytes"],
        rows,
    )


# ---------- summary ----------

def cmd_summary(args):
    db = connect()
    q = lambda sql: db.execute(sql).fetchall()

    raw = q("SELECT COUNT(*), MIN(timestamp), MAX(timestamp) FROM raw_frames")[0]
    print(f"raw frames: {raw[0]}  ({raw[1]} -> {raw[2]})")

    n, parsed_ok, crc_ok, crc_known = q(
        "SELECT COUNT(*), SUM(parse_error IS NULL), SUM(crc_ok = 1), SUM(crc_ok IS NOT NULL) "
        "FROM parsed_frames"
    )[0]
    print(f"parsed: {n}  clean parse: {parsed_ok}  CRC pass: {crc_ok} of {crc_known} checked")
    if crc_known and not crc_ok:
        print("  -> 0 CRC passes: the FCS is probably stripped from stored frames.")

    print("\ntop senders:")
    for src, ssid, c in q(
        "SELECT src, src_ssid, COUNT(*) c FROM parsed_frames "
        "WHERE src IS NOT NULL GROUP BY src, src_ssid ORDER BY c DESC LIMIT 10"
    ):
        print(f"  {src}-{ssid:<2} {c}")

    print("\nAPRS formats:")
    for fmt, c in q(
        "SELECT COALESCE(aprs_format, '(not APRS)'), COUNT(*) c FROM parsed_frames "
        "WHERE parse_error IS NULL GROUP BY 1 ORDER BY c DESC"
    ):
        print(f"  {fmt:<14} {c}")

    print("\nframes per day:")
    for d, c in q(
        "SELECT substr(timestamp,1,10) d, COUNT(*) FROM parsed_frames GROUP BY d ORDER BY d"
    ):
        print(f"  {d}  {c}")

    print("\nsample payloads:")
    for ts, src, info in q(
        "SELECT timestamp, src, info_text FROM parsed_frames "
        "WHERE info_text IS NOT NULL ORDER BY timestamp DESC LIMIT 5"
    ):
        print(f"  {ts} {src}: {info[:70]}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("parse").set_defaults(fn=cmd_parse)
    sub.add_parser("positions").set_defaults(fn=cmd_positions)

    p = sub.add_parser("export", help="positions CSV (placeholder ISS positions excluded)")
    p.add_argument("--src", help="only this sender")
    p.add_argument("--include-iss", action="store_true", help="include RS0ISS's placeholder positions")
    p.add_argument("--out", default="positions.csv")
    p.set_defaults(fn=cmd_export)

    p = sub.add_parser("export-frames", help="frame log CSV with reception timestamps")
    p.add_argument("--src", help="only this sender, e.g. RS0ISS")
    p.add_argument("--out", default="frames.csv")
    p.set_defaults(fn=cmd_export_frames)

    sub.add_parser("summary").set_defaults(fn=cmd_summary)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
