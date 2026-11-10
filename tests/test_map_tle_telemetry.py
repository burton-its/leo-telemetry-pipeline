import unittest
import requests
import mapping.map_tle_telemetry as mtt

from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta, timezone

# Confirmed golden fixture for ISS circa 2008 :D
# (Source: Wikipedia TLE example):
# https://tinyurl.com/ynvbk5sd

ISS_NAME = "ISS (ZARYA)"
ISS_LINE_ONE = "1 25544U 98067A   08264.51782528 -.00002182  00000-0 -11606-4 0  2927"  # noqa: E501
ISS_LINE_TWO = "2 25544  51.6416 247.4627 0006703 130.5360 325.0288 15.72125391563537"  # noqa: E501
ISS_TLE = (ISS_NAME, ISS_LINE_ONE, ISS_LINE_TWO)
ISS_TEXT = f"{ISS_NAME}\n{ISS_LINE_ONE}\n{ISS_LINE_TWO}\n"


# Expected telemetry output from golden test fixture:
EXPECTED_TELEMETRY = mtt.SatTelemetry(
    sat_name="ISS (ZARYA)",
    norad_id=25544,
    intl_designator="98067A",
    utc_epoch=datetime(2008, 9, 20, 12, 25, 40, 104187, tzinfo=timezone.utc),
    tle_age_hours=0.0,
    inclination_deg=51.6416,
    latitude_deg=51.46364018,
    longitude_deg=160.14523462,
    altitude_km=355.09572407,
    speed_inertial_kms=7.70461745,
    speed_earth_kms=7.40046560,
    speed_ground_kms=7.00976737,
)


# fr = Fake Response
def fr(text="", status=200):
    """Stand-in for a requests/response."""
    return MagicMock(status_code=status, text=text)


class TestGetTLE(unittest.TestCase):

    # Test if invalid IDS are rejected:
    def test_invalid_ids_are_rejected_without_a_request(self):
        for bad_id in ["ISS", "255 44", -5, 0, 25544.7, 99999999]:
            with self.subTest(bad_id=bad_id):
                with patch.object(mtt.requests, "get") as mock_get, self.assertLogs(mtt.logger, level="ERROR"):  # noqa: E501
                    self.assertIsNone(mtt.get_tle(bad_id))
                mock_get.assert_not_called()

    # Test if valid IDs are accepted:
    def test_valid_ids_request_the_right_url(self):
        for good_id in [25544, "25544", " 25544 "]:
            with self.subTest(good_id=good_id):
                with patch.object(mtt.requests, "get", return_value=fr(ISS_TEXT)) as mock_get:  # noqa: E501
                    self.assertEqual(mtt.get_tle(good_id), ISS_TLE)
                url = mock_get.call_args.args[0]
                self.assertIn("CATNR=25544&", url)

    # Test for when response != 200:
    def test_non_200_status_returns_none(self):
        with patch.object(mtt.requests, "get", return_value=fr("", status=503)), self.assertLogs(mtt.logger, level="ERROR"):  # noqa: E501
            self.assertIsNone(mtt.get_tle(25544))

    # Test for when there is no CelesTrak TLE returned:
    def test_no_data_message_returns_none(self):
        with patch.object(mtt.requests, "get", return_value=fr("No GP data found")), self.assertLogs(mtt.logger, level="ERROR"):  # noqa: E501
            self.assertIsNone(mtt.get_tle(99999))

    # Test for when the CelesTrak API is offline:
    def test_network_failure_returns_none(self):
        offline = requests.ConnectionError("offline")
        with patch.object(mtt.requests, "get", side_effect=offline), self.assertLogs(mtt.logger, level="ERROR"):  # noqa: E501
            self.assertIsNone(mtt.get_tle(25544))


class TestGoldenFixture(unittest.TestCase):

    def test_map_tle_telemetry_matches_fixture(self):
        fixture = mtt.map_tle_telemetry(ISS_TLE, when=EXPECTED_TELEMETRY.utc_epoch)  # noqa: E501

        # Test each expected telemetry point from golden fixture 
        # against map_tle_telemetry()'s named tuple output data:
        for telemetry, want in EXPECTED_TELEMETRY._asdict().items():

            got = getattr(fixture, telemetry)

            with self.subTest(telemetry=telemetry):

                if isinstance(want, float):
                    self.assertAlmostEqual(got, want, places=6)  # Round

                elif isinstance(want, datetime):
                    self.assertAlmostEqual(got, want, delta=timedelta(milliseconds=1))  # noqa: E501

                else:
                    self.assertEqual(got, want)


if __name__ == "__main__":
    unittest.main()
