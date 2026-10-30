""" Data contract for the Ingestion stage.
A RawFrame is what the Ingestion hands to the Decode stage:
one undecoded telemetry frame plus the metadata we need to trace where it came from.
"""

import hashlib
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class RawFrame:
    """One raw telemetry frame as received from SatNOGS DB."""

    norad_id: int
    timestamp: datetime
    observer: str
    station_id: int | None
    raw_bytes: bytes

    @classmethod
    def from_api(cls, entry: dict) -> "RawFrame":
        """Build a RawFrame frome one item in the SatNOGS /telemetry/ response.
        Raises ValueError or KeyError if the entry is missing required fields or has a frame that is not valid hex.
        """
        frame_hex = entry.get("frame") or ""
        if not frame_hex:
            raise ValueError("entry has no frame data")

        # SatNOGS sends times like "2026-10-28T19:04:11Z". Python's
        # fromisoformat() wants "+00:00" instead of "Z" on older versions.
        timestamp = datetime.fromisoformat(entry["timestamp"].replace("Z", "+00:00"))

        return cls(
            norad_id=int(entry["norad_cat_id"]),
            timestamp=timestamp,
            observer=entry.get("observer") or "unknown",
            station_id=entry.get("station_id"),
            raw_bytes=bytes.fromhex(frame_hex),
        )

    @property
    def dedup_key(self) -> str:
        """ Key that is identical when two ground stations hear the same frame.

        We hash the frame byttes with sha256 (not Python's buil-in hash(), which changes every time the program restarts).
        """
        digest = hashlib.sha256(self.raw_bytes).hexdigest()
        return f"{self.norad_id}:{digest}"

    def to_dict(self) -> dict:
        """JSON-friendly version, used when saving frames to disk."""
        return {
            "norad_id": self.norad_id,
            "timestamp": self.timestamp.isoformat(),
            "observer": self.observer,
            "station_id": self.station_id,
            "raw_bytes": self.raw_bytes.hex(),
        }
