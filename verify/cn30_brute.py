#!/usr/bin/env python3
"""Walk one opcode byte through four short frames. Stop if mode or setpoint moves.

One frame per quiet gap. Does not open the serial port unless --run is passed.
Stop any other owner of /dev/ttyUSB0 first.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from verify.serial_link import DEFAULT_BAUD, DEFAULT_SERIAL, SerialLink  # noqa: E402

FRAME_LEN = 33
SOF = b"\xfe\xaa"
EOF = 0x55
SHAPES = ("bare", "sum", "inv", "dir")


def short_sum(body: bytes) -> int:
    return (168 - sum(body)) & 0xFF


def payload(shape: str, op: int) -> bytes:
    op &= 0xFF
    if shape == "bare":
        return bytes((0xFE, 0xAA, op, EOF))
    if shape == "inv":
        return bytes((0xAA, 0xFE, op, EOF))
    if shape == "sum":
        body = bytes((0xFE, 0xAA, op))
        return body + bytes((short_sum(body), EOF))
    if shape == "dir":
        body = bytes((0xFE, 0xAA, 0x80, op))
        return body + bytes((short_sum(body), EOF))
    raise ValueError(shape)


def _read_frame(link: SerialLink, deadline: float) -> bytes | None:
    buf = bytearray()
    while time.monotonic() < deadline:
        chunk = link.read(64)
        if not chunk:
            continue
        buf.extend(chunk)
        idx = buf.find(SOF)
        if idx < 0:
            if len(buf) > 64:
                del buf[:-2]
            continue
        if idx:
            del buf[:idx]
        if len(buf) >= FRAME_LEN and buf[FRAME_LEN - 1] == EOF:
            return bytes(buf[:FRAME_LEN])
        if len(buf) > FRAME_LEN + 8:
            del buf[:2]
    return None


def _emit(rec: dict) -> None:
    print(json.dumps(rec), flush=True)


def _sweep(link: SerialLink, gap_s: float) -> int:
    """Same as run, but a collision retries the same opcode."""
    base = _read_frame(link, time.monotonic() + 8.0)
    if base is None:
        _emit({"event": "refused", "reason": "no status frame"})
        return 2
    base_mode = (base[5], base[6])
    base29 = base[29]
    _emit({
        "event": "start",
        "mode": f"{base_mode[0]:02X}:{base_mode[1]:02X}",
        "b29": base29,
        "shapes": list(SHAPES),
        "ops": 256,
    })
    sent = 0
    t0 = time.monotonic()
    for shape in SHAPES:
        op = 0
        while op < 256:
            pre = _read_frame(link, time.monotonic() + 8.0)
            if pre is None:
                _emit({"event": "refused", "reason": "lost status", "sent": sent})
                return 2
            if (pre[5], pre[6]) != base_mode or pre[29] != base29:
                _emit({
                    "event": "drift",
                    "sent": sent,
                    "shape": shape,
                    "op": f"{op:02X}",
                    "pre_mode": f"{pre[5]:02X}:{pre[6]:02X}",
                    "pre29": pre[29],
                    "raw": pre.hex(),
                })
                return 0
            time.sleep(gap_s)
            early = link.read(8)
            if early:
                _emit({"event": "skip-collision", "shape": shape, "op": f"{op:02X}"})
                continue
            frame = payload(shape, op)
            link.write(frame)
            sent += 1
            post = _read_frame(link, time.monotonic() + 8.0)
            rec = {
                "event": "sent",
                "n": sent,
                "shape": shape,
                "op": f"{op:02X}",
                "tx": frame.hex(),
                "t": round(time.monotonic() - t0, 1),
            }
            if post is None:
                rec["post"] = None
                _emit(rec)
                op += 1
                continue
            rec["post_mode"] = f"{post[5]:02X}:{post[6]:02X}"
            rec["post29"] = post[29]
            if sent % 16 == 0 or op in (0, 255):
                _emit(rec)
            if (post[5], post[6]) != base_mode or post[29] != base29:
                rec["event"] = "hit"
                rec["pre"] = pre.hex()
                rec["post"] = post.hex()
                _emit(rec)
                return 0
            op += 1
    _emit({"event": "done", "sent": sent, "t": round(time.monotonic() - t0, 1)})
    return 0


def payload2(hi: int, lo: int) -> bytes:
    """FE AA, two opcode bytes, the 168-constant checksum, then 55."""
    body = bytes((0xFE, 0xAA, hi & 0xFF, lo & 0xFF))
    return body + bytes((short_sum(body), EOF))


OFF_MODE = (0x04, 0x04)
GLYPH_DELAY_S = 0.101


def _glyph_copy(link: SerialLink, base: bytes) -> tuple[bytes | None, int]:
    """One exact 33-byte copy, about 101 ms after EOF. Never send while Off.

    Returns (baseline, 0) to walk, or (None, code) when the walk must not start.
    A later hi/lo opcode is not part of this send.
    """
    for _attempt in range(8):
        if (base[5], base[6]) == OFF_MODE:
            _emit({"event": "refused", "reason": "live mode is Off 04 04; glyph copy not sent"})
            return None, 2
        time.sleep(GLYPH_DELAY_S)
        if link.read(8):
            _emit({"event": "skip-collision", "phase": "glyph"})
            nxt = _read_frame(link, time.monotonic() + 8.0)
            if nxt is None:
                _emit({"event": "refused", "reason": "lost status during glyph"})
                return None, 2
            base = nxt
            continue
        link.write(base)
        _emit({
            "event": "glyph",
            "tx": base.hex(),
            "mode": f"{base[5]:02X}:{base[6]:02X}",
            "b29": base[29],
        })
        deadline = time.monotonic() + 8.0
        post: bytes | None = None
        while time.monotonic() < deadline:
            frame = _read_frame(link, deadline)
            if frame is None:
                break
            if frame == base:
                continue
            post = frame
            break
        if post is None:
            return base, 0
        if (post[5], post[6]) != (base[5], base[6]) or post[29] != base[29]:
            _emit({
                "event": "hit",
                "phase": "glyph",
                "pre": base.hex(),
                "post": post.hex(),
                "pre_mode": f"{base[5]:02X}:{base[6]:02X}",
                "post_mode": f"{post[5]:02X}:{post[6]:02X}",
                "pre29": base[29],
                "post29": post[29],
            })
            return None, 0
        return post, 0
    _emit({"event": "refused", "reason": "glyph gap never quiet"})
    return None, 2


def _sweep2(
    link: SerialLink,
    gap_s: float,
    start_hi: int = 0,
    start_lo: int = 0,
    glyph: bool = False,
) -> int:
    """Walk both opcode bytes. 65536 frames. Stop if mode or setpoint moves."""
    base = _read_frame(link, time.monotonic() + 8.0)
    if base is None:
        _emit({"event": "refused", "reason": "no status frame"})
        return 2
    if glyph:
        base, code = _glyph_copy(link, base)
        if base is None:
            return code
    base_mode = (base[5], base[6])
    base29 = base[29]
    _emit({
        "event": "start",
        "width": 2,
        "mode": f"{base_mode[0]:02X}:{base_mode[1]:02X}",
        "b29": base29,
        "shape": "sum2",
        "ops": 65536,
        "resume_hi": f"{start_hi:02X}",
        "resume_lo": f"{start_lo:02X}",
        "glyph": glyph,
    })
    sent = (start_hi & 0xFF) * 256 + (start_lo & 0xFF)
    t0 = time.monotonic()
    hi = start_hi & 0xFF
    while hi < 256:
        lo = (start_lo & 0xFF) if hi == (start_hi & 0xFF) else 0
        while lo < 256:
            pre = _read_frame(link, time.monotonic() + 8.0)
            if pre is None:
                _emit({"event": "refused", "reason": "lost status", "sent": sent})
                return 2
            if (pre[5], pre[6]) != base_mode or pre[29] != base29:
                _emit({
                    "event": "drift",
                    "sent": sent,
                    "hi": f"{hi:02X}",
                    "lo": f"{lo:02X}",
                    "pre_mode": f"{pre[5]:02X}:{pre[6]:02X}",
                    "pre29": pre[29],
                    "raw": pre.hex(),
                })
                return 0
            time.sleep(gap_s)
            if link.read(8):
                _emit({"event": "skip-collision", "hi": f"{hi:02X}", "lo": f"{lo:02X}"})
                continue
            frame = payload2(hi, lo)
            link.write(frame)
            sent += 1
            post = _read_frame(link, time.monotonic() + 8.0)
            rec = {
                "event": "sent",
                "n": sent,
                "hi": f"{hi:02X}",
                "lo": f"{lo:02X}",
                "tx": frame.hex(),
                "t": round(time.monotonic() - t0, 1),
            }
            if post is None:
                rec["post"] = None
                _emit(rec)
                lo += 1
                continue
            rec["post_mode"] = f"{post[5]:02X}:{post[6]:02X}"
            rec["post29"] = post[29]
            if sent % 16 == 0 or lo in (0, 255) or (hi == (start_hi & 0xFF) and lo == (start_lo & 0xFF)):
                _emit(rec)
            if (post[5], post[6]) != base_mode or post[29] != base29:
                rec["event"] = "hit"
                rec["pre"] = pre.hex()
                rec["post"] = post.hex()
                _emit(rec)
                return 0
            lo += 1
        hi += 1
    _emit({"event": "done", "sent": sent, "t": round(time.monotonic() - t0, 1)})
    return 0


def self_check() -> None:
    assert payload("bare", 0x1B).hex() == "feaa1b55"
    assert payload("inv", 0x1B).hex() == "aafe1b55"
    body = bytes.fromhex("feaa1b")
    assert payload("sum", 0x1B) == body + bytes((short_sum(body), EOF))
    assert payload("dir", 0x1B).hex() == "feaa801b6555"
    pair = bytes.fromhex("feaa0001")
    assert payload2(0x00, 0x01) == pair + bytes((short_sum(pair), EOF))
    assert payload2(0x12, 0xC0).hex() == "feaa12c02e55"
    assert 0x12 * 256 + 0xBF + 1 == 4800
    assert len(SHAPES) == 4
    print("self-check ok  shapes=4 ops=256 width2=65536 resume=12C0")


def _u8(text: str) -> int:
    value = int(text, 0)
    if value < 0 or value > 255:
        raise argparse.ArgumentTypeError("byte must be 0..255")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--bytes", type=int, choices=(1, 2), default=1)
    parser.add_argument("--gap", type=float, default=0.15)
    parser.add_argument("--port", default=DEFAULT_SERIAL)
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD)
    parser.add_argument("--hi", type=_u8, default=0, help="two-byte resume high byte, e.g. 0x12")
    parser.add_argument("--lo", type=_u8, default=0, help="two-byte resume low byte, e.g. 0xC0")
    parser.add_argument("--glyph", action="store_true", help="send one exact 33-byte copy before the walk")
    args = parser.parse_args()
    if not args.run:
        self_check()
        return 0
    link = SerialLink(args.port, args.baud, timeout=0.05)
    try:
        if args.bytes == 2:
            return _sweep2(link, args.gap, args.hi, args.lo, args.glyph)
        return _sweep(link, args.gap)
    finally:
        link.close()


if __name__ == "__main__":
    raise SystemExit(main())
