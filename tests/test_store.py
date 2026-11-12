"""Unit tests for the Storage stage. In-memory SQLite, no files except where noted."""

import json
import tempfile
import unittest
from collections import namedtuple
from datetime import datetime, timedelta, timezone
from pathlib import Path

from decode.registry import decode_frame
from ingest.models import RawFrame
from store.db import connect, insert_decoded, insert_tle_positions, load_decoded_jsonl
from store.export import export_all, summary_json
from store.track import build_track

try:
    import aprslib  # noqa: F401
except ImportError:
    aprslib = None

# Real frames captured from SatNOGS DB for NORAD 25544.
REAL_APRS_HEX = "829898404040e092ac6688b0ae64a4a66092a6a66103f04956335a5846205220353939"
REAL_JUNK_HEX = "ffff"
T0 = datetime(2026, 11, 4, 19, 57, 2, tzinfo=timezone.utc)


def _addr(call: str, last: bool) -> bytes:
    name, _, ssid = call.partition("-")
    shifted = bytes(ord(c) << 1 for c in name.ljust(6))
    return shifted + bytes([0x60 | (int(ssid or 0) << 1) | (1 if last else 0)])


def build_ui_frame(dest: str, src: str, path: list[str], info: str) -> bytes:
    calls = [dest, src] + path
    header = b"".join(_addr(c, last=(i == len(calls) - 1)) for i, c in enumerate(calls))
    return header + bytes([0x03, 0xF0]) + info.encode()


def decoded_row(raw: bytes, minutes: int = 0, norad_id: int = 25544) -> dict:
    frame = RawFrame(norad_id, T0 + timedelta(minutes=minutes), "TEST-STATION", 42, raw)
    return decode_frame(frame).to_dict()


