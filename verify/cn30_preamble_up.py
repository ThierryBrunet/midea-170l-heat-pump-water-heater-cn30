#!/usr/bin/env python3
"""One 33-byte status copy, then one Up frame in the same quiet gap.

Refuses to send if the live mode pair is Off (04 04). Does not open the
serial port unless --run is passed. Stop any other owner of /dev/ttyUSB0 first.
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
OFF = (0x04, 0x04)
# Manual key 27, plus the 168-constant checksum this bus already uses.
UP = bytes.fromhex("feaa1be555")


def short_sum(body: bytes) -> int:
    return (168 - sum(body)) & 0xFF


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


def run(link: SerialLink) -> int:
    pre = _read_frame(link, time.monotonic() + 8.0)
    if pre is None:
        print("REFUSED no status frame; no byte sent")
        return 2
    pair = (pre[5], pre[6])
    if pair == OFF:
        print("REFUSED mode is Off 04:04; no byte sent")
        return 2
    time.sleep(0.101)
    early = link.read(8)
    if early:
        print(json.dumps({"event": "skip-collision", "early": early.hex()}))
        print("REFUSED next frame already arriving; no byte sent")
        return 2
    link.write(pre)
    link.write(UP)
    print(json.dumps({
        "event": "sent",
        "mode": f"{pair[0]:02X}:{pair[1]:02X}",
        "pre29": pre[29],
        "copy": pre.hex(),
        "up": UP.hex(),
    }), flush=True)
    posts = []
    deadline = time.monotonic() + 14.0
    while len(posts) < 3 and time.monotonic() < deadline:
        frame = _read_frame(link, deadline)
        if frame is None:
            break
        posts.append(frame)
        print(json.dumps({
            "event": "post",
            "n": len(posts),
            "mode": f"{frame[5]:02X}:{frame[6]:02X}",
            "b29": frame[29],
            "raw": frame.hex(),
        }), flush=True)
    hit = any(frame[29] == ((pre[29] + 1) & 0xFF) for frame in posts)
    print(json.dumps({
        "event": "result",
        "hit": hit,
        "pre29": pre[29],
        "post29": [frame[29] for frame in posts],
    }))
    return 0


def self_check() -> None:
    body = bytes.fromhex("feaa1b")
    assert UP == body + bytes((short_sum(body), EOF))
    assert UP.hex() == "feaa1be555"
    assert OFF == (0x04, 0x04)
    print("self-check ok")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--port", default=DEFAULT_SERIAL)
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD)
    args = parser.parse_args()
    if not args.run:
        self_check()
        return 0
    link = SerialLink(args.port, args.baud, timeout=0.05)
    try:
        return run(link)
    finally:
        link.close()


if __name__ == "__main__":
    raise SystemExit(main())
