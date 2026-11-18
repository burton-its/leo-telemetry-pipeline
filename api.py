import logging

from fastapi import FastAPI, Query
from track.map_tle_telemetry import _tle_cache, map_tle_telemetry
from track.czml import generate_czml


logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(title="LEO Telemetry API")


@app.get("/api/get-tle-telemetry")
def get_tle_telemetry(ids: list[int] = Query(default=[25544])):
    """
    Get raw TLE data + current telemetry for a list of NORAD IDs.
    """

    results = []
    for norad_id in ids:
        telemetry = map_tle_telemetry(_tle_cache(norad_id))
        if telemetry is not None:
            results.append(telemetry._asdict())
    return results


@app.get("/api/get-czml")
def get_czml(ids: list[int] = Query(default=[25544])):
    """
    Get CZML for a list of NORAD IDs.
    """

    return generate_czml(ids)
