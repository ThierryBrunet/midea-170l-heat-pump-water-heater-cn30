#!/usr/bin/env python3
"""CLI checks for Chromagen Midea 170L via EW-11A Modbus TCP."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

logging.getLogger("pymodbus").setLevel(logging.ERROR)

ROOT = Path(__file__).resolve().parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from verify.cn30 import prove_tx, selftest as cn30_selftest  # noqa: E402
from verify.midea170 import (  # noqa: E402
    DEFAULT_HOST,
    DEFAULT_PORT,
    DEFAULT_UNIT,
    MODE_LABELS,
    Midea170,
)


def _client(ns: argparse.Namespace) -> Midea170:
    return Midea170(host=ns.host, port=ns.port, unit=ns.unit, timeout=ns.timeout)


def cmd_ping(ns: argparse.Namespace) -> int:
    c = _client(ns)
    ok, detail, ms = c.tcp_probe()
    print(f"{'OK' if ok else 'FAIL'}  tcp {ns.host}:{ns.port}  {detail}  ({ms:.0f} ms)")
    return 0 if ok else 2


def cmd_status(ns: argparse.Namespace) -> int:
    c = _client(ns)
    try:
        st = c.status()
    finally:
        c.close()
    if ns.json:
        print(json.dumps(st.to_dict(), indent=2))
        return 0 if st.connected and not st.errors else 2
    print(f"Midea 170L  {st.host}:{st.port} unit={st.unit}  ({st.elapsed_ms:.0f} ms)")
    print(f"  connected : {st.connected}")
    if st.power is not None:
        print(f"  power     : {'ON' if st.power else 'OFF'}")
    if st.mode is not None:
        print(f"  mode      : {MODE_LABELS.get(st.mode, st.mode)} (raw {st.mode_raw})")
    if st.target_c is not None:
        print(f"  target    : {st.target_c:.0f} °C")
    if st.sterilize is not None:
        print(f"  sterilize : {'ON' if st.sterilize else 'OFF'}")
    temps = [
        ("T5U tank top", st.t5u),
        ("T5L tank bot", st.t5l),
        ("T3  condenser", st.t3),
        ("T4  outdoor", st.t4),
        ("Tp  exhaust", st.tp),
        ("Th  suction", st.th),
    ]
    for label, v in temps:
        print(f"  {label:14s}: {v:.1f} °C" if v is not None else f"  {label:14s}: —")
    for e in st.errors:
        print(f"  error     : {e}")
    if not st.connected:
        print("Hint: EW-11A not reachable. Ping WaterHeater.lan / 192.168.31.219 port 502.")
        return 2
    if st.errors and st.power is None:
        print("Hint: TCP up, Modbus slave silent → RS485 A/B, GND, baud 9600, heater power.")
        return 2
    return 1 if st.errors else 0


def cmd_selftest(ns: argparse.Namespace) -> int:
    if getattr(ns, "cn30", True) and not getattr(ns, "modbus", False):
        steps = cn30_selftest(
            ns.host if not ns.serial else None,
            ns.port,
            seconds=16.0,
            serial=ns.serial or None,
            baud=ns.baud,
        )
        failed = 0
        for s in steps:
            print(f"{'PASS' if s.ok else 'FAIL':4s}  {s.name:16s}  {s.detail}")
            if s.hint and not s.ok:
                print(f"      → {s.hint}")
            if not s.ok:
                failed += 1
        print(f"{len(steps) - failed}/{len(steps)} passed")
        return 2 if failed else 0
    c = _client(ns)
    try:
        steps = c.selftest(writes=ns.writes)
    finally:
        c.close()
    failed = 0
    for s in steps:
        mark = "PASS" if s.ok else "FAIL"
        if not s.ok:
            failed += 1
        print(f"{mark:4s}  {s.name:24s}  {s.detail}")
        if s.hint and not s.ok:
            print(f"      → {s.hint}")
    print(f"{len(steps) - failed}/{len(steps)} passed")
    return 2 if failed else 0


def cmd_power(ns: argparse.Namespace) -> int:
    c = _client(ns)
    try:
        on = ns.state.lower() in ("on", "1", "true")
        c.set_power(on)
        print(f"power {'ON' if on else 'OFF'}")
    finally:
        c.close()
    return 0


def cmd_mode(ns: argparse.Namespace) -> int:
    c = _client(ns)
    try:
        c.set_mode(ns.name)
        print(f"mode {ns.name} (power ON)")
    finally:
        c.close()
    return 0


def cmd_target(ns: argparse.Namespace) -> int:
    c = _client(ns)
    try:
        c.set_target(ns.celsius)
        print(f"target {ns.celsius} °C")
    finally:
        c.close()
    return 0


def cmd_tx_prove(ns: argparse.Namespace) -> int:
    r = prove_tx(
        ns.host if not ns.serial else None,
        ns.port,
        serial=ns.serial or None,
        baud=ns.baud,
        restore=not ns.no_restore,
    )
    print(f"before  tgt={((r.get('before') or {}).get('target_c'))} mode={((r.get('before') or {}).get('mode'))}")
    print(f"sent    tgt={r.get('sent_target')}")
    print(f"after   tgts={[a.get('target_c') for a in (r.get('after') or [])]}")
    print(f"echo    ok={r.get('ok')} echoes={r.get('echoes')}")
    if r.get("restored") is not None:
        print(f"restore ok={r.get('restore_ok')} tgts={[a.get('target_c') for a in r.get('restored') or []]}")
    if r.get("error"):
        print(f"error   {r['error']}")
        return 2
    return 0 if r.get("ok") else 2


def cmd_sterilize(ns: argparse.Namespace) -> int:
    c = _client(ns)
    try:
        on = ns.state.lower() in ("on", "1", "true")
        c.set_sterilize(on)
        print(f"sterilize {'ON' if on else 'OFF'}")
    finally:
        c.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Verify Midea 170L over EW-11A Modbus TCP (before HACS).",
    )
    p.add_argument("--host", default=DEFAULT_HOST, help="EW-11A IP (default 192.168.31.219)")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--serial", default="", help="USB-UART e.g. /dev/ttyUSB0 (skips TCP)")
    p.add_argument("--baud", type=int, default=600)
    p.add_argument("--unit", type=int, default=DEFAULT_UNIT, help="Modbus slave / unit id")
    p.add_argument("--timeout", type=float, default=3.0)
    p.add_argument("--json", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("ping", help="TCP connect to port 502 only")
    sub.add_parser("status", help="Read power, mode, target, sensors")

    st = sub.add_parser("selftest", help="CN30 frame health (default) or Modbus")
    st.add_argument("--modbus", action="store_true", help="Old 9600 Modbus self-test (will fail on CN30 PCB)")
    st.add_argument(
        "--writes",
        action="store_true",
        help="Also round-trip current power/target (does not change setpoints)",
    )

    pw = sub.add_parser("power", help="Write power register")
    pw.add_argument("state", choices=["on", "off"])

    md = sub.add_parser("mode", help="Write mode and turn power on")
    md.add_argument("name", choices=["eco", "performance", "electric"])

    tg = sub.add_parser("target", help="Write target °C (60–70)")
    tg.add_argument("celsius", type=int)

    sz = sub.add_parser("sterilize", help="Write sterilize register 3")
    sz.add_argument("state", choices=["on", "off"])

    tp = sub.add_parser("tx-prove", help="Send patched CN30 frame, watch RX for setpoint echo")
    tp.add_argument("--no-restore", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    ns = build_parser().parse_args(argv)
    handlers = {
        "ping": cmd_ping,
        "status": cmd_status,
        "selftest": cmd_selftest,
        "power": cmd_power,
        "mode": cmd_mode,
        "target": cmd_target,
        "sterilize": cmd_sterilize,
        "tx-prove": cmd_tx_prove,
    }
    try:
        return handlers[ns.cmd](ns)
    except Exception as exc:
        print(f"FAIL  {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
