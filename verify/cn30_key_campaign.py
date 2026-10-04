#!/usr/bin/env python3
"""One short keypad-shaped frame per CN30 quiet gap. Stops on a status change.

Campaign 1 only. Does not open the serial port unless --run is passed.
The process owns /dev/ttyUSB0. Stop serial_tcp_bridge.py first.

A hit is a byte outside the spontaneous set (temperatures and the padlock)
changing in the first status frame after a send. Clock digits are not in the
33-byte report, so a clock hit can be invisible here.
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

from verify.cn30 import parse_frame  # noqa: E402
from verify.serial_link import DEFAULT_BAUD, DEFAULT_SERIAL, SerialLink  # noqa: E402

FRAME_LEN = 33
SOF = b"\xfe\xaa"
EOF = 0x55
# Temperatures wander. Byte 11 is the local padlock and is not a pass/fail here.
# Byte 23 tracked byte 16 by one count on the 2026-09-30 pass (0x99 to 0x98).
# Bytes 10, 17, 26, and 27 became 64/01/01/64 when the compressor started.
SPONTANEOUS = {10, 11, 15, 16, 17, 21, 22, 23, 24, 25, 26, 27, 31}

# Manual Fig. 6-3 button numbers. These are the printed key numbers, not a
# captured wire opcode.
KEYS = (
    (0x1B, "up"),       # 27
    (0x1C, "down"),     # 28
    (0x17, "mode"),     # 23
    (0x18, "clock"),    # 24
    (0x19, "time_on"),  # 25
    (0x1A, "time_off"), # 26
    (0x16, "cancel"),   # 22
    (0x15, "on_off"),   # 21
)


def short_sum(body: bytes) -> int:
    """Same 168-constant used by the live 33-byte frames, over this body only."""
    return (168 - sum(body)) & 0xFF


def frame_bare(op: int) -> bytes:
    return bytes((0xFE, 0xAA, op & 0xFF, EOF))


def frame_summed(op: int) -> bytes:
    body = bytes((0xFE, 0xAA, op & 0xFF))
    return body + bytes((short_sum(body), EOF))


def frame_chord(a: int, b: int) -> bytes:
    body = bytes((0xFE, 0xAA, a & 0xFF, b & 0xFF))
    return body + bytes((short_sum(body), EOF))


def campaign1() -> list[tuple[str, bytes]]:
    """Two shapes per key, then the two manual chords. Up is first."""
    out: list[tuple[str, bytes]] = []
    for op, name in KEYS:
        out.append((f"bare-{name}", frame_bare(op)))
        out.append((f"sum-{name}", frame_summed(op)))
    out.append(("chord-clear-error", frame_chord(0x19, 0x16)))  # TIME ON + CANCEL
    out.append(("chord-query", frame_chord(0x18, 0x16)))        # CLOCK + CANCEL
    return out


# Campaign 2 keeps only the four keys whose result is visible in the status
# frame. Header is not the bare FE AA + key number already tried.
SCORE_KEYS = (
    (0x1B, 0x01, "up"),
    (0x1C, 0x02, "down"),
    (0x17, 0x04, "mode"),
    (0x15, 0x08, "on_off"),
)


def frame_dir(op: int) -> bytes:
    """FE AA, then 0x80 where a status frame always has 0x00, then the key."""
    body = bytes((0xFE, 0xAA, 0x80, op & 0xFF))
    return body + bytes((short_sum(body), EOF))


def frame_inv(op: int) -> bytes:
    return bytes((0xAA, 0xFE, op & 0xFF, EOF))


def frame_mask(mask: int) -> bytes:
    body = bytes((0xA5, mask & 0xFF))
    return body + bytes((short_sum(body), EOF))


def campaign2() -> list[tuple[str, bytes]]:
    """Direction mark, inverted header, and a one-bit mask. Up is first."""
    out: list[tuple[str, bytes]] = []
    for op, mask, name in SCORE_KEYS:
        out.append((f"dir-{name}", frame_dir(op)))
        out.append((f"inv-{name}", frame_inv(op)))
        out.append((f"mask-{name}", frame_mask(mask)))
    return out


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


def _changed(before: bytes, after: bytes) -> list[int]:
    return [i for i in range(FRAME_LEN) if before[i] != after[i] and i not in SPONTANEOUS]


def run(link: SerialLink, minutes: float, gap_s: float, start: int, items: list[tuple[str, bytes]]) -> int:
    t0 = time.monotonic()
    end = t0 + minutes * 60.0
    n = start
    sent = 0
    while time.monotonic() < end:
        item_name, payload = items[n % len(items)]
        pre = _read_frame(link, time.monotonic() + 8.0)
        if pre is None:
            print(json.dumps({"event": "no-frame", "name": item_name}))
            continue
        # Quiet gap. If the next SOF is already here, skip this slot.
        time.sleep(gap_s)
        early = link.read(8)
        if early:
            print(json.dumps({
                "event": "skip-collision",
                "name": item_name,
                "early": early.hex(),
            }))
            continue
        link.write(payload)
        sent += 1
        post = _read_frame(link, time.monotonic() + 8.0)
        rec = {
            "event": "sent",
            "n": sent,
            "name": item_name,
            "tx": payload.hex(),
            "t": round(time.monotonic() - t0, 3),
            "pre29": pre[29],
            "pre_mode": f"{pre[5]:02X}:{pre[6]:02X}",
        }
        if post is None:
            rec["post"] = None
            print(json.dumps(rec))
            n += 1
            continue
        changed = _changed(pre, post)
        parsed = parse_frame(post)
        rec.update({
            "post29": post[29],
            "post_mode": f"{post[5]:02X}:{post[6]:02X}",
            "post_ok": parsed.checksum_ok if parsed else False,
            "changed": changed,
        })
        print(json.dumps(rec), flush=True)
        if changed:
            print(json.dumps({
                "event": "hit",
                "name": item_name,
                "tx": payload.hex(),
                "changed": changed,
                "pre": pre.hex(),
                "post": post.hex(),
            }), flush=True)
            return 0
        n += 1
    print(json.dumps({"event": "done", "sent": sent, "minutes": minutes}))
    return 0


def self_check() -> None:
    items = campaign1()
    assert len(items) == 18
    assert items[0] == ("bare-up", bytes.fromhex("feaa1b55"))
    body = bytes.fromhex("feaa1b")
    assert items[1][1] == body + bytes((short_sum(body), EOF))
    assert items[1][1][-1] == EOF
    assert items[-2][0] == "chord-clear-error"
    assert items[-1][0] == "chord-query"
    names = [name for name, _ in items]
    assert names.index("bare-on_off") > names.index("bare-up")
    assert names[11] == "sum-time_off"
    c2 = campaign2()
    assert len(c2) == 12
    assert c2[0][0] == "dir-up"
    assert c2[0][1] == frame_dir(0x1B)
    assert c2[1][1] == bytes.fromhex("aafe1b55")
    assert c2[-1][0] == "mask-on_off"
    print(f"self-check ok  set1={len(items)} set2={len(c2)}")
    for name, raw in c2:
        print(f"  {name:22} {raw.hex()}")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--run", action="store_true", help="open the serial port and send")
    p.add_argument("--set", type=int, choices=(1, 2), default=1)
    p.add_argument("--minutes", type=float, default=10.0)
    p.add_argument("--start", type=int, default=0, help="candidate index to resume from")
    p.add_argument("--gap", type=float, default=0.15, help="seconds after EOF before TX")
    p.add_argument("--port", default=DEFAULT_SERIAL)
    p.add_argument("--baud", type=int, default=DEFAULT_BAUD)
    args = p.parse_args()
    if not args.run:
        self_check()
        return 0
    link = SerialLink(args.port, args.baud, timeout=0.05)
    try:
        items = campaign1() if args.set == 1 else campaign2()
        return run(link, args.minutes, args.gap, args.start, items)
    finally:
        link.close()


if __name__ == "__main__":
    raise SystemExit(main())
