#!/usr/bin/env python3
"""Listen to EW-11 transparent TCP for CN30 600-baud frames."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from verify.cn30 import extract_frames, parse_frame  # noqa: E402
from verify.midea170 import DEFAULT_HOST  # noqa: E402
from verify.serial_link import DEFAULT_SERIAL, open_link  # noqa: E402

DEFAULT_PORT = 502


def sniff(
    host: str | None,
    port: int,
    seconds: float,
    dump: bool,
    *,
    serial: str | None = None,
    baud: int = 600,
) -> int:
    where = serial or f"{host}:{port}"
    print(f"CN30 sniff {where} for {seconds:.0f}s ({baud} 8N1)")
    t_end = time.time() + seconds
    buf = b""
    n = 0
    try:
        link = open_link(serial=serial, baud=baud, host=host, port=port)
    except OSError as exc:
        print(f"FAIL  link {exc}")
        print("Hint: /dev/ttyUSB0 600 8N1, or EW-11 TCP 502 transparent.")
        return 2
    try:
        while time.time() < t_end:
            try:
                chunk = link.read(512)
            except socket.timeout:
                continue
            if not chunk:
                continue
            if dump:
                print(f"  raw +{len(chunk)} {chunk.hex(' ')}")
            buf += chunk
            frames, buf = extract_frames(buf)
            for raw in frames:
                n += 1
                fr = parse_frame(raw)
                mark = "OK" if fr.checksum_ok else "BADCS"
                print(
                    f"{mark} #{n} len={len(raw)} mode={fr.mode} "
                    f"tgt={fr.target_c} t5c={fr.t5c_c} t3={fr.t3_c} "
                    f"t4={fr.t4_c} th={fr.th_c}"
                )
                print(f"     {fr.hex}")
                if fr.notes:
                    print(f"     {fr.notes}")
                if dump:
                    print(json.dumps(fr.to_dict(), indent=2))
    finally:
        link.close()
    print(f"{n} frame(s). leftover {len(buf)} byte(s)")
    if n == 0:
        print(
            "No FE AA … 55 frames. Check A/B on CN30, GND, heater on, "
            "600 8N1. Then swap A/B once."
        )
        return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Sniff Midea CN30 Wire Control")
    p.add_argument("--host", default=DEFAULT_HOST)
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--serial", default="", help="e.g. /dev/ttyUSB0 (skips TCP)")
    p.add_argument("--baud", type=int, default=600)
    p.add_argument("--seconds", type=float, default=20)
    p.add_argument("--dump", action="store_true")
    ns = p.parse_args(argv)
    ser = ns.serial or None
    return sniff(None if ser else ns.host, ns.port, ns.seconds, ns.dump, serial=ser, baud=ns.baud)


if __name__ == "__main__":
    raise SystemExit(main())
