"""Tests for ingest/db.py. Uses in-memory or temporary databases, so real data is never touched."""

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

from ingest import db
from ingest.__main__ import CATCH_UP_PAGES, FIRST_RUN_PAGES, fetch_since_checkpoint, ingest_once
from ingest.models import RawFrame
from store import db as store_db


def make_frame(frame_hex="c0ffee", timestamp="2026-10-28T19:04:11Z", observer="KJ6ABC", norad_id=25544):
    """A RawFrame built the same way the real client builds one."""
    return RawFrame.from_api({"norad_cat_id": norad_id, "timestamp": timestamp,
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


class TestCheckpoint(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")

    def tearDown(self):
        self.conn.close()

    def test_no_frames_means_no_checkpoint(self):
        self.assertIsNone(db.get_checkpoint(self.conn, 25544))

    def test_checkpoint_is_newest_frame_time(self):
        db.save_frames(self.conn, [make_frame("aa", timestamp="2026-10-28T19:00:00Z"),
                                   make_frame("bb", timestamp="2026-10-28T21:30:00Z"),
                                   make_frame("cc", timestamp="2026-10-28T20:15:00Z")])

        self.assertEqual(db.get_checkpoint(self.conn, 25544),
                         datetime(2026, 10, 28, 21, 30, tzinfo=timezone.utc))

    def test_checkpoint_is_per_satellite(self):
        db.save_frames(self.conn, [make_frame("aa", timestamp="2026-10-28T19:00:00Z"),
                                   make_frame("bb", timestamp="2026-10-29T08:00:00Z", norad_id=99999)])

        self.assertEqual(db.get_checkpoint(self.conn, 25544),
                         datetime(2026, 10, 28, 19, 0, tzinfo=timezone.utc))

    def test_ignores_frames_stamped_in_the_future(self):
        db.save_frames(self.conn, [make_frame("aa", timestamp="2026-10-28T19:00:00Z"),
                                   make_frame("bb", timestamp="2099-01-01T00:00:00Z")])

        self.assertEqual(db.get_checkpoint(self.conn, 25544),
                         datetime(2026, 10, 28, 19, 0, tzinfo=timezone.utc))


class TestFetchSinceCheckpoint(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        self.client = MagicMock()
        self.client.fetch_frames.return_value = []

    def tearDown(self):
        self.conn.close()

    def test_first_run_fetches_newest_pages(self):
        fetch_since_checkpoint(self.client, self.conn, 25544)

        self.client.fetch_frames.assert_called_once_with(25544, max_pages=FIRST_RUN_PAGES)

    def test_later_runs_start_before_checkpoint(self):
        db.save_frames(self.conn, [make_frame(timestamp="2026-10-28T19:00:00Z")])

        fetch_since_checkpoint(self.client, self.conn, 25544)

        self.client.fetch_frames.assert_called_once_with(
            25544, start="2026-10-28T13:00:00Z", max_pages=CATCH_UP_PAGES)


class TestRecordRun(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        self.started = datetime(2026, 11, 12, 18, 0, tzinfo=timezone.utc)

    def tearDown(self):
        self.conn.close()

    def test_records_successful_run(self):
        db.record_run(self.conn, 25544, self.started, fetched=42, inserted=7)
        row = self.conn.execute("SELECT * FROM ingest_runs").fetchone()

        self.assertEqual(row["status"], "ok")
        self.assertEqual(row["fetched"], 42)
        self.assertEqual(row["inserted"], 7)
        self.assertEqual(row["started_at"], "2026-11-12T18:00:00+00:00")
        self.assertEqual(row["started_unix"], int(self.started.timestamp()))
        self.assertIsNone(row["error"])

    def test_records_failed_run(self):
        db.record_run(self.conn, 25544, self.started, fetched=0, inserted=0, error="ConnectionError: down")
        row = self.conn.execute("SELECT * FROM ingest_runs").fetchone()

        self.assertEqual(row["status"], "error")
        self.assertEqual(row["error"], "ConnectionError: down")

    def test_status_view_summarizes_runs(self):
        db.record_run(self.conn, 25544, datetime(2026, 11, 12, 18, 0, tzinfo=timezone.utc), 40, 10)
        db.record_run(self.conn, 25544, datetime(2026, 11, 12, 18, 30, tzinfo=timezone.utc), 30, 5)
        db.record_run(self.conn, 25544, datetime(2026, 11, 12, 19, 0, tzinfo=timezone.utc), 0, 0, error="boom")
        row = self.conn.execute("SELECT * FROM ingest_status WHERE norad_id = 25544").fetchone()

        self.assertEqual(row["total_runs"], 3)
        self.assertEqual(row["failed_runs"], 1)
        self.assertEqual(row["frames_fetched"], 70)
        self.assertEqual(row["frames_saved"], 15)
        self.assertEqual(row["last_run"], "2026-11-12T19:00:00+00:00")
        self.assertEqual(row["last_success"], "2026-11-12T18:30:00+00:00")


class TestIngestOnceRecordsRuns(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db_path = Path(tmp.name) / "test.db"
        self.real_connect = db.connect
        for target, value in [("ingest.__main__.db.connect", lambda: self.real_connect(self.db_path)),
                              ("ingest.__main__.OUTPUT_DIR", Path(tmp.name) / "raw")]:
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = MagicMock()

    def runs(self):
        conn = self.real_connect(self.db_path)
        try:
            return [dict(r) for r in conn.execute("SELECT * FROM ingest_runs")]
        finally:
            conn.close()

    def test_successful_run_is_recorded(self):
        self.client.fetch_frames.return_value = [make_frame("aa"), make_frame("bb"), make_frame("aa")]

        inserted = ingest_once(self.client, 25544)

        self.assertEqual(inserted, 2)
        [run] = self.runs()
        self.assertEqual((run["status"], run["fetched"], run["inserted"]), ("ok", 3, 2))

    def test_failed_run_is_recorded_and_error_still_raised(self):
        self.client.fetch_frames.side_effect = requests.ConnectionError("network down")

        with self.assertRaises(requests.ConnectionError):
            ingest_once(self.client, 25544)

        [run] = self.runs()
        self.assertEqual(run["status"], "error")
        self.assertIn("ConnectionError: network down", run["error"])


if __name__ == "__main__":
    unittest.main()
