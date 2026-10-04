"""Parser parity with verify/cn30.py, and a TCP probe that must not transmit."""

from __future__ import annotations

import asyncio
import importlib
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMP = ROOT / "custom_components" / "midea_cn30_hws"

# Register the package without running __init__.py (that imports Home Assistant).
if "custom_components" not in sys.modules:
    parent = types.ModuleType("custom_components")
    parent.__path__ = [str(ROOT / "custom_components")]
    sys.modules["custom_components"] = parent
pkg = types.ModuleType("custom_components.midea_cn30_hws")
pkg.__path__ = [str(COMP)]
pkg.__package__ = "custom_components.midea_cn30_hws"
sys.modules["custom_components.midea_cn30_hws"] = pkg

protocol = importlib.import_module("custom_components.midea_cn30_hws.protocol")
gateway = importlib.import_module("custom_components.midea_cn30_hws.gateway")

sys.path.insert(0, str(ROOT))
from verify import cn30 as live  # noqa: E402

# Frames captured on this HP170. Checksums were valid on the wire.
OFF = bytes.fromhex(
    "FE AA 00 00 FF 04 04 FF FF 50 00 00 00 00 00 53 94 00 00 46 3C 00 1A 94 4F 02 00 00 00 40 FE 05 55"
)
OFF_ELEMENT = bytes.fromhex(
    "FE AA 00 00 FF 04 04 FF FF 50 00 20 00 00 00 50 9C 09 00 46 3C C0 19 9C 4D 01 00 00 00 40 FE 13 55"
)
HYBRID = bytes.fromhex(
    "fe aa 00 00 ff 02 02 ff ff 50 00 00 00 00 00 4d 93 00 00 46 3c 00 18 93 4d 02 00 00 00 40 fe 15 55"
)
FRAMES = (OFF, OFF_ELEMENT, HYBRID)


