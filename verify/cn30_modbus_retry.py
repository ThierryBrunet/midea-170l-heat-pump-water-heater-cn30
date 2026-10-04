#!/usr/bin/env python3
"""Redo the CN30 Modbus RTU writes on the local UART. One frame per quiet gap.

Same five frames as verify/cn30_bruteforce.py: FC06 slaves 1, 0, and 247,
FC06 power-off, and FC16 power/eco/setpoint. Bauds 600, 9600, 4800, 2400,
1200, 19200. Status is always scored at 600 baud. Stops when mode bytes 5-6
or setpoint byte 29 move. Does not send a 33-byte copy.
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from verify.cn30 import checksum33  # noqa: E402
from verify.serial_link import DEFAULT_BAUD, DEFAULT_SERIAL, SerialLink  # noqa: E402

FRAME_LEN = 33
SOF = b"\xfe\xaa"
EOF = 0x55
BAUDS = (600, 9600, 4800, 2400, 1200, 19200)
WANT_TGT = 60


def crc16_modbus(data: bytes) -> bytes:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return struct.pack("<H", crc)


def modbus_fc6(slave: int, reg: int, value: int) -> bytes:
    pdu = bytes((slave & 0xFF, 0x06)) + struct.pack(">HH", reg & 0xFFFF, value & 0xFFFF)
    return pdu + crc16_modbus(pdu)


def modbus_fc16(slave: int, reg: int, values: list[int]) -> bytes:
    body = bytes((slave & 0xFF, 0x10)) + struct.pack(">HHB", reg & 0xFFFF, len(values), len(values) * 2)
    for value in values:
        body += struct.pack(">H", value & 0xFFFF)
    return body + crc16_modbus(body)


def payloads_for(tgt: int) -> list[tuple[str, bytes]]:
    return [
        ("mb_fc6_s1_r2", modbus_fc6(1, 2, tgt)),
        ("mb_fc6_s0_r2", modbus_fc6(0, 2, tgt)),
        ("mb_fc6_s1_r0", modbus_fc6(1, 0, 0)),
        ("mb_fc16_s1_r0", modbus_fc16(1, 0, [0, 1, tgt])),
        ("mb_fc6_s247_r2", modbus_fc6(247, 2, tgt)),
    ]


def _emit(obj: dict) -> None:
    print(json.dumps(obj, separators=(",", ":")), flush=True)


def _read_frame(link: SerialLink, deadline: float) -> bytes | None:
    buf = bytearray()
    while time.monotonic() < deadline:
        chunk = link.read(64)
        if chunk:
            buf.extend(chunk)
        while True:
            start = buf.find(SOF)
            if start < 0:
                if len(buf) > 2:
                    del buf[:-2]
                break
            if start:
                del buf[:start]
            if len(buf) < FRAME_LEN:
                break
            if buf[32] == EOF and checksum33(bytes(buf[:FRAME_LEN])) == buf[31]:
                frame = bytes(buf[:FRAME_LEN])
                del buf[:FRAME_LEN]
                return frame
            del buf[:2]
        time.sleep(0.01)
    return None


def _set_baud(link: SerialLink, baud: int) -> None:
    link._s.baudrate = baud  # type: ignore[attr-defined]
    link._s.reset_input_buffer()  # type: ignore[attr-defined]


def _quiet_gap(link: SerialLink) -> bytes | None:
    frame = _read_frame(link, time.monotonic() + 8.0)
    if frame is None:
        return None
    time.sleep(0.101)
    if link.read(8):
        return None
    return frame


def self_check() -> None:
    frame = modbus_fc6(1, 2, 60)
    assert len(frame) == 8
    assert frame[:6] == bytes.fromhex("01060002003c")
    assert frame[6:] == crc16_modbus(frame[:6])
    block = modbus_fc16(1, 0, [0, 1, 60])
    assert block[0:2] == bytes((1, 0x10))
    assert block[6] == 6
    assert modbus_fc6(247, 2, 60).hex() == "f7060002003c3c8d"
    assert len(payloads_for(60)) == 5
    assert BAUDS[0] == 600 and 9600 in BAUDS
    print("self-check ok  modbus frames=5 bauds=6")


def _timer_on(frame: bytes) -> bool:
    return bool(frame[5] & 0x10) or bool(frame[6] & 0x10)


def confirm_one(link: SerialLink, wait_s: float) -> int:
    """Wait until the timer bit is clear for two frames, then send slave 247 once."""
    _set_baud(link, 600)
    payload = modbus_fc6(247, 2, 60)
    assert payload.hex() == "f7060002003c3c8d"
    deadline = time.monotonic() + wait_s
    clear_run = 0
    last = None
    while time.monotonic() < deadline:
        frame = _read_frame(link, min(deadline, time.monotonic() + 8.0))
        if frame is None:
            continue
        last = frame
        clear = not _timer_on(frame)
        _emit({
            "event": "seen",
            "mode": f"{frame[5]:02X}:{frame[6]:02X}",
            "b29": frame[29],
            "timer": not clear,
        })
        clear_run = clear_run + 1 if clear else 0
        if clear_run < 2:
            continue
        time.sleep(0.101)
        if link.read(8):
            _emit({"event": "skip-collision"})
            clear_run = 0
            continue
        link.write(payload)
        _emit({"event": "sent", "name": "mb_fc6_s247_r2", "baud": 600, "tx": payload.hex()})
        posts = []
        watch_until = time.monotonic() + 16.0
        while time.monotonic() < watch_until and len(posts) < 3:
            post = _read_frame(link, watch_until)
            if post is None:
                break
            posts.append(post)
            _emit({
                "event": "post",
                "mode": f"{post[5]:02X}:{post[6]:02X}",
                "b29": post[29],
                "timer": _timer_on(post),
                "frame": post.hex(),
            })
        timer_back = any(_timer_on(post) for post in posts)
        _emit({"event": "hit" if timer_back else "done", "timer_back": timer_back, "posts": len(posts)})
        return 0 if timer_back else 2
    _emit({
        "event": "refused",
        "reason": "timer bit stayed set; frame not sent",
        "mode": None if last is None else f"{last[5]:02X}:{last[6]:02X}",
    })
    return 2


def run(link: SerialLink, from_baud: int = 600, to_baud: int | None = None) -> int:
    _set_baud(link, 600)
    base = _read_frame(link, time.monotonic() + 8.0)
    if base is None:
        _emit({"event": "refused", "reason": "no status frame"})
        return 2
    base_mode = (base[5], base[6])
    base29 = base[29]
    want = WANT_TGT if base29 != WANT_TGT else 62
    names = payloads_for(want)
    try:
        start = BAUDS.index(from_baud)
        end = len(BAUDS) if to_baud is None else BAUDS.index(to_baud) + 1
    except ValueError:
        _emit({"event": "refused", "reason": "baud is not in the sweep"})
        return 2
    bauds = BAUDS[start:end]
    if not bauds:
        _emit({"event": "refused", "reason": f"from-baud {from_baud} is past the sweep"})
        return 2
    _emit({
        "event": "start",
        "mode": f"{base_mode[0]:02X}:{base_mode[1]:02X}",
        "b29": base29,
        "timer": _timer_on(base),
        "b21": base[21],
        "want": want,
        "from_baud": from_baud,
        "frames": len(names) * len(bauds),
    })
    for baud in bauds:
        for name, payload in names:
            live = None
            for _attempt in range(6):
                _set_baud(link, 600)
                live = _quiet_gap(link)
                if live is not None:
                    break
            if live is None:
                _emit({"event": "refused", "reason": "no quiet gap", "name": name, "baud": baud})
                return 2
            if (live[5], live[6]) != base_mode or live[29] != base29:
                _emit({
                    "event": "drift",
                    "name": name,
                    "baud": baud,
                    "pre_mode": f"{live[5]:02X}:{live[6]:02X}",
                    "pre29": live[29],
                    "frame": live.hex(),
                })
                return 0
            if baud != 600:
                _set_baud(link, baud)
            link.write(payload)
            _set_baud(link, 600)
            post = _read_frame(link, time.monotonic() + 8.0)
            if post is None:
                _emit({"event": "refused", "reason": "no post frame", "name": name, "baud": baud, "tx": payload.hex()})
                return 2
            changed = (post[5], post[6]) != base_mode or post[29] != base29
            _emit({
                "event": "hit" if changed else "sent",
                "name": name,
                "baud": baud,
                "tx": payload.hex(),
                "post_mode": f"{post[5]:02X}:{post[6]:02X}",
                "post29": post[29],
            })
            if changed:
                _emit({"event": "post", "frame": post.hex()})
                return 0
    _emit({"event": "done", "mode": f"{base_mode[0]:02X}:{base_mode[1]:02X}", "b29": base29})
    return 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--confirm", action="store_true", help="wait until the timer bit clears, then send slave 247 once")
    parser.add_argument("--wait", type=float, default=240.0)
    parser.add_argument("--from-baud", type=int, default=600)
    parser.add_argument("--to-baud", type=int, default=0)
    parser.add_argument("--port", default=DEFAULT_SERIAL)
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD)
    args = parser.parse_args()
    if not args.run:
        self_check()
        return 0
    link = SerialLink(args.port, args.baud, timeout=0.05)
    try:
        link._s.dtr = False  # type: ignore[attr-defined]
        link._s.rts = False  # type: ignore[attr-defined]
        if args.confirm:
            return confirm_one(link, args.wait)
        return run(link, args.from_baud, args.to_baud or None)
    finally:
        link.close()


if __name__ == "__main__":
    raise SystemExit(main())
