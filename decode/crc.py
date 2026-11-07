"""CRC helpers for the Decode stage.

AX.25 uses CRC-16/X.25 (a.k.a. CRC-16/IBM-SDLC) as its frame check sequence (FCS):
init 0xFFFF, reflected polynomial 0x8408, final XOR 0xFFFF, sent low byte first.
Standard check value: crc16_x25(b"123456789") == 0x906E.

SatNOGS usually strips the FCS from AX.25 frames, so most frames from the ISS
carry no CRC to check. Satellites that keep a trailing FCS can opt in with
crc="x25_trailing" in their SatelliteSpec.
"""


def crc16_x25(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if crc & 1 else crc >> 1
    return crc ^ 0xFFFF


def append_fcs(body: bytes) -> bytes:
    """Return body with its 2-byte FCS appended (low byte first). Used by tests."""
    return body + crc16_x25(body).to_bytes(2, "little")


def split_fcs(frame: bytes) -> tuple[bytes, bool]:
    """Split a frame that ends in an FCS into (body, crc_ok)."""
    if len(frame) < 3:
        raise ValueError("frame too short to contain a body and an FCS")
    body, fcs = frame[:-2], frame[-2:]
    return body, crc16_x25(body) == int.from_bytes(fcs, "little")