class ParserParityTest(unittest.TestCase):
    def test_matches_live_decoder(self) -> None:
        for raw in FRAMES:
            got = protocol.parse_frame(raw)
            ref = live.parse_frame(raw)
            self.assertTrue(ref.checksum_ok, raw.hex())
            self.assertEqual(got.checksum_ok, ref.checksum_ok)
            self.assertEqual(got.mode, ref.mode)
            self.assertEqual(got.mode_raw, ref.mode_raw)
            self.assertEqual(got.power, ref.power)
            self.assertEqual(got.timer, ref.timer)
            self.assertEqual(got.target_c, ref.target_c)
            self.assertEqual(got.t5c_c, ref.t5c_c)
            self.assertEqual(got.t3_c, ref.t3_c)
            self.assertEqual(got.t4_c, ref.t4_c)
            self.assertEqual(got.th_c, ref.th_c)
            self.assertEqual(got.tp_c, ref.tp_c)
            self.assertEqual(got.eev, ref.eev)
            self.assertEqual(got.mode_max_c, ref.mode_max_c)
            self.assertEqual(got.mode_min_c, ref.mode_min_c)
            self.assertEqual(got.eheater_flag, ref.eheater_flag)

    def test_off_element_fields(self) -> None:
        frame = protocol.parse_frame(OFF_ELEMENT)
        self.assertEqual(frame.mode, "off")
        self.assertEqual(frame.target_c, 64)
        self.assertEqual(frame.t5c_c, 63.0)
        self.assertEqual(frame.t3_c, 25.0)
        self.assertEqual(frame.t4_c, 23.5)
        self.assertIsNone(frame.th_c)
        self.assertEqual(frame.tp_c, 25.0)
        self.assertEqual(frame.eev, 60)
        self.assertEqual(frame.mode_max_c, 70)
        self.assertEqual(frame.mode_min_c, 60)
        self.assertEqual(frame.eheater_flag, 0xC0)
        self.assertTrue(frame.lock_bit)
        self.assertFalse(frame.timer)
        self.assertFalse(frame.power)

    def test_stored_eheater_with_timer_is_power_off(self) -> None:
        # Live 2026-10-02: glass was Power OFF and the LCD showed 58 °C.
        raw = bytes.fromhex(
            "FE AA 00 00 FF 1C 18 FF FF 50 00 00 00 00 00 4B 92 00 00 46 3C 00 19 92 4B 02 00 00 00 40 FE EA 55"
        )
        frame = protocol.parse_frame(raw)
        self.assertTrue(frame.checksum_ok)
        self.assertEqual(frame.mode, "electric")
        self.assertTrue(frame.timer)
        self.assertFalse(frame.power)
        self.assertEqual(frame.t5c_c, 58.0)
        self.assertEqual(frame.target_c, 64)
        self.assertEqual(frame.tp_c, 25.0)
        self.assertEqual(frame.eev, 60)

    def test_panel_query_2026_10_04(self) -> None:
        # Live frame around 09:22: panel T5L 29, T4 26, T3 23, Th 23, Tp 25, EEV 60.
        raw = bytes.fromhex(
            "fe aa 00 00 ff 1c 18 ff ff 50 00 00 00 00 00 4d 59 00 00 46 3c 00 19 59 54 04 00 00 00 41 fe 4e 55"
        )
        frame = protocol.parse_frame(raw)
        self.assertTrue(frame.checksum_ok)
        self.assertEqual(frame.t5c_c, 29.5)
        self.assertEqual(frame.t3_c, 23.5)
        self.assertEqual(frame.t4_c, 27.0)
        self.assertEqual(frame.tp_c, 25.0)
        self.assertEqual(frame.eev, 60)
        self.assertIsNone(frame.th_c)
        self.assertEqual(frame.target_c, 65)
        self.assertFalse(frame.power)

    def test_hybrid_running_pair(self) -> None:
        frame = protocol.parse_frame(HYBRID)
        self.assertEqual(frame.mode, "performance")
        self.assertEqual(frame.mode_raw, (0x02, 0x02))
        self.assertTrue(frame.power)
        self.assertEqual(frame.target_c, 64)

    def test_14_14_is_eheater_with_timer(self) -> None:
        # 2026-10-04: glass E-Heater, element 0xC0, pair 14 14 (not Off).
        raw = bytes.fromhex(
            "fe aa 00 00 ff 14 14 ff ff 50 00 00 00 00 00 4c 80 09 00 46 3c c0 2f 80 5a 03 00 00 00 41 fe 29 55"
        )
        frame = protocol.parse_frame(raw)
        self.assertTrue(frame.checksum_ok)
        self.assertEqual(frame.mode, "electric")
        self.assertTrue(frame.timer)
        self.assertTrue(frame.power)
        self.assertEqual(frame.eheater_flag, 0xC0)
        self.assertEqual(frame.t5c_c, 49.0)
        self.assertEqual(frame.tp_c, 47.0)

    def test_running_eheater_0c_08_is_power_on(self) -> None:
        raw = bytearray(OFF)
        raw[5] = 0x0C
        raw[6] = 0x08
        raw[31] = protocol.checksum33(bytes(raw))
        frame = protocol.parse_frame(bytes(raw))
        self.assertEqual(frame.mode, "electric")
        self.assertTrue(frame.power)
        self.assertFalse(frame.timer)

    def test_feed_splits_noise_and_keeps_a_partial(self) -> None:
        blob = b"\xff\x00" + OFF + b"\xfe\xaa\x00"
        frames, rest = protocol.feed(b"", blob)
        self.assertEqual(len(frames), 1)
        self.assertTrue(frames[0].checksum_ok)
        self.assertEqual(rest, b"\xfe\xaa\x00")
        more, rest = protocol.feed(rest, OFF[3:])
        self.assertEqual(more[0].raw, OFF)
        self.assertEqual(rest, b"")

    def test_bad_checksum_is_not_accepted(self) -> None:
        raw = bytearray(OFF)
        raw[31] ^= 0xFF
        frame = protocol.parse_frame(bytes(raw))
        self.assertFalse(frame.checksum_ok)


class ProbeSilenceTest(unittest.IsolatedAsyncioTestCase):
    async def test_probe_reads_a_frame_and_writes_nothing(self) -> None:
        received = bytearray()

        async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            writer.write(b"\xff\xff" + HYBRID)
            await writer.drain()
            try:
                while True:
                    chunk = await asyncio.wait_for(reader.read(128), timeout=1.0)
                    if not chunk:
                        break
                    received.extend(chunk)
            except TimeoutError:
                pass
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            frame = await gateway.probe_frame("127.0.0.1", port, timeout=3)
        finally:
            server.close()
            await server.wait_closed()
        self.assertEqual(frame.mode, "performance")
        self.assertEqual(frame.target_c, 64)
        self.assertEqual(bytes(received), b"")

    async def test_probe_rejects_noise(self) -> None:
        async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            writer.write(b"\xff" * 40)
            await writer.drain()
            await asyncio.sleep(0.2)
            writer.close()

        server = await asyncio.start_server(handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            with self.assertRaises(gateway.GatewayError) as raised:
                await gateway.probe_frame("127.0.0.1", port, timeout=1)
        finally:
            server.close()
            await server.wait_closed()
        self.assertEqual(raised.exception.code, "no_frame")


class SourceGuardTest(unittest.TestCase):
    def test_bridge_modules_do_not_write_the_socket(self) -> None:
        for name in ("gateway.py", "coordinator.py", "protocol.py"):
            text = (COMP / name).read_text(encoding="utf-8")
            self.assertNotIn("writer.write", text, name)
            self.assertNotIn(".send(", text, name)
            self.assertNotIn("patch_frame", text, name)


if __name__ == "__main__":
    unittest.main()