class TestStore(unittest.TestCase):
    def setUp(self):
        self.conn = connect(":memory:")
        self.rows = [
            decoded_row(bytes.fromhex(REAL_APRS_HEX), 0),
            decoded_row(bytes.fromhex(REAL_JUNK_HEX), 1),
            decoded_row(build_ui_frame("APRS", "N0CALL-9", ["RS0ISS"], "!4903.50N/07201.75W-Test"), 2),
        ]

    def test_insert_and_idempotent_reload(self):
        self.assertEqual(insert_decoded(self.conn, self.rows), (3, 0))
        self.assertEqual(insert_decoded(self.conn, self.rows), (0, 3))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM frames").fetchone()[0], 3)

    def test_timestamps_stored_as_utc_iso_and_epoch(self):
        insert_decoded(self.conn, self.rows[:1])
        row = self.conn.execute("SELECT ts, ts_unix FROM frames").fetchone()
        self.assertEqual(row["ts"], "2026-11-04T19:57:02+00:00")
        self.assertEqual(row["ts_unix"], int(T0.timestamp()))

    def test_health_view_counts_rejects(self):
        insert_decoded(self.conn, self.rows)
        h = self.conn.execute("SELECT * FROM v_ingest_health").fetchone()
        self.assertEqual((h["total"], h["decoded"], h["rejected"]), (3, 2, 1))
        self.assertAlmostEqual(h["reject_pct"], 33.3, places=1)

    def test_iss_view_flattens_json_fields(self):
        insert_decoded(self.conn, self.rows)
        first = self.conn.execute("SELECT * FROM v_iss_frames ORDER BY ts_unix").fetchone()
        self.assertEqual(first["src"], "IV3DXW-2")
        self.assertEqual(first["dest"], "ALL")
        self.assertEqual(json.loads(first["path"]), ["RS0ISS"])
        self.assertEqual(first["message"], "IV3ZXF R 599")

    def test_rejected_frames_kept_but_hidden_from_views(self):
        insert_decoded(self.conn, self.rows)
        bad = self.conn.execute("SELECT ok, error FROM frames WHERE ok = 0").fetchone()
        self.assertIn("too short", bad["error"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM v_ok_frames").fetchone()[0], 2)

    def test_frames_json_nests_fields_and_sorts_newest_first(self):
        insert_decoded(self.conn, self.rows)
        with tempfile.TemporaryDirectory() as tmp:
            frames_path = export_all(self.conn, 25544, Path(tmp))[0]
            docs = json.loads(frames_path.read_text())
        self.assertEqual(len(docs), 2)  # rejected frame excluded
        self.assertGreater(docs[0]["ts_unix"], docs[1]["ts_unix"])
        self.assertIsInstance(docs[1]["fields"], dict)  # real JSON, not an escaped string
        self.assertEqual(docs[1]["fields"]["src"], "IV3DXW-2")

    def test_summary_json(self):
        insert_decoded(self.conn, self.rows)
        s = json.loads(summary_json(self.conn))
        self.assertEqual(s["satellites"][0]["total"], 3)
        self.assertIsInstance(s["packet_types"], list)
        self.assertTrue(s["generated_at"].endswith("Z"))

    def test_empty_database_exports_empty_arrays(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = export_all(self.conn, 25544, Path(tmp))
            self.assertEqual(json.loads(paths[0].read_text()), [])
            self.assertEqual(json.loads(paths[1].read_text()), [])
            self.assertEqual(json.loads(paths[2].read_text())["satellites"], [])

    @unittest.skipUnless(aprslib, "aprslib not installed")
    def test_positions_view_and_json(self):
        insert_decoded(self.conn, self.rows)
        pos = self.conn.execute("SELECT * FROM v_sender_positions").fetchall()
        self.assertEqual(len(pos), 1)
        self.assertEqual(pos[0]["sender"], "N0CALL-9")
        self.assertAlmostEqual(pos[0]["latitude"], 49.0583, places=3)
        self.assertAlmostEqual(pos[0]["longitude"], -72.0292, places=3)
        with tempfile.TemporaryDirectory() as tmp:
            docs = json.loads(export_all(self.conn, 25544, Path(tmp))[1].read_text())
        self.assertEqual(docs[0]["sender"], "N0CALL-9")

    @unittest.skipUnless(aprslib, "aprslib not installed")
    def test_positions_view_skips_iss_placeholder_position(self):
        # What the ISS really sends: 0 deg 00.34' N, 0 deg 10.00' E, i.e. no fix.
        placeholder = decoded_row(build_ui_frame("APRS", "NA1SS", ["RS0ISS"], "!0000.34N/00010.00E-"), 3)
        insert_decoded(self.conn, [placeholder])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM v_ok_frames").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM v_sender_positions").fetchone()[0], 0)


    def test_load_jsonl_skips_bad_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "25544.jsonl"
            path.write_text("\n".join([json.dumps(self.rows[0]), "not json", json.dumps({"x": 1}), ""]))
            with self.assertLogs("store.db", level="WARNING"):
                result = load_decoded_jsonl(self.conn, path)
        self.assertEqual(result, (1, 0, 2))


# Same fields as SatTelemetry in the mapping module (PR #6).
FakeTelemetry = namedtuple(
    "FakeTelemetry",
    "sat_name norad_id intl_designator utc_epoch tle_age_hours inclination_deg "
    "latitude_deg longitude_deg altitude_km speed_inertial_kms speed_earth_kms speed_ground_kms")


def fake_telemetry(lat=10.0, lon=20.0, alt=420.0):
    return FakeTelemetry("ISS (ZARYA)", 25544, "98067A", T0, 1.5, 51.64,
                         lat, lon, alt, 7.66, 7.4, 7.0)


class TestTlePositions(unittest.TestCase):
    def setUp(self):
        self.conn = connect(":memory:")

    def test_insert_then_update_same_moment(self):
        self.assertEqual(insert_tle_positions(self.conn, [(T0, fake_telemetry())]), 1)
        insert_tle_positions(self.conn, [(T0, fake_telemetry(alt=415.0))])
        rows = self.conn.execute("SELECT altitude_km, ts, tle_epoch FROM tle_positions").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["altitude_km"], 415.0)  # fresher value replaced the old one
        self.assertEqual(rows[0]["ts"], "2026-11-04T19:57:02+00:00")
        self.assertEqual(rows[0]["tle_epoch"], "2026-11-04T19:57:02+00:00")

    def test_accepts_plain_dicts(self):
        insert_tle_positions(self.conn, [(T0, fake_telemetry()._asdict())])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM tle_positions").fetchone()[0], 1)

    def test_requires_timezone_aware_time(self):
        with self.assertRaises(ValueError):
            insert_tle_positions(self.conn, [(datetime(2026, 11, 4, 19, 0), fake_telemetry())])

    def test_track_json_is_oldest_first_and_latest_ignores_future(self):
        now = datetime.now(timezone.utc).replace(microsecond=0)
        items = [(now + timedelta(minutes=m), fake_telemetry(lat=float(m))) for m in (-2, -1, 0, 5)]
        insert_tle_positions(self.conn, items)
        with tempfile.TemporaryDirectory() as tmp:
            paths = export_all(self.conn, 25544, Path(tmp))
            track = json.loads(paths[3].read_text())
            summary = json.loads(paths[2].read_text())
        self.assertEqual([d["latitude"] for d in track], [-2.0, -1.0, 0.0, 5.0])
        self.assertEqual(self.conn.execute("SELECT latitude FROM v_tle_latest").fetchone()[0], 0.0)
        self.assertEqual(summary["tle_latest"][0]["latitude"], 0.0)

    def test_empty_track_exports_empty_array(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = export_all(self.conn, 25544, Path(tmp))
            self.assertEqual(json.loads(paths[3].read_text()), [])
            self.assertEqual(json.loads(paths[2].read_text())["tle_latest"], [])

    def test_build_track_window_order_and_skipped_points(self):
        calls = []

        def fake_map(tle, when):
            calls.append(when)
            return None if when.minute == 5 else fake_telemetry()

        now = datetime(2026, 11, 4, 12, 0, 30, tzinfo=timezone.utc)
        track = build_track(("n", "1", "2"), fake_map, now=now, back=10, ahead=10, step=1)
        self.assertEqual(len(calls), 21)  # 11:50 .. 12:10 inclusive
        self.assertEqual(len(track), 20)  # 12:05 returned None and is skipped
        self.assertEqual(track[0][0], datetime(2026, 11, 4, 11, 50, tzinfo=timezone.utc))
        self.assertEqual(track[-1][0], datetime(2026, 11, 4, 12, 10, tzinfo=timezone.utc))
        self.assertEqual([w for w, _ in track], sorted(w for w, _ in track))


if __name__ == "__main__":
    unittest.main()