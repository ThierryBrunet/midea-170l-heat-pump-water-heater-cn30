#!/usr/bin/env python3
"""CN30 TX brute force: bauds + known protocols, then check 600-baud status.

Success = D30 (setpoint) changes. Does not send power-on mode bytes.
Restores UART to 600/NONE at the end.
"""

from __future__ import annotations

import socket
import struct
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from verify.cn30 import EOF, checksum33, parse_frame, patch_frame, recv_frames  # noqa: E402
from verify.ew11 import get_uart, set_uart  # noqa: E402
from verify.midea170 import DEFAULT_HOST  # noqa: E402

HOST = DEFAULT_HOST
PORT = 502
WANT_TGT = 60


def crc16_modbus(data: bytes) -> bytes:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return struct.pack("<H", crc)


def modbus_fc6(slave: int, reg: int, value: int) -> bytes:
    pdu = bytes((slave, 0x06)) + struct.pack(">HH", reg, value)
    return pdu + crc16_modbus(pdu)


def modbus_fc16(slave: int, reg: int, values: list[int]) -> bytes:
    n = len(values)
    body = bytes((slave, 0x10)) + struct.pack(">HHB", reg, n, n * 2)
    for v in values:
        body += struct.pack(">H", v)
    return body + crc16_modbus(body)


def xye_set_temp(temp_c: int) -> bytes:
    cmd = 0xC3
    frame = [0xAA, cmd, 0x00, 0x80, 0x80, 0x80, 0, 0, temp_c & 0xFF, 0, 0, 0, 0, 0, 0, 0x55]
    frame[13] = (255 - cmd) & 0xFF
    frame[14] = (256 - (sum(frame[1:14]) % 256)) & 0xFF
    return bytes(frame)


def xye_query() -> bytes:
    cmd = 0xC0
    frame = [0xAA, cmd, 0x00, 0x80, 0x80, 0x80, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0x55]
    frame[13] = (255 - cmd) & 0xFF
    frame[14] = (256 - (sum(frame[1:14]) % 256)) & 0xFF
    return bytes(frame)


def send_burst(payloads: list[bytes], pause: float = 0.08) -> None:
    sock = socket.create_connection((HOST, PORT), timeout=8)
    try:
        for p in payloads:
            sock.sendall(p)
            time.sleep(pause)
    finally:
        sock.close()


def read_status(seconds: float = 12.0) -> dict | None:
    sock = socket.create_connection((HOST, PORT), timeout=8)
    try:
        rx = recv_frames(sock, seconds, 2)
    finally:
        sock.close()
    if not rx:
        return None
    return parse_frame(rx[-1]).to_dict()


def ensure_600() -> None:
    for _ in range(8):
        try:
            u = get_uart(HOST)
            if u.get("Baudrate") == 600 and u.get("UartProto") == "NONE":
                return
            break
        except Exception:
            time.sleep(3)
    print("  restore UART 600 NONE")
    set_uart(HOST, baud=600, proto="NONE", gap=1000)


def payloads_for_tgt(tgt: int, template: bytes | None) -> list[tuple[str, bytes]]:
    out: list[tuple[str, bytes]] = [
        ("mb_fc6_s1_r2", modbus_fc6(1, 2, tgt)),
        ("mb_fc6_s0_r2", modbus_fc6(0, 2, tgt)),
        ("mb_fc6_s1_r0", modbus_fc6(1, 0, 0)),  # power off only
        ("mb_fc16_s1_r0", modbus_fc16(1, 0, [0, 1, tgt])),  # pwr off, eco, tgt
        ("mb_fc6_s247_r2", modbus_fc6(247, 2, tgt)),
        ("xye_c3_temp", xye_set_temp(tgt)),
        ("xye_c0", xye_query()),
        ("aa55_tgt", bytes((0xAA, 0x55, tgt, 0x55))),
        ("fe55_tgt", bytes((0xFE, 0x55, tgt, 0x55))),
    ]
    if template and len(template) == 33:
        out.append(("cn30_clone_tgt", patch_frame(template, target=tgt)))
        b = bytearray(template)
        b[2] = 0x80
        b[29] = tgt
        b[31] = checksum33(bytes(b))
        b[32] = EOF
        out.append(("cn30_b2_80", bytes(b)))
        b = bytearray(template)
        b[0], b[1] = 0xAA, 0xFE
        b[29] = tgt
        b[31] = checksum33(bytes(b))
        b[32] = EOF
        out.append(("cn30_aafe", bytes(b)))
    return out


def main() -> int:
    print("CN30 brute force — setpoint-only probes; UART restored to 600")
    ensure_600()
    st0 = read_status(14)
    if not st0:
        print("FAIL no CN30 status at 600")
        return 2
    print("baseline", st0.get("mode"), "tgt", st0.get("target_c"), "pwr", st0.get("power"))
    raw_hex = st0.get("hex") or ""
    template = bytes.fromhex(raw_hex) if raw_hex else None
    want = WANT_TGT if st0.get("target_c") != WANT_TGT else 62
    print("want tgt", want)

    # Fast path: stay on 600, fire all protocol variants
    print("--- baud 600 (no UART change) ---")
    send_burst([p for _, p in payloads_for_tgt(want, template)])
    time.sleep(0.5)
    st = read_status(14)
    print("after 600 burst", None if not st else (st.get("mode"), st.get("target_c")))
    if st and st.get("target_c") == want:
        print("HIT at 600 baud mixed protocols")
        return 0

    bauds = (9600, 4800, 2400, 1200, 19200)
    for baud in bauds:
        protos = ("Modbus",) if baud == 9600 else ("NONE",)
        for proto in protos:
            print(f"--- UART {baud} {proto} ---")
            set_uart(HOST, baud=baud, proto=proto, gap=200 if baud >= 4800 else 1000)
            try:
                send_burst([p for _, p in payloads_for_tgt(want, template)])
            except OSError as exc:
                print("  send fail", exc)
            ensure_600()
            st = read_status(14)
            print("  status", None if not st else (st.get("mode"), st.get("target_c"), st.get("power")))
            if st and st.get("target_c") == want:
                print("HIT", baud, proto)
                return 0
            if st and st.get("mode") not in (st0.get("mode"), None, "off") and st.get("power"):
                print("NOTE mode changed to", st.get("mode"), "— check panel")

    print("no setpoint echo on any baud/protocol set")
    ensure_600()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
