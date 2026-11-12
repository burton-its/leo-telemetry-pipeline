import numpy as np
import logging
import math

from datetime import datetime, timedelta, timezone
from skyfield.api import EarthSatellite
from track.map_tle_telemetry import TIMESCALE, _tle_cache, map_tle_telemetry as mtt  # noqa 5301

logger = logging.getLogger(__name__)


# Constants for CZML generation (seconds).
past = 3 * 3600  # How far back to estimate tracking telemetry (3 hours).
future = 6 * 3600  # How far forward ^ ... (6 hours).
step = 60  # How often to sample (1 min).


def _iso(dt):
    """
    Helper used to convert a datetime object to an ISO 8601 UTC string.
    """
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def generate_czml(norad_ids):
    """
    Generates CZML for given NORAD ID(s).
    """

    # Current time for the CZML clock.
    present = datetime.now(timezone.utc).replace(microsecond=0)

    # Window of time for which to generate telemetry (UTC).
    start = present - timedelta(seconds=past)
    stop = present + timedelta(seconds=future)

    # CZML interval string for the clock.
    interval = f"{_iso(start)}/{_iso(stop)}"

    # Skyfield time objects for the telemetry window.
    # Reference: https://rhodesmill.org/skyfield/time.html
    offsets = np.arange(0, past + future + 1, step)
    t = TIMESCALE.utc(start.year, start.month, start.day, start.hour,
                      start.minute, start.second + offsets)

    # References:
    # https://github.com/AnalyticalGraphicsInc/czml-writer/wiki/Packet
    # https://github.com/AnalyticalGraphicsInc/czml-writer/wiki/Clock
    # https://github.com/AnalyticalGraphicsInc/czml-writer/wiki/ClockRange

    packets = [{
        "id": "document",
        "name": "LEO Telemetry",
        "version": "1.0",  # Don't remove -> breaks CesiumJS LOL
        "clock": {
            "interval": interval,
            "currentTime": _iso(present),
            "multiplier": 1,
            "range": "UNBOUNDED",
            "step": "SYSTEM_CLOCK"
        }
    }]

    # Get TLEs and generate CZML packets for each satellite.
    for norad_id in norad_ids:
        tle = _tle_cache(norad_id)
        if tle is None:
            continue

        # Create a Skyfield EarthSatellite object from obtained TLE.
        name, line1, line2 = tle
        sat = EarthSatellite(line1, line2, name, TIMESCALE)

        # Run SGP4 to find the satellite's position at
        # each time step in the telemetry window.
        xyz = sat.at(t).position.m
        if np.isnan(xyz).any():
            logger.error("SGP4 failed within window for NORAD ID %s", norad_id)
            continue

        # Mean motion: compute time for one full orbit in seconds.
        # No kozai: mean motion in radians per minute.
        # https://en.wikipedia.org/wiki/Mean_motion
        orbital_period = (2 * math.pi / sat.model.no_kozai) * 60

        # Convert pos (xyz) array to CZML format by flattening into a 1D array.
        cartesian = np.column_stack((offsets, xyz.T)).ravel().tolist()

        # Build LEO w/ orbital path:

        # References:
        # https://github.com/AnalyticalGraphicsInc/czml-writer/wiki/Packet
        # https://github.com/AnalyticalGraphicsInc/czml-writer/wiki/Point
        # https://github.com/AnalyticalGraphicsInc/czml-writer/wiki/Label
        # https://github.com/AnalyticalGraphicsInc/czml-writer/wiki/Path
        # https://github.com/AnalyticalGraphicsInc/czml-writer/wiki/PolylineMaterial

        packets.append({
            "id": f"sat-{norad_id}",
            "name": name,
            "availability": interval,
            "description": (  # Description blurb for satellite.
                f"<b>NORAD:</b> {norad_id}<br>"
                f"<b>Int'l designator:</b> {line1[9:17].strip()}<br>"
                f"<b>Inclination:</b> {math.degrees(sat.model.inclo):.2f}°<br>"
                f"<b>TLE epoch:</b> {sat.epoch.utc_iso()} <br>"
            ),
            "point": {
                "pixelSize": 10,
                "color": {"rgba": [255, 255, 0, 255]},  # Yellow
                "outlineColor": {"rgba": [0, 0, 0, 255]},  # Black
                "outlineWidth": 2,
            },
            "label": {
                "text": name,
                "font": "22px Rajdhani, Bahnschrift, sans-serif",
                "style": "FILL_AND_OUTLINE",
                "outlineWidth": 4,
                "verticalOrigin": "BOTTOM",
                "pixelOffset": {"cartesian2": [0, -14]},  # Put label above pt.
            },
            "path": {
                "leadTime": orbital_period / 2,  # How far ahead to show path.
                "trailTime": orbital_period / 2,  # How far back ^
                "resolution": 60,
                "width": 3,
                "material": {
                    "polylineGlow": {
                        "color": {"rgba": [0, 255, 255, 255]},
                        "glowPower": 0.2,
                    },
                },
            },
            "position": {
                "epoch": _iso(start),
                "referenceFrame": "INERTIAL",
                "interpolationAlgorithm": "LAGRANGE",
                "interpolationDegree": 5,
                "cartesian": cartesian,
            },
        })

    return packets
