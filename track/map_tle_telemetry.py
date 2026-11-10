import numpy as np
import requests
import logging
import math
import time

from skyfield.api import EarthSatellite, load, wgs84
from skyfield.framelib import itrs
from typing import NamedTuple
from datetime import datetime
from pathlib import Path


logger = logging.getLogger(__name__)

# tle = Two-line element set (used to estimate lat, lon, velocity):
# https://en.wikipedia.org/wiki/Two-line_element_set
TLE_URL = "https://celestrak.org/NORAD/elements/gp.php?CATNR={norad_id}&FORMAT=tle"  # noqa: E501
HEADERS = {"User-Agent": "LEO-Telemetry-Project"}

# Cache handling:
TLE_CACHE = Path("data/tle")
TLE_REFRESH_LIMIT = 7200  # ~2 hours in seconds.

# Timescale & radius for position and velocity calculations:
TIMESCALE = load.timescale()
EARTH_RADIUS_KM = 6371.0


def _tle_cache(norad_id=25544):
    """
    Helper that uses cached copy of TLE to minimize API blocking
    if a TLE was fetched less than (<) TLE_REFRESH_LIMIT secs ago.
    """

    path = TLE_CACHE / f"{str(norad_id).strip()}.tle"

    # Check for existing TLE + time to refresh limit:
    if path.exists() and time.time() - path.stat().st_mtime < TLE_REFRESH_LIMIT:  # noqa: E501
        logger.debug("Using recently cached TLE for %s", norad_id)
        return tuple(path.read_text().splitlines())

    # Refresh TLE:
    tle = get_tle(norad_id)

    # Fallback if no new TLE received but a cached TLE is present:
    # (e.g. due to network err from CelesTrak)
    if tle is None:
        if path.exists():
            logger.warning("Failed request: using stale (>%g hour(s) old) TLE for %s.",  # noqa: E501
                           (TLE_REFRESH_LIMIT/60)/60, norad_id)
            return tuple(path.read_text().splitlines())
        return None

    # Create directory and store refreshed TLE data:
    TLE_CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(tle))

    logger.info("Fetched new TLE for %s from CelesTrak API.", norad_id)

    return tle


def get_tle(norad_id=25544):
    """
    Acquires the most recent TLE report for a given satellite
    (defaults ISS: NORAD ID 25544) using the CelesTrak DB as
    raw strings for Simplified General Perturbations 4 model
    to convert to object telemetry.

    Returns tuple -> Satellite name,
                     line 1 raw telemetry,
                     line 2 raw telemetry.
    """

    # Check if NORAD ID is valid:
    norad_id = str(norad_id).strip()
    if not norad_id.isdecimal() or not 0 < int(norad_id) <= 339999:  # Upper bound of Alpha-5 NORAD ID catalogue.  # noqa: E501
        logger.error("Invalid NORAD ID: %s", norad_id)
        return None

    # Get raw TLE byte report from Celestrak DB:
    try:

        tle = requests.get(TLE_URL.format(norad_id=norad_id),
                           headers=HEADERS,
                           timeout=10)

    except requests.RequestException as err:
        logger.error("Network error fetching TLE for %s: %s", norad_id, err)
        return None

    # If API call OK:
    if tle.status_code == 200:

        # Split TLE report into lines:
        tle = [line.strip() for line in tle.text.strip().splitlines()]

        # Check if NORAD ID returns a valid TLE.
        if len(tle) < 3 or not tle[1].startswith("1 ") or not tle[2].startswith("2 "):  # noqa: E501
            logger.error("No valid TLE for NORAD ID %s: %r", norad_id, tle)
            return None

        sat_name = tle[0]
        line_one = tle[1]
        line_two = tle[2]

        return sat_name, line_one, line_two

    else:

        # Report error:
        logger.error("CelesTrak returned HTTP %s for NORAD ID %s",
                     tle.status_code, norad_id)
        return None


# TLE-based Satellite Telemetry:
class SatTelemetry(NamedTuple):
    sat_name: str
    norad_id: int
    intl_designator: str
    utc_epoch: datetime
    tle_age_hours: float
    inclination_deg: float
    latitude_deg: float
    longitude_deg: float
    altitude_km: float
    speed_inertial_kms: float
    speed_earth_kms: float
    speed_ground_kms: float


def map_tle_telemetry(tle, when=None):
    """
    Takes TLE report and reads identifying fields. Calculates
    and packages telemetry for dashboard modules + 3D tracking map.

    Returns SatTelemetry (NamedTuple) containing:
        Satellite name, NORAD ID, int'l designator,
        UTC epoch, inclination (deg), position (lat/lon),
        altitude (km), inertial speed (km/s),
        earth-relative speed (km/s), and ground speed (km/s).
    """

    # Absent TLE:
    if tle is None:
        return None

    # Unpack TLE tuple & make sat obj:
    sat_name, line_one, line_two = tle
    sat = EarthSatellite(line_one, line_two, sat_name, TIMESCALE)

    # SATELLITE NAME / NORAD ID / INTERNATIONAL DESIGNATOR:
    norad_id = int(line_two[2:7])
    int_desig = line_one[9:17].strip()

    # EPOCH TIME (UTC) & TLE AGE
    t = TIMESCALE.from_datetime(when) if when else TIMESCALE.now()
    utc_epoch = sat.epoch.utc_datetime()
    tle_age_hours = (t - sat.epoch) * 24

    # INCLINATION (deg)
    inclination = math.degrees(sat.model.inclo)

    # POSITION (lat, lon, alt) & VELOCITY (km/s)
    # Calculate position at given time:
    pos = sat.at(t)

    # Check for null value from SGP4:
    if np.isnan(pos.position.km).any():
        logger.error("SGP4 cannot estimate position for NORAD %s: %s",
                     norad_id, pos.message)
        return None

    # Get latitude, longitude, and altitude:
    # wgs84 = World Geodetic System 1984
    lat, lon = wgs84.latlon_of(pos)
    alt = wgs84.height_of(pos).km

    # Calculate relative speed, earth speed, and ground speed:
    speed_inertial = np.linalg.norm(pos.velocity.km_per_s)

    # itrs = International Terrestrial Reference System
    _, velocity_earth = pos.frame_xyz_and_velocity(itrs)  # _ = pos

    speed_earth = np.linalg.norm(velocity_earth.km_per_s)
    speed_ground = speed_earth * EARTH_RADIUS_KM / (EARTH_RADIUS_KM + alt)

    return SatTelemetry(
        sat_name=sat_name,
        norad_id=norad_id,
        intl_designator=int_desig,
        utc_epoch=utc_epoch,
        tle_age_hours=float(tle_age_hours),
        inclination_deg=inclination,
        latitude_deg=float(lat.degrees),
        longitude_deg=float(lon.degrees),
        altitude_km=float(alt),
        speed_inertial_kms=float(speed_inertial),
        speed_earth_kms=float(speed_earth),
        speed_ground_kms=float(speed_ground),
    )


if __name__ == "__main__":

    logging.basicConfig(level=logging.DEBUG,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")  # noqa: E501

    # NOTE: Add NORAD ID inside of '()'
    # in order to test other LEOs:
    tle = _tle_cache(7530)

    # Prints formatted telemetry output:
    for telemetry, value in map_tle_telemetry(tle)._asdict().items():
        print(f"{telemetry:<20} {value}")

    # Individual telemetry (example):
    # print(map_tle_telemetry(tle).sat_name)
