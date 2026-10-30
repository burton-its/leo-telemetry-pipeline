import numpy as np
import requests
import math

from skyfield.api import EarthSatellite, load, wgs84
from skyfield.framelib import itrs


# tle = Two-line element set (used to estimate lat, lon, velocity):
# https://en.wikipedia.org/wiki/Two-line_element_set
TLE_URL = "https://celestrak.org/NORAD/elements/gp.php?CATNR={norad_id}&FORMAT=tle"  # noqa: E501
HEADERS = {"User-Agent": "LEO-Telemetry-Project"}

# Timescale & radius for position and velocity calculations:
TIMESCALE = load.timescale()
EARTH_RADIUS_KM = 6371.0


def get_tle(norad_id=25544):
    """
    Acquires the most recent TLE report for a given satellite
    (defaults ISS: NORAD ID 25544) using the CelesTrak DB as
    raw strings for Simplified General Perturbations 4 model
    to map object telemetry.

    Returns tuple -> Satellite name,
                     line 1 raw telemetry,
                     line 2 raw telemetry.
    """

    # Check if NORAD ID is valid:
    norad_id = str(norad_id).strip()
    if not norad_id.isdecimal() or not 0 < int(norad_id) <= 339999:
        print(f"Invalid NORAD ID: {norad_id}")
        return None

    # Get raw TLE byte report from Celestrak DB:
    try:

        tle = requests.get(TLE_URL.format(norad_id=norad_id),
                           headers=HEADERS,
                           timeout=10)

    except requests.RequestException as err:
        print(f"Network Error: {err}")
        return None

    # If API call OK:
    if tle.status_code == 200:

        # Split TLE report into lines:
        tle = [line.strip() for line in tle.text.strip().splitlines()]

        # Check if NORAD ID returns a valid TLE.
        if len(tle) < 3 or not tle[1].startswith("1 ") or not tle[2].startswith("2 "):  # noqa: E501
            print(f"No valid TLE ({tle}) returned for NORAD ID ({norad_id})")
            return None

        sat_name = tle[0]
        line_one = tle[1]
        line_two = tle[2]

        return sat_name, line_one, line_two

    else:

        # Report error:
        print(f"Network Error: {tle.status_code}")
        return None


def map_telemetry(tle, when=None):
    """
    Takes TLE report and reads identifying fields.
    Calculates and packages telemetry for dashboard projection.

    Returns tuple -> Satellite name, NORAD ID, int'l designator,
                     UTC epoch, inclination (deg), position (lat/lon),
                     altitude (km), inertial speed (km/s),
                     earth-relative speed (km/s), and ground speed (km/s).
    """

    # Error check:
    if tle is None:
        return None

    # Unpack TLE tuple & make sat obj:
    sat_name, line_one, line_two = tle
    sat = EarthSatellite(line_one, line_two, sat_name, TIMESCALE)

    # SATELLITE NAME / NORAD ID / INTERNATIONAL DESIGNATOR:
    sat_name = sat_name
    norad_id = int(line_two[2:7])
    int_desig = line_one[9:17].strip()

    # EPOCH TIME (UTC)
    utc_epoch = sat.epoch.utc_datetime()

    # INCLINATION (deg)
    inclination = math.degrees(sat.model.inclo)

    # POSITION (lat, lon, alt) & VELOCITY (km/s)
    # Calculate position at given time:
    time = TIMESCALE.from_datetime(when) if when else TIMESCALE.now()
    pos = sat.at(time)

    # Check for null value from SGP4:
    if np.isnan(pos.position.km).any():
        print(f"SGP4 cannot estimate position for this TLE: {pos.message}")
        return None

    # Get latitude, longitude, and altitude:
    # wgs84 = World Geodetic System 1984
    lat, lon = wgs84.latlon_of(pos)
    alt = wgs84.height_of(pos).km

    # Calculate relative speed, earth speed, and ground speed:
    speed_inertial = np.linalg.norm(pos.velocity.km_per_s)

    # itrs = International Terrestrail Reference System
    _, velocity_earth = pos.frame_xyz_and_velocity(itrs)  # _ = pos

    speed_earth = np.linalg.norm(velocity_earth.km_per_s)
    speed_ground = speed_earth * EARTH_RADIUS_KM / (EARTH_RADIUS_KM + alt)

    telemetry = (
        sat_name,
        norad_id,
        int_desig,
        utc_epoch,
        inclination,
        lat.degrees,
        lon.degrees,
        alt,
        speed_inertial,
        speed_earth,
        speed_ground
    )

    return telemetry


if __name__ == "__main__":
    tle = get_tle()
    print(map_telemetry(tle))
