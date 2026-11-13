"""Tests for ingest/db.py. Uses in-memory databases, so no real files are touched."""

import unittest

from ingest import db
from ingest.models import RawFrame
from store import db as store_db


def make_frame(frame_hex="c0ffee", timestamp="2026-10-28T19:04:11Z", observer="KJ6ABC"):
    """A RawFrame built the same way the real client builds one."""
    return RawFrame.from_api({"norad_cat_id": 25544, "timestamp": timestamp,
                              "observer": observer, "station_id": 42, "frame": frame_hex})


class TestSaveFrames(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")

    def tearDown(self):
        self.conn.close()

    def count_rows(self):
        return self.conn.execute("SELECT COUNT(*) FROM raw_frames").fetchone()[0]

    def test_saves_new_frames(self):
        inserted = db.save_frames(self.conn, [make_frame("aa"), make_frame("bb")])

        self.assertEqual(inserted, 2)
        self.assertEqual(self.count_rows(), 2)

    def test_skips_duplicates_across_runs(self):
        db.save_frames(self.conn, [make_frame("aa")])
        inserted = db.save_frames(self.conn, [make_frame("aa")])

        self.assertEqual(inserted, 0)
        self.assertEqual(self.count_rows(), 1)

    def test_keeps_same_bytes_sent_at_different_times(self):
        beacons = [make_frame("aa", timestamp="2026-10-28T19:00:00Z"),
                   make_frame("aa", timestamp="2026-10-28T19:05:00Z")]

        self.assertEqual(db.save_frames(self.conn, beacons), 2)

    def test_stores_frame_exactly_as_received(self):
        db.save_frames(self.conn, [make_frame("c0ffee")])
        row = self.conn.execute("SELECT * FROM raw_frames").fetchone()

        self.assertEqual(row["raw_hex"], "c0ffee")
        self.assertEqual(row["ts"], "2026-10-28T19:04:11+00:00")
        self.assertEqual(row["observer"], "KJ6ABC")
        self.assertEqual(row["station_id"], 42)

    def test_new_frames_are_pending_decode(self):
        db.save_frames(self.conn, [make_frame()])
        row = self.conn.execute("SELECT decoded_at FROM raw_frames").fetchone()

        self.assertIsNone(row["decoded_at"])


class TestMatchesDecodedStore(unittest.TestCase):
    def test_timestamps_line_up_with_frames_table(self):
        conn = store_db.connect(":memory:")
        conn.executescript(db.SCHEMA)
        frame = make_frame("aa", timestamp="2026-10-28T12:04:11-07:00")

        db.save_frames(conn, [frame])
        store_db.insert_decoded(conn, [{"norad_id": 25544, "timestamp": frame.timestamp.isoformat(),
                                        "raw_hex": "aa", "ok": True}])
        matches = conn.execute(
            "SELECT COUNT(*) FROM raw_frames r JOIN frames f "
            "ON r.norad_id = f.norad_id AND r.ts = f.ts AND r.raw_hex = f.raw_hex"
        ).fetchone()[0]

        self.assertEqual(matches, 1)
        conn.close()


if __name__ == "__main__":
    unittest.main()
