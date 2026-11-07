"""Data contract for the Decode stage.

A DecodedFrame is one row's worth of output for the storage stage. Every input
frame produces exactly one DecodedFrame, so counts always reconcile:
ok=True means the frame parsed, ok=False means `error` says why not.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ingest.models import RawFrame


class DecodeError(Exception):
    """Raised by a satellite decoder when a frame is not valid for that satellite."""


@dataclass
class DecodedFrame:
    norad_id: int
    timestamp: datetime
    observer: str
    station_id: int | None
    raw_hex: str
    ok: bool
    crc_ok: bool | None = None  # None = this satellite has no CRC to check
    error: str | None = None
    fields: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def success(cls, frame: RawFrame, fields: dict[str, Any], crc_ok: bool | None = None) -> "DecodedFrame":
        return cls(frame.norad_id, frame.timestamp, frame.observer, frame.station_id,
                   frame.raw_bytes.hex(), True, crc_ok, None, fields)

    @classmethod
    def failure(cls, frame: RawFrame, error: str, crc_ok: bool | None = None) -> "DecodedFrame":
        return cls(frame.norad_id, frame.timestamp, frame.observer, frame.station_id,
                   frame.raw_bytes.hex(), False, crc_ok, error, {})

    def to_dict(self) -> dict:
        return {
            "norad_id": self.norad_id,
            "timestamp": self.timestamp.isoformat(),
            "observer": self.observer,
            "station_id": self.station_id,
            "raw_hex": self.raw_hex,
            "ok": self.ok,
            "crc_ok": self.crc_ok,
            "error": self.error,
            "fields": self.fields,
        }