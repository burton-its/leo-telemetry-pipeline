"""Registry that maps a NORAD ID to the decoder for that satellite.

    RawFrame -> decode_frame() -> REGISTRY[norad_id] -> DecodedFrame

To add a satellite: write `def decode_xyz(payload: bytes) -> dict` that raises
DecodeError on bad input, then add a SatelliteSpec for it at the bottom.
"""

import logging
from dataclasses import dataclass
from typing import Callable

from decode.crc import split_fcs
from decode.iss import decode_iss
from decode.models import DecodeError, DecodedFrame
from ingest.models import RawFrame

logger = logging.getLogger(__name__)

CRC_NONE = "none"  # SatNOGS stripped the FCS (or the format has none)
CRC_X25_TRAILING = "x25_trailing"  # last 2 bytes are a CRC-16/X.25 FCS, low byte first


@dataclass(frozen=True)
class SatelliteSpec:
    norad_id: int
    name: str
    frame_format: str
    decoder: Callable[[bytes], dict]
    crc: str = CRC_NONE


REGISTRY: dict[int, SatelliteSpec] = {}


def register(spec: SatelliteSpec) -> None:
    if spec.norad_id in REGISTRY:
        raise ValueError(f"NORAD {spec.norad_id} is already registered")
    if spec.crc not in (CRC_NONE, CRC_X25_TRAILING):
        raise ValueError(f"unknown crc mode {spec.crc!r}")
    REGISTRY[spec.norad_id] = spec


def get_spec(norad_id: int) -> SatelliteSpec | None:
    return REGISTRY.get(norad_id)


def decode_frame(frame: RawFrame) -> DecodedFrame:
    """Decode one frame. Never raises: failures come back as ok=False with a reason."""
    spec = get_spec(frame.norad_id)
    if spec is None:
        return DecodedFrame.failure(frame, f"no decoder registered for NORAD {frame.norad_id}")

    payload, crc_ok = frame.raw_bytes, None
    if spec.crc == CRC_X25_TRAILING:
        try:
            payload, crc_ok = split_fcs(frame.raw_bytes)
        except ValueError as err:
            return DecodedFrame.failure(frame, str(err))
        if not crc_ok:
            return DecodedFrame.failure(frame, "CRC mismatch", crc_ok=False)

    try:
        fields = spec.decoder(payload)
    except DecodeError as err:
        return DecodedFrame.failure(frame, str(err), crc_ok=crc_ok)
    except Exception as err:  # a decoder bug must not kill the whole run
        logger.exception("decoder for NORAD %s crashed", frame.norad_id)
        return DecodedFrame.failure(frame, f"decoder crashed: {err!r}", crc_ok=crc_ok)
    return DecodedFrame.success(frame, fields, crc_ok=crc_ok)


# --- Registered satellites ---------------------------------------------------

register(SatelliteSpec(
    norad_id=25544,
    name="ISS (APRS digipeater)",
    frame_format="AX.25 UI / APRS",
    decoder=decode_iss,
    crc=CRC_NONE,
))