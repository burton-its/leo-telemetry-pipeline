"""
Command-line interface for mapping TLEs to telemetry data.

Entry: python -m track [NORAD ID]

Stores current TLE-based telemetry for a given
satellite (default ISS: NORAD ID 25544).
"""

import logging
import sys

from track.map_tle_telemetry import _tle_cache, map_tle_telemetry


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")  # noqa: E501

    argv = sys.argv[1:] if argv is None else argv
    norad_id = int(argv[0]) if argv else 25544

    telemetry = map_tle_telemetry(_tle_cache(norad_id))

    if telemetry is None:
        print(f"Failed to map TLE to telemetry for NORAD {norad_id}", file=sys.stderr)  # noqa: E501
        return 1

    for name, value in telemetry._asdict().items():
        print(f"{name:<20} {value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
