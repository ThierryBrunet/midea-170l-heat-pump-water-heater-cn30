#!/usr/bin/env python3
"""Sniff the HP170 display ribbon (USB-UART or USB-RS485) and optionally CN30 in parallel.

Display cable is NOT CN30. Isolator off before tapping. Meter first (see docs).
"""

from __future__ import annotations

import argparse
import socket
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from verify.cn30 import extract_frames, parse_frame  # noqa: E402
from verify.midea170 import DEFAULT_HOST  # noqa: E402

BAUDS = (600, 1200, 2400, 4800, 9600, 19200)


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3]


def _cn30_thread(host: str, port: int, stop: threading.Event, log) -> None:
    buf = b""
    while not stop.is_set():
        try:
            sock = socket.create_connection((host, port), timeout=8)
            sock.settimeout(1.0)
            while not stop.is_set():
                try:
                    chunk = sock.recv(512)
                except socket.timeout:
                    continue
                if not chunk:
                    break
                buf += chunk
                frames, buf = extract_frames(buf)
                for raw in frames:
                    fr = parse_frame(raw)
                    line = f"{_ts()} CN30 {fr.mode} tgt={fr.target_c} tmr={fr.timer} {fr.hex}\n"
                    log(line)
            sock.close()
        except OSError as exc:
            log(f"{_ts()} CN30 err {exc}\n")
            stop.wait(2.0)


def sniff_com(port: str, baud: int, seconds: float, log) -> None:
    try:
        import serial  # type: ignore
    except ImportError:
        print("pip install pyserial", file=sys.stderr)
        raise SystemExit(2)
    ser = serial.Serial(port, baud, bytesize=8, parity="N", stopbits=1, timeout=0.2)
    log(f"{_ts()} COM {port} {baud} 8N1 open\n")
    t_end = time.time() + seconds
    buf = b""
    n = 0
    try:
        while time.time() < t_end:
            chunk = ser.read(256)
            if not chunk:
                continue
            n += len(chunk)
            buf += chunk
            # flush line-oriented hex dumps in ~32-byte slices or on idle
            while len(buf) >= 16:
                piece, buf = buf[:32], buf[32:]
                log(f"{_ts()} DISP {piece.hex(' ')}\n")
        if buf:
            log(f"{_ts()} DISP {buf.hex(' ')}\n")
    finally:
        ser.close()
    log(f"{_ts()} COM closed bytes={n}\n")


def main() -> int:
    p = argparse.ArgumentParser(description="HP170 display-ribbon sniffer")
    p.add_argument("--com", help="COMx of USB-UART/USB-RS485 on the display tap")
    p.add_argument("--baud", type=int, default=0, help="0 = try common bauds for 8s each")
    p.add_argument("--seconds", type=float, default=60)
    p.add_argument("--cn30-host", default=DEFAULT_HOST)
    p.add_argument("--no-cn30", action="store_true")
    p.add_argument("--out", default="")
    ns = p.parse_args()

    cap = ROOT / "captures"
    cap.mkdir(exist_ok=True)
    out = Path(ns.out) if ns.out else cap / time.strftime("display-%Y%m%d-%H%M%S.log")
    fh = out.open("a", encoding="utf-8")

    def log(line: str) -> None:
        sys.stdout.write(line)
        sys.stdout.flush()
        fh.write(line)
        fh.flush()

    log(f"{_ts()} start com={ns.com} baud={ns.baud} file={out}\n")
    stop = threading.Event()
    t = None
    if not ns.no_cn30:
        t = threading.Thread(
            target=_cn30_thread, args=(ns.cn30_host, 502, stop, log), daemon=True
        )
        t.start()

    if not ns.com:
        log("No --com. CN30-only log. Plug USB-UART/RS485 and re-run with --com COMx.\n")
        try:
            time.sleep(ns.seconds)
        except KeyboardInterrupt:
            pass
        stop.set()
        fh.close()
        print(f"wrote {out}")
        return 0

    if ns.baud:
        sniff_com(ns.com, ns.baud, ns.seconds, log)
    else:
        per = max(8.0, ns.seconds / len(BAUDS))
        log(f"{_ts()} baud sweep {BAUDS} {per:.0f}s each — press panel keys during each\n")
        for b in BAUDS:
            sniff_com(ns.com, b, per, log)
    stop.set()
    fh.close()
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
