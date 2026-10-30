from unittest.mock import Mock, patch

import pytest
import requests

import map_sat_frames as msf


# Confirmed golden fixture for ISS circa 2008 :D
# (Source: Wikipedia TLE example):
# https://tinyurl.com/ynvbk5sd

iss_name = "ISS (ZARYA)"
iss_line_one = "1 25544U 98067A   08264.51782528 -.00002182  00000-0 -11606-4 0  2927"  # noqa: E501
iss_line_two = "2 25544  51.6416 247.4627 0006703 130.5360 325.0288 15.72125391563537"  # noqa: E501
iss_tle = (iss_name, iss_line_one, iss_line_two)


# fr = Fake Response
def fr(text="", status=200):
    """Stand-in for a requests/response."""
    return Mock(status_code=status, text=text)


iss_text = f"{iss_name}\n{iss_line_one}\n{iss_line_two}\n"


# Test if invalid IDs are rejected:
@pytest.mark.parametrize("bad_id", ["ISS", "255 44", -5, 0, 25544.7, 99999999])
def test_invalid_ids_are_rejected_without_a_request(bad_id):
    with patch.object(msf.requests, "get") as mock_get:
        assert msf.get_tle(bad_id) is None
        mock_get.assert_not_called()


# Test if valid IDs are accepted:
@pytest.mark.parametrize("good_id", [25544, "25544", " 25544 "])
def test_valid_ids_request_the_right_url(good_id):
    response = fr(iss_text)
    with patch.object(msf.requests, "get", return_value=response) as mock_get:
        assert msf.get_tle(good_id) == iss_tle
        url = mock_get.call_args.args[0]
        assert "CATNR=25544&" in url


# Test for when response != 200:
def test_non_200_status_returns_none():
    with patch.object(msf.requests, "get", return_value=fr("", status=503)):
        assert msf.get_tle(25544) is None


# Test for when there is no CelesTrak TLE returned:
def test_no_data_message_returns_none():
    with patch.object(msf.requests, "get", return_value=fr("No GP data found")):  # noqa: E501
        assert msf.get_tle(99999) is None


# Test for when a CelesTrak API offline:
def test_network_failure_returns_none():
    offline = requests.ConnectionError("offline")
    with patch.object(msf.requests, "get", side_effect=offline):
        assert msf.get_tle(25544) is None
