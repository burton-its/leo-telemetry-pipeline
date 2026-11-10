"""Command-line entry point: python -m store.track [NORAD_ID]

Computes a ground track from the satellite's latest TLE (via the mapping module)
and stores it in data/leo.db, then refreshes the dashboard JSON in data/export/.
By default the track runs from 90 minutes ago to 90 minutes ahead, one point a
minute, which is about one orbit each way for a LEO satellite.

Needs the TLE mapping module (PR #6) and network access to CelesTrak, unless a
recent TLE is already cached in data/tle/.
"""

import logging
import sys
from datetime import datetime, timedelta, timezone

from store.db import DB_PATH, connect, insert_tle_positions
from store.export import export_all

ISS_NORAD_ID = 25544
BACK_MIN, AHEAD_MIN, STEP_MIN = 90, 90, 1


def build_track(tle, map_fn, now=None, back=BACK_MIN, ahead=AHEAD_MIN, step=STEP_MIN):
    """Return [(when, telemetry), ...] oldest first. Points SGP4 cannot compute are skipped.

    `map_fn(tle, when=...)` is map_tle_telemetry from the mapping module.
    """
    now = (now or datetime.now(timezone.utc)).replace(second=0, microsecond=0)
    track = []
    for minutes in range(-back, ahead + 1, step):
        when = now + timedelta(minutes=minutes)
        telemetry = map_fn(tle, when=when)
        if telemetry is not None:
            track.append((when, telemetry))
    return track


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    argv = sys.argv[1:] if argv is None else argv
    norad_id = int(argv[0]) if argv else ISS_NORAD_ID

    try:
        # Adjust this import to wherever PR #6's module ends up living.
        from mapping.map_tle_telemetry import _tle_cache, map_tle_telemetry
    except ImportError as err:
        print(f"Could not import the TLE mapping module ({err}). "
              "Is PR #6 merged, and are skyfield and numpy installed?", file=sys.stderr)
        return 1

    tle = _tle_cache(norad_id)
    if tle is None:
        print(f"No TLE for NORAD {norad_id}: CelesTrak was unreachable and nothing is cached.",
              file=sys.stderr)
        return 1

    track = build_track(tle, map_tle_telemetry)
    if not track:
        print("SGP4 could not compute any positions from this TLE.", file=sys.stderr)
        return 1

    conn = connect()
    written = insert_tle_positions(conn, track)
    print(f"Stored {written} track points for {tle[0]} "
          f"({track[0][0]:%Y-%m-%d %H:%M}Z to {track[-1][0]:%Y-%m-%d %H:%M}Z) in {DB_PATH}")
    for path in export_all(conn, norad_id):
        print(f"  wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())