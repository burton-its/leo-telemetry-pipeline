"""Unit tests for the Ingestion stage. No real network calls are made."""

import unittest
from unittest.mock import MagicMock, patch

from ingest.models import RawFrame
from ingest.satnogs_client import SatNOGSAuthError, SatNOGSClient, deduplicate


def make_entry(frame_hex="c0ffee", observer="KJ6ABC", timestamp="2026-10-28T19:04:11Z"):
    """A fake SatNOGS telemetry entry shaped like the real API response."""
    return {"norad_cat_id": 25544, "timestamp": timestamp, "observer": observer,
            "station_id": 42, "frame": frame_hex}


def make_response(status=200, body=None, headers=None, next_url=None):
    """A fake requests.Response with just the parts our client uses."""
    response = MagicMock()
    response.status_code = status
    response.json.return_value = body if body is not None else {"results": [], "next": None}
    response.headers = headers or {}
    response.links = {"next": {"url": next_url}} if next_url else {}
    if status >= 400:
        response.raise_for_status.side_effect = Exception(f"HTTP {status}")
    return response


class TestRawFrame(unittest.TestCase):
    def test_from_api_parses_fields(self):
        frame = RawFrame.from_api(make_entry())
        self.assertEqual(frame.norad_id, 25544)
        self.assertEqual(frame.raw_bytes, b"\xc0\xff\xee")
        self.assertEqual(frame.timestamp.year, 2026)

    def test_from_api_rejects_missing_frame(self):
        with self.assertRaises(ValueError):
            RawFrame.from_api(make_entry(frame_hex=""))

    def test_same_bytes_from_two_stations_share_dedup_key(self):
        a = RawFrame.from_api(make_entry(observer="STATION-A"))
        b = RawFrame.from_api(make_entry(observer="STATION-B"))
        self.assertEqual(a.dedup_key, b.dedup_key)


class TestSatNOGSClient(unittest.TestCase):
    def setUp(self):
        self.client = SatNOGSClient("fake-token", max_retries=2)
        self.client.session = MagicMock()

    def test_requires_token(self):
        with self.assertRaises(ValueError):
            SatNOGSClient("")

    def test_follows_pagination(self):
        page1 = make_response(body={"results": [make_entry("aa")], "next": "https://next"})
        page2 = make_response(body={"results": [make_entry("bb")], "next": None})
        self.client.session.get.side_effect = [page1, page2]

        frames = self.client.fetch_frames(25544)

        self.assertEqual([f.raw_bytes for f in frames], [b"\xaa", b"\xbb"])
        self.assertEqual(self.client.session.get.call_count, 2)

    def test_follows_link_header_when_body_is_a_list(self):
        page1 = make_response(body=[make_entry("aa")], next_url="https://next")
        page2 = make_response(body=[make_entry("bb")])
        self.client.session.get.side_effect = [page1, page2]

        self.assertEqual(len(self.client.fetch_frames(25544)), 2)

    def test_stops_at_max_pages(self):
        endless = make_response(body={"results": [make_entry()], "next": "https://next"})
        self.client.session.get.return_value = endless

        self.client.fetch_frames(25544, max_pages=3)

        self.assertEqual(self.client.session.get.call_count, 3)

    def test_skips_malformed_entries(self):
        bad = make_entry(frame_hex="not-hex")
        body = {"results": [bad, make_entry("aa")], "next": None}
        self.client.session.get.return_value = make_response(body=body)

        self.assertEqual(len(self.client.fetch_frames(25544)), 1)

    @patch("ingest.satnogs_client.time.sleep")
    def test_retries_after_rate_limit(self, mock_sleep):
        limited = make_response(status=429, headers={"Retry-After": "5"})
        ok = make_response(body={"results": [make_entry()], "next": None})
        self.client.session.get.side_effect = [limited, ok]

        frames = self.client.fetch_frames(25544)

        self.assertEqual(len(frames), 1)
        mock_sleep.assert_called_once_with(5.0)

    @patch("ingest.satnogs_client.time.sleep")
    def test_gives_up_after_max_retries(self, mock_sleep):
        self.client.session.get.return_value = make_response(status=503)

        with self.assertRaises(Exception):
            self.client.fetch_frames(25544)
        self.assertEqual(self.client.session.get.call_count, 3)  # 1 try + 2 retries

    def test_bad_token_raises_auth_error(self):
        self.client.session.get.return_value = make_response(status=401)

        with self.assertRaises(SatNOGSAuthError):
            self.client.fetch_frames(25544)


class TestDeduplicate(unittest.TestCase):
    def test_keeps_first_copy_of_each_frame(self):
        frames = [RawFrame.from_api(make_entry("aa", observer="A")),
                  RawFrame.from_api(make_entry("aa", observer="B")),
                  RawFrame.from_api(make_entry("bb", observer="A"))]

        unique = deduplicate(frames)

        self.assertEqual([f.observer for f in unique], ["A", "A"])
        self.assertEqual(len(unique), 2)


if __name__ == "__main__":
    unittest.main()
