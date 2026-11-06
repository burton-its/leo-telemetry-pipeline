"""Decoder for the ISS (NORAD 25544) APRS digipeater.

SatNOGS delivers these as AX.25 UI frames with the flags and FCS already
stripped. Layout:

    destination (7) | source (7) | 0-8 digipeaters (7 each) | control (1) | PID (1) | info

Each 7-byte address is 6 callsign characters shifted left one bit, then an SSID
byte: bit 7 = C/H flag, bits 4-1 = SSID, bit 0 = 1 on the last address.

The header is parsed by hand so the only hard requirement is the standard
library. If `aprslib` is installed, the payload is also run through it and the
result is stored under fields["aprs"] (it is None for non-APRS text).
"""

import json

from decode.models import DecodeError

try:
    import aprslib
except ImportError:  # optional
    aprslib = None

MAX_ADDRESSES = 10  # destination + source + up to 8 digipeaters
MIN_UI_FRAME = 16  # 2 addresses (14) + control (1) + PID (1)


def _decode_address(chunk: bytes) -> tuple[str, bool, bool]:
    """Return (callsign with SSID, h_bit, is_last)."""
    chars = [b >> 1 for b in chunk[:6]]
    if any(not (c == 0x20 or chr(c).isascii() and chr(c).isalnum()) for c in chars):
        raise DecodeError("address field is not a valid callsign")
    call = "".join(chr(c) for c in chars).strip()
    if not call:
        raise DecodeError("empty callsign")
    ssid = (chunk[6] >> 1) & 0x0F
    return (f"{call}-{ssid}" if ssid else call), bool(chunk[6] & 0x80), bool(chunk[6] & 0x01)


def parse_ax25_ui(data: bytes) -> dict:
    if len(data) < MIN_UI_FRAME:
        raise DecodeError(f"frame too short ({len(data)} bytes, need at least {MIN_UI_FRAME})")

    addresses = []
    offset = 0
    while True:
        if offset + 7 > len(data):
            raise DecodeError("address field runs past end of frame")
        addresses.append(_decode_address(data[offset:offset + 7]))
        offset += 7
        if addresses[-1][2]:
            break
        if len(addresses) >= MAX_ADDRESSES:
            raise DecodeError("too many addresses (no end-of-address marker)")

    if len(addresses) < 2:
        raise DecodeError("need at least a destination and a source address")
    if offset + 2 > len(data):
        raise DecodeError("frame ends before control/PID bytes")

    control, pid = data[offset], data[offset + 1]
    if control & 0xEF != 0x03:
        raise DecodeError(f"not a UI frame (control=0x{control:02x})")

    dest, src = addresses[0][0], addresses[1][0]
    path = [call + ("*" if h_bit else "") for call, h_bit, _ in addresses[2:]]  # * = already repeated
    info = data[offset + 2:]
    return {
        "src": src,
        "dest": dest,
        "path": path,
        "pid": pid,
        "info": info.decode("ascii", errors="replace"),
        "info_len": len(info),
    }


def _try_aprs(parsed: dict) -> dict | None:
    if aprslib is None:
        return None
    tnc2 = ",".join([f"{parsed['src']}>{parsed['dest']}"] + parsed["path"]) + ":" + parsed["info"]
    try:
        result = aprslib.parse(tnc2)
        return json.loads(json.dumps(result, default=str))  # make sure it is JSON-safe
    except Exception:
        return None


def decode_iss(payload: bytes) -> dict:
    """bytes -> parsed fields. Raises DecodeError for anything that is not an AX.25 UI frame."""
    parsed = parse_ax25_ui(payload)
    aprs = _try_aprs(parsed)
    parsed["aprs"] = aprs
    parsed["packet_type"] = (aprs or {}).get("format", "unparsed")
    return parsed