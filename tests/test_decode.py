"""Unit tests for the Decode stage. Uses real frames from a live ISS run (2026-11-04)."""

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from decode.__main__ import load_raw_frames, raw_frame_from_dict, run
from decode.crc import append_fcs, crc16_x25, split_fcs
from decode.iss import parse_ax25_ui
from decode.models import DecodeError
from decode.registry import (CRC_X25_TRAILING, REGISTRY, SatelliteSpec, decode_frame,
                             get_spec, register)
from ingest.models import RawFrame

# Real frames captured from SatNOGS DB for NORAD 25544.
REAL_APRS_HEX = "829898404040e092ac6688b0ae64a4a66092a6a66103f04956335a5846205220353939"
REAL_JUNK_HEX = "ffff"


def make_frame(hex_bytes=REAL_APRS_HEX, norad_id=25544):
    return RawFrame(
        norad_id=norad_id,
        timestamp=datetime(2026, 11, 4, 19, 57, 2, tzinfo=timezone.utc),
        observer="IV3DXW-JN65qq",
        station_id=None,
        raw_bytes=bytes.fromhex(hex_bytes),
    )


class TestCrc(unittest.TestCase):
    def test_standard_check_value(self):
        self.assertEqual(crc16_x25(b"123456789"), 0x906E)

    def test_round_trip(self):
        body, ok = split_fcs(append_fcs(b"hello"))
        self.assertEqual(body, b"hello")
        self.assertTrue(ok)

    def test_detects_corruption(self):
        bad = bytearray(append_fcs(b"hello"))
        bad[0] ^= 0x01
        self.assertFalse(split_fcs(bytes(bad))[1])


class TestAx25(unittest.TestCase):
    def test_parses_real_iss_frame(self):
        parsed = parse_ax25_ui(bytes.fromhex(REAL_APRS_HEX))
        self.assertEqual(parsed["src"], "IV3DXW-2")
        self.assertEqual(parsed["dest"], "ALL")
        self.assertEqual(parsed["path"], ["RS0ISS"])
        self.assertEqual(parsed["pid"], 0xF0)
        self.assertEqual(parsed["info"], "IV3ZXF R 599")

    def test_rejects_junk_as_too_short(self):
        with self.assertRaisesRegex(DecodeError, "too short"):
            parse_ax25_ui(bytes.fromhex(REAL_JUNK_HEX))

    def test_rejects_non_ui_control_byte(self):
        data = bytearray.fromhex(REAL_APRS_HEX)
        data[21] = 0x00  # control byte after dest + src + 1 digi (3 x 7 = 21)
        with self.assertRaisesRegex(DecodeError, "not a UI frame"):
            parse_ax25_ui(bytes(data))

    def test_rejects_garbage_callsign(self):
        with self.assertRaises(DecodeError):
            parse_ax25_ui(bytes([0xFF] * 20))

    def test_rejects_missing_end_of_address_marker(self):
        # Every SSID byte has bit 0 clear, so the address field never ends.
        with self.assertRaises(DecodeError):
            parse_ax25_ui(bytes.fromhex("829898404040e0") * 12)


class TestRegistry(unittest.TestCase):
    def test_iss_is_registered(self):
        self.assertEqual(get_spec(25544).name, "ISS (APRS digipeater)")

    def test_decodes_real_frame(self):
        result = decode_frame(make_frame())
        self.assertTrue(result.ok)
        self.assertIsNone(result.crc_ok)
        self.assertEqual(result.fields["src"], "IV3DXW-2")
        self.assertEqual(result.raw_hex, REAL_APRS_HEX)

    def test_junk_frame_is_rejected_not_raised(self):
        result = decode_frame(make_frame(REAL_JUNK_HEX))
        self.assertFalse(result.ok)
        self.assertIn("too short", result.error)

    def test_unknown_satellite(self):
        result = decode_frame(make_frame(norad_id=99999))
        self.assertFalse(result.ok)
        self.assertIn("no decoder registered", result.error)

    def test_duplicate_registration_rejected(self):
        with self.assertRaises(ValueError):
            register(SatelliteSpec(25544, "dup", "x", lambda b: {}))

    def test_crc_mode_good_and_bad(self):
        spec = SatelliteSpec(90001, "test-sat", "test", lambda b: {"len": len(b)}, crc=CRC_X25_TRAILING)
        register(spec)
        try:
            good = decode_frame(make_frame(append_fcs(b"abcdef").hex(), norad_id=90001))
            self.assertTrue(good.ok)
            self.assertTrue(good.crc_ok)
            self.assertEqual(good.fields["len"], 6)

            corrupted = bytearray(append_fcs(b"abcdef"))
            corrupted[1] ^= 0xFF
            bad = decode_frame(make_frame(corrupted.hex(), norad_id=90001))
            self.assertFalse(bad.ok)
            self.assertIs(bad.crc_ok, False)
            self.assertEqual(bad.error, "CRC mismatch")
        finally:
            del REGISTRY[90001]

    def test_decoder_crash_is_contained(self):
        def boom(_):
            raise RuntimeError("bug")
        register(SatelliteSpec(90002, "buggy", "test", boom))
        try:
            with self.assertLogs("decode.registry", level="ERROR"):
                result = decode_frame(make_frame(norad_id=90002))
            self.assertFalse(result.ok)
            self.assertIn("decoder crashed", result.error)
        finally:
            del REGISTRY[90002]


class TestPipelineFiles(unittest.TestCase):
    def test_ingest_output_round_trips_into_decode(self):
        frame = make_frame()
        self.assertEqual(raw_frame_from_dict(frame.to_dict()), frame)

    def test_run_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw_dir, out_dir = Path(tmp) / "raw", Path(tmp) / "decoded"
            raw_dir.mkdir()
            lines = [json.dumps(make_frame().to_dict()),
                     json.dumps(make_frame(REAL_JUNK_HEX).to_dict()),
                     "not json at all",
                     ""]
            (raw_dir / "25544.jsonl").write_text("\n".join(lines) + "\n")

            with self.assertLogs("decode.__main__", level="WARNING"):
                summary = run(25544, raw_dir, out_dir)

            self.assertEqual(summary["total"], 2)
            self.assertEqual(summary["ok"], 1)
            self.assertEqual(summary["unreadable_lines"], 1)
            out = [json.loads(line) for line in (out_dir / "25544.jsonl").read_text().splitlines()]
            self.assertEqual([row["ok"] for row in out], [True, False])
            self.assertEqual(out[0]["fields"]["src"], "IV3DXW-2")
            self.assertIn("too short", out[1]["error"])


if __name__ == "__main__":
    unittest.main()