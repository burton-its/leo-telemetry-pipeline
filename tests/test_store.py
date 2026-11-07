"""Unit tests for the Storage stage. In-memory SQLite, no files except where noted."""

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from decode.registry import decode_frame
from ingest.models import RawFrame
from store.db import connect, insert_decoded, load_decoded_jsonl
from store.export import export_all, summary_json

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


if __name__ == "__main__":
    unittest.main()