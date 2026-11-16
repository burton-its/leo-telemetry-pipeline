"""Tests for the ingest scheduler and command-line options. Nothing really sleeps."""

import contextlib
import io
import unittest
from unittest.mock import MagicMock

from ingest.__main__ import ISS_NORAD_ID, parse_args
from ingest.scheduler import run_every


class TestRunEvery(unittest.TestCase):
    def test_runs_job_and_sleeps_between_runs(self):
        job, sleep = MagicMock(), MagicMock()

        run_every(1800, job, sleep=sleep, max_runs=3)

        self.assertEqual(job.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    def test_sleep_subtracts_time_the_run_took(self):
        sleep = MagicMock()
        clock = MagicMock(side_effect=[0, 100, 1800])

        run_every(1800, MagicMock(), sleep=sleep, clock=clock, max_runs=2)

        sleep.assert_called_once_with(1700)

    def test_slow_run_does_not_sleep_negative(self):
        sleep = MagicMock()
        clock = MagicMock(side_effect=[0, 2000, 2000])

        run_every(1800, MagicMock(), sleep=sleep, clock=clock, max_runs=2)

        sleep.assert_called_once_with(0.0)

    def test_failed_run_is_logged_and_loop_keeps_going(self):
        job = MagicMock(side_effect=[RuntimeError("SatNOGS is down"), None])

        with self.assertLogs("ingest.scheduler", level="ERROR"):
            run_every(1800, job, sleep=MagicMock(), max_runs=2)

        self.assertEqual(job.call_count, 2)

    def test_expected_error_logs_one_line_without_traceback(self):
        job = MagicMock(side_effect=[ConnectionError("network down"), None])

        with self.assertLogs("ingest.scheduler", level="ERROR") as logs:
            run_every(1800, job, expected=(ConnectionError,), sleep=MagicMock(), max_runs=2)

        self.assertEqual(len(logs.records), 1)
        self.assertIn("network down", logs.output[0])
        self.assertIsNone(logs.records[0].exc_info)
        self.assertEqual(job.call_count, 2)

    def test_fatal_error_stops_the_loop(self):
        job = MagicMock(side_effect=ValueError("bad token"))

        with self.assertRaises(ValueError):
            run_every(1800, job, fatal=(ValueError,), sleep=MagicMock(), max_runs=5)

        self.assertEqual(job.call_count, 1)


class TestParseArgs(unittest.TestCase):
    def test_defaults_to_iss_and_single_run(self):
        args = parse_args([])

        self.assertEqual(args.norad_id, ISS_NORAD_ID)
        self.assertIsNone(args.every)

    def test_reads_satellite_and_interval(self):
        args = parse_args(["40069", "--every", "30"])

        self.assertEqual(args.norad_id, 40069)
        self.assertEqual(args.every, 30)

    def test_rejects_interval_under_minimum(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parse_args(["--every", "1"])


if __name__ == "__main__":
    unittest.main()
