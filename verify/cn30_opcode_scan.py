#!/usr/bin/env python3
"""Probe CN30 TX opcodes. Unit should stay Off; we never send a power-on mode."""

from __future__ import annotations

import socket
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from verify.cn30 import EOF, checksum33, parse_frame, recv_frames  # noqa: E402
from verify.ew11 import _post  # noqa: E402
from verify.midea170 import DEFAULT_HOST  # noqa: E402


def uart() -> dict:
    r = _post(DEFAULT_HOST, {"CID": 10001, "PL": ["UART"]}, 8)
    return (r.get("PL") or {}).get("UART") or {}


def xye_query() -> bytes:
    """Midea XYE 16-byte query (cmd 0xC0), used on AC wired bus."""
    cmd = 0xC0
    frame = [
        0xAA,
        cmd,
        0x00,
        0x80,
        0x80,
        0x80,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0x55,
    ]
    frame[13] = (255 - cmd) & 0xFF
    s = sum(frame[1:14])
    frame[14] = (256 - (s % 256)) & 0xFF
    return bytes(frame)


def patch_opcode(raw: bytes, idx: int, value: int, target: int | None = None) -> bytes:
    b = bytearray(raw)
    b[idx] = value & 0xFF
    if target is not None:
        b[29] = int(target)
    b[31] = checksum33(bytes(b))
    b[32] = EOF
    return bytes(b)


def main() -> int:
    sock = socket.create_connection((DEFAULT_HOST, 502), timeout=8)
    try:
        base = recv_frames(sock, 12.0, 1)
        if not base:
            print("FAIL no RX")
            return 2
        raw0 = base[-1]
        fr0 = parse_frame(raw0)
        print("base", fr0.mode, "tgt", fr0.target_c, "timer", fr0.timer, fr0.hex)
        cur = int(fr0.target_c or 61)
        want = 60 if cur != 60 else 62

        probes: list[tuple[str, bytes]] = []
        # Opcode in byte2 (currently 00): XYE command slot after AA
        for op in (0x01, 0x02, 0x10, 0x20, 0x80, 0xC0, 0xC3, 0xAA, 0xFF):
            probes.append((f"b2={op:02X} tgt={want}", patch_opcode(raw0, 2, op, want)))
        # Byte4 is always FF on RX — try 00/80 as direction
        probes.append((f"b4=00 tgt={want}", patch_opcode(raw0, 4, 0x00, want)))
        probes.append((f"b4=80 tgt={want}", patch_opcode(raw0, 4, 0x80, want)))
        # Drop leading FE (XYE-shaped 32-byte)
        stripped = raw0[1:]
        probes.append(("drop_FE", stripped if len(stripped) else raw0))
        # Reverse SOF AA FE
        rev = bytearray(raw0)
        rev[0], rev[1] = 0xAA, 0xFE
        rev[31] = checksum33(bytes(rev))
        rev[32] = EOF
        probes.append(("SOF AA FE", bytes(rev)))
        probes.append(("XYE C0 query 16B", xye_query()))

        results = []
        for name, pkt in probes:
            u0 = uart()
            time.sleep(0.35)
            sock.sendall(pkt)
            time.sleep(0.05)
            after = recv_frames(sock, 5.5, 2)
            u1 = uart()
            parsed = [parse_frame(x) for x in after] if after else []
            tgts = [p.target_c for p in parsed]
            modes = [p.mode for p in parsed]
            dtx = (u1.get("SentFrames") or 0) - (u0.get("SentFrames") or 0)
            hit = any(t == want for t in tgts)
            # abort if unit left Off
            if any(m not in (None, "off") for m in modes):
                print("ABORT mode became", modes, "— sending off clone")
                off = bytearray(raw0)
                off[5] = off[6] = 0x04
                off[31] = checksum33(bytes(off))
                off[32] = EOF
                sock.sendall(bytes(off))
                results.append((name, "ABORT_ON", dtx, tgts, modes))
                break
            mark = "ECHO" if hit else "no"
            print(f"{mark:4s} {name:22s} uartTX+{dtx} after_tgt={tgts} mode={modes} len={len(pkt)}")
            results.append((name, mark, dtx, tgts, modes))
        print("---")
        print("echo hits", sum(1 for r in results if r[1] == "ECHO"))
        return 0
    finally:
        sock.close()


if __name__ == "__main__":
    raise SystemExit(main())
