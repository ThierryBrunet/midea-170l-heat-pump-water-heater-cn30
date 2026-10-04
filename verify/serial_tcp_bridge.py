#!/usr/bin/env python3
"""Forward /dev/ttyUSB0 (600 8N1) to TCP so the Windows CN30 panel can connect."""

from __future__ import annotations

import argparse
import socket
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from verify.serial_link import SerialLink  # noqa: E402


def pipe(src_read, dst_write, label: str) -> None:
    try:
        while True:
            data = src_read()
            if not data:
                time.sleep(0.02)
                continue
            dst_write(data)
    except Exception as exc:
        sys.stderr.write(f"{label} stop {exc}\n")


def handle(conn: socket.socket, serial_port: str, baud: int) -> None:
    conn.settimeout(1.0)
    ser = SerialLink(serial_port, baud)

    def sock_read() -> bytes:
        try:
            data = conn.recv(512)
        except socket.timeout:
            return b""
        if not data:
            raise ConnectionError("peer closed")
        return data

    def sock_write(data: bytes) -> None:
        conn.sendall(data)

    t1 = threading.Thread(target=pipe, args=(ser.read, sock_write, "ser->tcp"), daemon=True)
    t2 = threading.Thread(target=pipe, args=(sock_read, ser.write, "tcp->ser"), daemon=True)
    t1.start()
    t2.start()
    try:
        while t1.is_alive() and t2.is_alive():
            time.sleep(0.2)
    finally:
        try:
            conn.close()
        except OSError:
            pass
        ser.close()


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--serial", default="/dev/ttyUSB0")
    p.add_argument("--baud", type=int, default=600)
    p.add_argument("--listen", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8766)
    ns = p.parse_args()
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((ns.listen, ns.port))
    srv.listen(1)
    print(f"serial {ns.serial} @{ns.baud} -> tcp {ns.listen}:{ns.port}", flush=True)
    while True:
        conn, addr = srv.accept()
        print(f"client {addr}", flush=True)
        handle(conn, ns.serial, ns.baud)
        print("client gone", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
