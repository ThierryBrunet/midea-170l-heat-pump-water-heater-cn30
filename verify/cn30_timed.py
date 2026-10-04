#!/usr/bin/env python3
"""Timestamped CN30 listen, plus at most one UART write, on the local serial port.

The process that runs this owns /dev/ttyUSB0. Stop serial_tcp_bridge.py first.
Default --tx none only reads. copy / badcs / collide each send one frame and stop.

A write hit is not decided here. The log records EOF-to-TX time, echo, gaps,
leftover bytes, and which frame indexes changed. Keypad E2 is outside this log.
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

from verify.cn30 import checksum33  # noqa: E402
from verify.serial_link import DEFAULT_BAUD, DEFAULT_SERIAL, SerialLink  # noqa: E402

FRAME_LEN = 33
SOF = b"\xfe\xaa"
EOF = 0x55
# 7-byte Off body, the 168-sum checksum, and EOF. Nine bytes. This frame did
# not latch keypad E2 when Economy was running. It is not a 33-byte status copy.
SHORT_OFF = bytes.fromhex("fe aa 00 00 ff 04 04") + bytes(
    (((168 - sum(bytes.fromhex("fe aa 00 00 ff 04 04"))) & 0xFF), EOF)
)
# Move while only listening. A flip here is not an E2 mark by itself.
SPONTANEOUS = {11, 15, 16, 21, 22, 24, 25}


def nudge_setpoint(frame: bytes) -> bytes:
    """Copy one 33-byte frame, lower byte 29 by 1 °C, rebuild the checksum."""
    if len(frame) != FRAME_LEN or frame[29] == 0:
        raise ValueError("cannot nudge setpoint")
    out = bytearray(frame)
    out[29] = (out[29] - 1) & 0xFF
    out[31] = checksum33(bytes(out))
    out[32] = EOF
    return bytes(out)


def bad_checksum(frame: bytes) -> bytes:
    """Same 33 bytes with checksum index 31 forced wrong. EOF stays 0x55."""
    if len(frame) != FRAME_LEN:
        raise ValueError(f"need {FRAME_LEN} bytes, got {len(frame)}")
    out = bytearray(frame)
    out[31] = (out[31] + 1) & 0xFF
    out[32] = EOF
    if out[31] == checksum33(bytes(out)):
        out[31] = (out[31] + 1) & 0xFF
    return bytes(out)


def allows(pair: tuple[int, int], expect: tuple[tuple[int, int], ...]) -> bool:
    return pair in expect


def pull_events(data: bytes, start: int) -> tuple[list[dict], int]:
    """Walk committed bytes. Hold an incomplete SOF or a trailing 0xFE."""
    events: list[dict] = []
    i = start
    n = len(data)
    while i < n:
        j = data.find(SOF, i)
        if j < 0:
            if data[n - 1] == 0xFE:
                if n - 1 > i:
                    events.append({"kind": "gap", "off": i, "n": n - 1 - i})
                return events, n - 1
            if n > i:
                events.append({"kind": "gap", "off": i, "n": n - i})
            return events, n
        if j > i:
            events.append({"kind": "gap", "off": i, "n": j - i})
        if j + FRAME_LEN > n:
            return events, j
        if data[j + FRAME_LEN - 1] == EOF:
            events.append({"kind": "frame", "off": j, "n": FRAME_LEN})
            i = j + FRAME_LEN
            continue
        events.append({"kind": "skip", "off": j, "n": 1})
        i = j + 1
    return events, i


def _span_times(rx_t: list[float], off: int, n: int) -> tuple[float, float]:
    if n <= 0 or off >= len(rx_t):
        return (0.0, 0.0)
    last = min(len(rx_t) - 1, off + n - 1)
    return (rx_t[off], rx_t[last])


def column_diff(pre: list[bytes], post: list[bytes]) -> list[dict]:
    rows: list[dict] = []
    if not pre or not post:
        return rows
    for i in range(FRAME_LEN):
        pre_set = sorted({frame[i] for frame in pre})
        post_set = sorted({frame[i] for frame in post})
        if pre_set == post_set:
            continue
        if len(post_set) > 1 or len(pre_set) > 1:
            kind = "unstable"
        elif i in SPONTANEOUS:
            kind = "spontaneous"
        else:
            kind = "stable-candidate"
        rows.append(
            {
                "index": i,
                "kind": kind,
                "pre": pre_set,
                "post": post_set,
            }
        )
    return rows


def _hex_pair(pair: tuple[int, int]) -> str:
    return f"{pair[0]:02X}:{pair[1]:02X}"


def _parse_expect(text: str) -> tuple[int, int]:
    parts = text.split(":")
    if len(parts) != 2:
        raise argparse.ArgumentTypeError("expect HH:HH")
    try:
        pair = (int(parts[0], 16), int(parts[1], 16))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expect HH:HH") from exc
    if not all(0 <= b <= 0xFF for b in pair):
        raise argparse.ArgumentTypeError("expect HH:HH")
    return pair


class ScriptedLink:
    """In-memory stand-in for --self-check. Not a serial port."""

    def __init__(self, reads: list[bytes]) -> None:
        self.reads = list(reads)
        self.writes: list[bytes] = []

    def read(self, n: int = 512) -> bytes:
        if self.reads:
            return self.reads.pop(0)
        time.sleep(0.02)
        return b""

    def write(self, data: bytes) -> None:
        self.writes.append(bytes(data))

    def close(self) -> None:
        return None


def run(link, cfg: argparse.Namespace) -> dict:
    rx = bytearray()
    rx_t: list[float] = []
    consumed = 0
    pre: list[bytes] = []
    phase = "baseline"
    reason = ""
    payload: bytes | None = None
    t_eof: float | None = None
    t_tx0: float | None = None
    t_tx1: float | None = None
    arm_deadline: float | None = None
    seen_locked = False
    t_begin = time.perf_counter()
    listen_deadline = t_begin + cfg.listen

    def append(chunk: bytes) -> list[dict]:
        nonlocal consumed
        if not chunk:
            return []
        t = time.perf_counter()
        rx.extend(chunk)
        rx_t.extend([t] * len(chunk))
        events, consumed = pull_events(bytes(rx), consumed)
        return events

    while True:
        now = time.perf_counter()
        if phase == "sent" and t_tx1 is not None and now >= t_tx1 + cfg.after:
            break
        if phase == "baseline" and cfg.tx == "none" and now >= listen_deadline:
            break
        if phase == "baseline" and cfg.tx == "nudge" and now >= listen_deadline:
            phase = "refuse"
            reason = "lock to unlock edge not seen; no byte sent"
            break
        if phase == "baseline" and cfg.tx not in ("none", "nudge") and now >= listen_deadline and len(pre) < cfg.baseline:
            phase = "refuse"
            reason = f"only {len(pre)} baseline frame(s) in {cfg.listen:.0f}s; no byte sent"
            break
        if phase == "arm" and arm_deadline is not None and now >= arm_deadline:
            phase = "refuse"
            payload = None
            reason = "no next SOF within 8s; no byte sent"
            break
        if phase == "wait" and t_eof is not None and now >= t_eof + cfg.delay:
            assert payload is not None
            t_tx0 = time.perf_counter()
            print(
                f"SENT {len(payload)} bytes delay_target={cfg.delay:.3f}s "
                f"eof_to_tx={(t_tx0 - t_eof):.3f}s",
                flush=True,
            )
            print(f"     {payload.hex(' ')}", flush=True)
            link.write(payload)
            t_tx1 = time.perf_counter()
            print(
                f"     wire={(t_tx1 - t_tx0):.3f}s (DE hold through last stop bit)",
                flush=True,
            )
            phase = "sent"
            continue

        chunk = link.read(512)
        if phase == "arm" and chunk and payload is not None:
            # Overlap the next report: TX as soon as its SOF is in the buffer.
            pending = bytes(rx) + chunk
            hold = pending[consumed:]
            if hold.startswith(SOF) and len(hold) < FRAME_LEN:
                t = time.perf_counter()
                rx.extend(chunk)
                rx_t.extend([t] * len(chunk))
                t_tx0 = time.perf_counter()
                print(
                    f"SENT {len(payload)} bytes during next SOF "
                    f"held={hold.hex(' ')}",
                    flush=True,
                )
                print(f"     {payload.hex(' ')}", flush=True)
                link.write(payload)
                t_tx1 = time.perf_counter()
                print(f"     wire={(t_tx1 - t_tx0):.3f}s", flush=True)
                phase = "sent"
                continue

        events = append(chunk) if chunk else []
        for event in events:
            if event["kind"] != "frame":
                continue
            raw = bytes(rx[event["off"] : event["off"] + FRAME_LEN])
            t_start, t_end = _span_times(rx_t, event["off"], FRAME_LEN)
            pair = (raw[5], raw[6])
            mark = "OK" if raw[31] == checksum33(raw) else "BADCS"
            print(
                f"FRAME {mark} {_hex_pair(pair)} b11={raw[11]:02X} b29={raw[29]:02X} "
                f"t_end={t_end - t_begin:.3f}s",
                flush=True,
            )
            print(f"     {raw.hex(' ')}", flush=True)
            if phase == "baseline":
                if cfg.tx != "none" and not allows(pair, tuple(cfg.expect)):
                    phase = "refuse"
                    reason = (
                        f"pair {_hex_pair(pair)} is outside --expect; no byte sent"
                    )
                    break
                pre.append(raw)
                if cfg.tx == "nudge":
                    if raw[11] == 0x20:
                        seen_locked = True
                        print("LOCK byte11=20", flush=True)
                        continue
                    if raw[11] == 0x00 and seen_locked:
                        payload = nudge_setpoint(raw)
                        t_eof = t_end
                        phase = "wait"
                        print(
                            f"UNLOCK byte11=00 nudge b29 "
                            f"{raw[29]:02X}->{payload[29]:02X}",
                            flush=True,
                        )
                        continue
                    print(
                        f"WAIT byte11={raw[11]:02X} locked_seen={seen_locked}",
                        flush=True,
                    )
                    continue
                if cfg.tx == "none" or len(pre) < cfg.baseline:
                    continue
                if cfg.tx == "copy":
                    payload = raw
                elif cfg.tx == "badcs":
                    payload = bad_checksum(raw)
                elif cfg.tx == "short":
                    payload = SHORT_OFF
                elif cfg.tx == "collide":
                    payload = raw
                    phase = "arm"
                    arm_deadline = time.perf_counter() + 8.0
                    print("ARMED collide: next SOF gets one exact copy", flush=True)
                    continue
                else:
                    phase = "refuse"
                    reason = f"unknown tx {cfg.tx}"
                    break
                t_eof = t_end
                phase = "wait"
                print(
                    f"WAIT {_hex_pair(pair)} baseline={len(pre)} "
                    f"tx={cfg.tx} delay={cfg.delay:.3f}s",
                    flush=True,
                )
            elif phase == "wait":
                phase = "refuse"
                payload = None
                t_eof = None
                reason = "next frame began before the TX delay elapsed; no byte sent"
                break
            elif phase == "arm":
                phase = "refuse"
                payload = None
                reason = "next frame completed before collide TX; no byte sent"
                break
        if phase == "refuse":
            break

    if phase == "refuse":
        print(f"REFUSED {reason}", flush=True)

    summary = _summarize(
        bytes(rx),
        rx_t,
        t_begin,
        t_eof,
        t_tx0,
        t_tx1,
        payload if t_tx0 is not None else None,
        phase,
        reason,
    )
    print(summary["text"], flush=True)
    return summary


def _summarize(
    data: bytes,
    rx_t: list[float],
    t_begin: float,
    t_eof: float | None,
    t_tx0: float | None,
    t_tx1: float | None,
    payload: bytes | None,
    phase: str,
    reason: str,
) -> dict:
    events, _consumed = pull_events(data, 0)
    frames: list[dict] = []
    gaps: list[dict] = []
    leading: list[dict] = []
    seen_frame = False
    for event in events:
        raw = data[event["off"] : event["off"] + event["n"]]
        t_start, t_end = _span_times(rx_t, event["off"], event["n"])
        if event["kind"] == "frame":
            seen_frame = True
            echo = False
            # Only the module loopback during the DE window. A later identical
            # status report is the heater, not an echo.
            if (
                payload is not None
                and t_tx0 is not None
                and t_tx1 is not None
                and raw == payload
                and t_end > t_tx0 + 0.001
                and t_start <= t_tx1 + 0.05
            ):
                echo = True
            frames.append(
                {
                    "t_start": round(t_start - t_begin, 4),
                    "t_end": round(t_end - t_begin, 4),
                    "pair": [raw[5], raw[6]],
                    "b29": raw[29],
                    "checksum_ok": raw[31] == checksum33(raw),
                    "echo": echo,
                    "hex": raw.hex(" "),
                }
            )
        elif event["kind"] in {"gap", "skip"} and raw:
            item = {
                "kind": event["kind"],
                "t": round(t_start - t_begin, 4),
                "hex": raw.hex(" "),
            }
            if seen_frame:
                gaps.append(item)
            else:
                leading.append(item)

    def heater(frame: dict) -> bool:
        return not frame["echo"] and (t_tx0 is None or frame["t_end"] + t_begin > t_tx0)

    # Pre frames ended before TX. Post frames are heater frames after TX.
    if t_tx0 is None:
        pre_raw = [bytes.fromhex(f["hex"]) for f in frames]
        post_raw: list[bytes] = []
    else:
        pre_raw = [
            bytes.fromhex(f["hex"])
            for f in frames
            if not f["echo"] and (f["t_end"] + t_begin) <= t_tx0 + 0.001
        ]
        post_raw = [bytes.fromhex(f["hex"]) for f in frames if heater(f) and (f["t_end"] + t_begin) > t_tx0]

    diffs = column_diff(pre_raw, post_raw)
    gap_ms: list[float] = []
    heater_frames = [f for f in frames if not f["echo"]]
    for left, right in zip(heater_frames, heater_frames[1:]):
        gap_ms.append(round((right["t_start"] - left["t_end"]) * 1000, 1))

    lines = [
        f"PHASE {phase} {reason}".rstrip(),
        f"RX {len(data)} bytes  frames={len(frames)} between={len(gaps)} leading={len(leading)}",
    ]
    if t_tx0 is not None and t_tx1 is not None:
        eof_note = ""
        if t_eof is not None:
            eof_note = f" eof_to_tx={t_tx0 - t_eof:.3f}s"
        lines.append(
            f"TX once t={t_tx0 - t_begin:.3f}s wire={t_tx1 - t_tx0:.3f}s{eof_note}"
        )
    echo_n = sum(1 for f in frames if f["echo"])
    lines.append(f"ECHO frames={echo_n}")
    lines.append(
        "GAPS_MS " + (" ".join(str(g) for g in gap_ms) if gap_ms else "(none)")
    )
    if gaps:
        shown = " | ".join(g["hex"] for g in gaps[:8])
        lines.append(f"LEFTOVER {shown}")
    else:
        lines.append("LEFTOVER (none)")
    if diffs:
        for row in diffs:
            pre_h = ",".join(f"{b:02X}" for b in row["pre"])
            post_h = ",".join(f"{b:02X}" for b in row["post"])
            lines.append(f"DIFF {row['kind']} [{row['index']}] {pre_h} -> {post_h}")
    elif post_raw:
        lines.append("DIFF (no index changed between pre and post frames)")
    else:
        lines.append("DIFF (no post frames)")
    stable = [row for row in diffs if row["kind"] == "stable-candidate"]
    if post_raw and not stable and not any(g["hex"] for g in gaps):
        lines.append(
            "WIRE no stable body mark and no leftover bytes in this capture"
        )
    elif stable:
        indexes = ",".join(str(row["index"]) for row in stable)
        lines.append(f"WIRE stable-candidate indexes {indexes}")
    lines.append("KEYPAD not in this log; E2 still needs the display")
    text = "\n".join(lines)
    return {
        "text": text,
        "phase": phase,
        "reason": reason,
        "sent": t_tx0 is not None,
        "frames": frames,
        "gaps": gaps,
        "gap_ms": gap_ms,
        "diffs": diffs,
        "rx_hex": data.hex(" "),
    }


def _sample_frame(pair: tuple[int, int] = (0x04, 0x04), target: int = 0x3F) -> bytes:
    raw = bytearray(FRAME_LEN)
    raw[0:5] = bytes.fromhex("fe aa 00 00 ff")
    raw[5], raw[6] = pair
    raw[9] = 0x50
    raw[29] = target
    raw[30] = 0xFE
    raw[31] = checksum33(bytes(raw))
    raw[32] = EOF
    return bytes(raw)


def self_check() -> int:
    raw = _sample_frame()
    other = _sample_frame((0x01, 0x01))
    events, consumed = pull_events(raw + b"\xee" + raw, 0)
    kinds = [event["kind"] for event in events]
    if kinds != ["frame", "gap", "frame"] or consumed != len(raw) * 2 + 1:
        print(f"FAIL walk {kinds} consumed={consumed}")
        return 1
    if (raw + b"\xee" + raw)[events[1]["off"] : events[1]["off"] + events[1]["n"]] != b"\xee":
        print("FAIL leftover")
        return 1
    bad = bad_checksum(raw)
    if bad[31] == checksum33(bad) or bad[32] != EOF or bad[:31] != raw[:31]:
        print("FAIL bad checksum")
        return 1
    if not allows((0x04, 0x04), ((0x04, 0x04), (0x09, 0x08))):
        print("FAIL expect allow")
        return 1
    if allows((0x01, 0x01), ((0x04, 0x04), (0x09, 0x08))):
        print("FAIL expect block")
        return 1

    held, held_at = pull_events(b"\x00\xfe", 0)
    if held_at != 1 or any(event["kind"] == "frame" for event in held):
        print(f"FAIL hold FE held_at={held_at} {held}")
        return 1

    cfg = argparse.Namespace(
        tx="copy",
        expect=((0x04, 0x04),),
        delay=0.0,
        baseline=1,
        listen=1.0,
        after=0.12,
    )
    link = ScriptedLink([raw[:8], raw[8:]])
    summary = run(link, cfg)
    if link.writes != [raw] or not summary["sent"]:
        print(f"FAIL copy write {link.writes!r} sent={summary['sent']}")
        return 1

    cfg_block = argparse.Namespace(
        tx="copy",
        expect=((0x04, 0x04),),
        delay=0.0,
        baseline=1,
        listen=0.4,
        after=0.05,
    )
    blocked = ScriptedLink([other])
    summary = run(blocked, cfg_block)
    if blocked.writes or summary["sent"] or summary["phase"] != "refuse":
        print(f"FAIL refuse phase={summary['phase']} writes={blocked.writes!r}")
        return 1

    cfg_bad = argparse.Namespace(
        tx="badcs",
        expect=((0x04, 0x04),),
        delay=0.0,
        baseline=1,
        listen=1.0,
        after=0.12,
    )
    bad_link = ScriptedLink([raw])
    run(bad_link, cfg_bad)
    if bad_link.writes != [bad]:
        print(f"FAIL badcs payload {bad_link.writes!r}")
        return 1

    cfg_hit = argparse.Namespace(
        tx="collide",
        expect=((0x04, 0x04),),
        delay=0.1,
        baseline=1,
        listen=1.0,
        after=0.12,
    )
    collide = ScriptedLink([raw, SOF + b"\x00"])
    run(collide, cfg_hit)
    if collide.writes != [raw]:
        print(f"FAIL collide {collide.writes!r}")
        return 1

    cfg_early = argparse.Namespace(
        tx="copy",
        expect=((0x04, 0x04),),
        delay=5.0,
        baseline=1,
        listen=1.0,
        after=0.05,
    )
    early = ScriptedLink([raw, raw])
    summary = run(early, cfg_early)
    if early.writes or summary["phase"] != "refuse":
        print(f"FAIL early-frame guard writes={early.writes!r} {summary['phase']}")
        return 1

    quiet_pre = _sample_frame()
    quiet_post = bytearray(quiet_pre)
    quiet_post[30] = 0x00
    quiet_post[31] = checksum33(bytes(quiet_post))
    rows = column_diff([quiet_pre], [bytes(quiet_post)])
    kinds = {row["index"]: row["kind"] for row in rows}
    if kinds.get(30) != "stable-candidate" or kinds.get(31) != "stable-candidate":
        print(f"FAIL diff {kinds}")
        return 1

    if SHORT_OFF != bytes.fromhex("fe aa 00 00 ff 04 04 f9 55"):
        print(f"FAIL short payload {SHORT_OFF.hex()}")
        return 1
    cfg_short = argparse.Namespace(
        tx="short",
        expect=((0x04, 0x04),),
        delay=0.0,
        baseline=1,
        listen=1.0,
        after=0.12,
    )
    short_link = ScriptedLink([raw])
    run(short_link, cfg_short)
    if short_link.writes != [SHORT_OFF]:
        print(f"FAIL short write {short_link.writes!r}")
        return 1

    locked = bytearray(_sample_frame())
    locked[11] = 0x20
    locked[31] = checksum33(bytes(locked))
    locked_b = bytes(locked)
    unlocked = bytearray(locked_b)
    unlocked[11] = 0x00
    unlocked[31] = checksum33(bytes(unlocked))
    unlocked_b = bytes(unlocked)
    nudged = nudge_setpoint(unlocked_b)
    if nudged[29] != unlocked_b[29] - 1 or nudged[31] != checksum33(nudged) or nudged[11] != 0x00:
        print("FAIL nudge builder")
        return 1
    cfg_nudge = argparse.Namespace(
        tx="nudge",
        expect=((0x04, 0x04),),
        delay=0.0,
        baseline=1,
        listen=1.5,
        after=0.12,
    )
    nudge_link = ScriptedLink([locked_b, unlocked_b])
    summary = run(nudge_link, cfg_nudge)
    if nudge_link.writes != [nudged] or not summary["sent"]:
        print(f"FAIL nudge write {nudge_link.writes!r} sent={summary['sent']}")
        return 1
    early_unlock = ScriptedLink([unlocked_b])
    summary = run(early_unlock, cfg_nudge)
    if early_unlock.writes or summary["sent"]:
        print(f"FAIL nudge without prior lock {early_unlock.writes!r}")
        return 1

    print("SELF-CHECK ok")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="CN30 timestamped listen / one TX")
    parser.add_argument("--serial", default=DEFAULT_SERIAL)
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD)
    parser.add_argument(
        "--tx",
        choices=("none", "copy", "badcs", "collide", "short", "nudge"),
        default="none",
        help="none=listen; copy=exact 33-byte frame; badcs=checksum+1; collide=exact copy at the next SOF; short=9-byte Off frame; nudge=setpoint -1 after byte11 goes 20 then 00",
    )
    parser.add_argument(
        "--expect",
        type=_parse_expect,
        action="append",
        default=[],
        help="allowed mode pair before TX, HH:HH. Repeat for several. Required when --tx is not none.",
    )
    parser.add_argument("--delay", type=float, default=0.10, help="seconds after EOF for copy/badcs")
    parser.add_argument("--baseline", type=int, default=2, help="matching frames to record before TX")
    parser.add_argument("--listen", type=float, default=20.0, help="max seconds to wait for baseline")
    parser.add_argument("--after", type=float, default=30.0, help="seconds to read after TX")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        return self_check()
    if args.tx != "none" and not args.expect:
        print("REFUSED --tx requires at least one --expect HH:HH; no byte sent")
        return 2
    if args.baseline < 1:
        print("REFUSED --baseline must be >= 1")
        return 2
    if args.delay < 0:
        print("REFUSED --delay must be >= 0")
        return 2
    print(
        f"CN30 timed {args.serial} @{args.baud} tx={args.tx} "
        f"expect={','.join(_hex_pair(p) for p in args.expect) or '-'} "
        f"delay={args.delay:.3f}s baseline={args.baseline}",
        flush=True,
    )
    try:
        link = SerialLink(args.serial, args.baud, timeout=0.05)
    except OSError as exc:
        print(f"FAIL link {exc}")
        return 2
    try:
        summary = run(link, args)
    finally:
        link.close()
    print("JSON " + json.dumps({k: summary[k] for k in ("phase", "reason", "sent", "gap_ms", "diffs", "gaps")}))
    return 0 if summary["phase"] != "refuse" else 3


if __name__ == "__main__":
    raise SystemExit(main())
