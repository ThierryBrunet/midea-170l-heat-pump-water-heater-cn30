"""Chromagen/Midea 170L Wire Control CN30 protocol (not Modbus).

Community capture (dannymanno, HA #773718): RS485 @ 600 8N1, 33-byte frames
about every 4 s. Header FE AA 00 00 FF … checksum … 55.

Checksum (1-based bytes 1–31): (168 - sum) mod 256 == byte 32.
"""

from __future__ import annotations

import socket
import time
from dataclasses import dataclass, field
from typing import Any

SOF = bytes((0xFE, 0xAA, 0x00, 0x00, 0xFF))
EOF = 0x55
FRAME_LEN = 33
FRAME_LEN_SHORT = 30  # EW-11 sometimes drops bytes 5,6,8 of USB capture

MODE_BY_D6 = {
    0x01: "eco",  # running pair 01 01
    0x02: "performance",  # running pair 02 02, Hybrid
    0x04: "off",
    0x09: "eco",  # max 65
    0x0A: "performance",  # Hybrid, max 70
    0x0C: "electric",  # E-Heater, max 70
    0x11: "eco",
    0x12: "performance",
    0x14: "electric",
    0x1A: "off",
}

MODE_LABELS = {
    "eco": "Eco",
    "performance": "Hybrid",
    "electric": "E-Heater",
    "off": "Off",
}
MODE_TO_D6 = {
    "off": 0x04,
    "eco": 0x09,
    "performance": 0x0A,
    "electric": 0x0C,
}


def checksum33(data: bytes) -> int:
    """(168 - sum of 1-based bytes 1..31) mod 256. Matches live CN30 frames."""
    return (168 - sum(data[:31])) & 0xFF


def decode_mode(d6: int) -> str | None:
    """Exact byte first, then the low nibble.

    ``14 14`` is E-Heater plus timer (2026-10-04 glass), not Off.
    """
    return MODE_BY_D6.get(d6) or MODE_BY_D6.get(d6 & 0x0F)


def power_on(d6: int, d7: int, mode: str | None) -> bool | None:
    """Power button, separate from the stored mode.

    Off when the mode itself is off. Running E-Heater ``0C 08`` is on.
    Stored-mode marker 0x08 on byte 6 (``09 08``, ``0A 08``, ``1C 18``) is off.
    ``14 14`` is E-Heater plus timer and is on. Repeated running pairs
    ``01 01`` and ``02 02`` are on.
    """
    if mode is None:
        return None
    if mode == "off":
        return False
    if (d6 & 0x0F) == 0x0C and (d6 & 0x10) == 0:
        return True
    if (d7 & ~0x10) == 0x08:
        return False
    return True


def scale_temp(raw: int | None) -> float | None:
    if raw is None:
        return None
    return (raw * 0.5) - 15.0


def checksum30(data: bytes) -> int:
    return (144 - sum(data[:29])) & 0xFF


def extract_frames(buf: bytes) -> tuple[list[bytes], bytes]:
    """Split a byte stream into candidate frames; return (frames, remainder)."""
    frames: list[bytes] = []
    i = 0
    while True:
        j = buf.find(b"\xfe\xaa", i)
        if j < 0:
            return frames, buf[i:]
        if j + FRAME_LEN <= len(buf) and buf[j + FRAME_LEN - 1] == EOF:
            frames.append(buf[j : j + FRAME_LEN])
            i = j + FRAME_LEN
            continue
        if j + FRAME_LEN_SHORT <= len(buf) and buf[j + FRAME_LEN_SHORT - 1] == EOF:
            frames.append(buf[j : j + FRAME_LEN_SHORT])
            i = j + FRAME_LEN_SHORT
            continue
        if j + FRAME_LEN > len(buf):
            return frames, buf[j:]
        i = j + 1


@dataclass
class Cn30Frame:
    raw: bytes
    checksum_ok: bool
    mode: str | None
    mode_raw: tuple[int, int] | None
    power: bool | None
    timer: bool | None
    target_c: int | None
    t5c_c: float | None
    t3_c: float | None
    t4_c: float | None
    th_c: float | None
    tp_c: float | None
    eev: int | None
    mode_max_c: int | None
    mode_min_c: int | None
    eheater_flag: int | None
    hex: str = ""
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "hex": self.hex,
            "len": len(self.raw),
            "checksum_ok": self.checksum_ok,
            "mode": self.mode,
            "mode_label": MODE_LABELS.get(self.mode or "", self.mode),
            "mode_raw": list(self.mode_raw) if self.mode_raw else None,
            "power": self.power,
            "timer": self.timer,
            "target_c": self.target_c,
            "temps": {
                "t5c": self.t5c_c,
                "t3": self.t3_c,
                "t4": self.t4_c,
                "th": self.th_c,
                "tp": self.tp_c,
            },
            "eev": self.eev,
            "mode_max_c": self.mode_max_c,
            "mode_min_c": self.mode_min_c,
            "eheater_flag": self.eheater_flag,
            "notes": self.notes,
        }


def parse_frame(raw: bytes) -> Cn30Frame:
    hx = raw.hex(" ")
    notes: list[str] = []
    if len(raw) == FRAME_LEN:
        ok = raw[31] == checksum33(raw) and raw[32] == EOF
        if not ok:
            notes.append(f"cs got {raw[31]:02X} expect {checksum33(raw):02X}")
        d6, d7 = raw[5], raw[6]
        # T5L tank byte 16 (byte 23 copies it), T3 evaporator byte 15, T4 ambient byte 24.
        # Scale (raw * 0.5) - 15. TP discharge byte 22 unscaled °C. EEV byte 20 integer.
        # TH suction has no separate byte in this 33-byte frame.
        t5c = scale_temp(raw[16])
        t3 = scale_temp(raw[15])
        t4 = scale_temp(raw[24])
        th = None
        tp = float(raw[22])
        tmax, tmin = raw[19], raw[20]
        eev = raw[20]
        eheat = raw[21]
        target = raw[29]
    elif len(raw) == FRAME_LEN_SHORT:
        ok = raw[28] == checksum30(raw) and raw[29] == EOF
        if not ok:
            notes.append(f"cs30 got {raw[28]:02X} expect {checksum30(raw):02X}")
        notes.append("30-byte EW-11 shortened frame")
        d6, d7 = raw[5], raw[6]
        t3 = scale_temp(raw[12]) if len(raw) > 12 else None
        t5c = scale_temp(raw[13]) if len(raw) > 13 else None
        t4 = None
        th = None
        tp = None
        eev = None
        tmax = tmin = eheat = target = None
    else:
        return Cn30Frame(
            raw=raw,
            checksum_ok=False,
            mode=None,
            mode_raw=None,
            power=None,
            timer=None,
            target_c=None,
            t5c_c=None,
            t3_c=None,
            t4_c=None,
            th_c=None,
            tp_c=None,
            eev=None,
            mode_max_c=None,
            mode_min_c=None,
            eheater_flag=None,
            hex=hx,
            notes=[f"unexpected length {len(raw)}"],
        )

    timer = bool(d6 & 0x10) or bool(d7 & 0x10)
    mode = decode_mode(d6)
    if mode is None:
        notes.append(f"unknown mode byte {d6:02X}/{d7:02X}")
    if timer:
        notes.append("timer bit D6/D7 0x10")
    power = power_on(d6, d7, mode)
    return Cn30Frame(
        raw=raw,
        checksum_ok=ok,
        mode=mode,
        mode_raw=(d6, d7),
        power=power,
        timer=timer,
        target_c=target,
        t5c_c=t5c,
        t3_c=t3,
        t4_c=t4,
        th_c=th,
        tp_c=tp,
        eev=eev,
        mode_max_c=tmax,
        mode_min_c=tmin,
        eheater_flag=eheat,
        hex=hx,
        notes=notes,
    )


@dataclass
class StepResult:
    name: str
    ok: bool
    detail: str
    hint: str = ""


def selftest(
    host: str | None = None,
    port: int = 502,
    seconds: float = 16.0,
    *,
    serial: str | None = None,
    baud: int = 600,
) -> list[StepResult]:
    """Open link + at least one valid CN30 frame with plausible fields."""
    from verify.serial_link import open_link

    steps: list[StepResult] = []
    t0 = time.perf_counter()
    label = serial or f"{host}:{port}"
    try:
        link = open_link(serial=serial, baud=baud, host=host, port=port)
    except Exception as exc:
        steps.append(
            StepResult(
                "link",
                False,
                str(exc),
                "Serial /dev/ttyUSB0 600 8N1, or EW-11 TCP 502.",
            )
        )
        return steps
    try:
        ms = (time.perf_counter() - t0) * 1000
        steps.append(StepResult("link", True, f"{label} open ({ms:.0f} ms)"))
        frames = recv_frames(link, seconds, want=2)
    finally:
        link.close()

    if not frames:
        steps.append(
            StepResult(
                "cn30_frame",
                False,
                f"no FE AA frame in {seconds:.0f}s",
                "CN30 A/B, GND, 600 baud transparent. Heater powered.",
            )
        )
        return steps

    fr = parse_frame(frames[-1])
    steps.append(
        StepResult(
            "cn30_frame",
            True,
            f"{len(frames)} frame(s) len={len(fr.raw)} {fr.hex[:24]}…",
        )
    )
    steps.append(
        StepResult(
            "checksum",
            fr.checksum_ok,
            "ok" if fr.checksum_ok else "; ".join(fr.notes) or "fail",
        )
    )
    steps.append(
        StepResult(
            "mode",
            fr.mode is not None,
            f"{fr.mode} raw={fr.mode_raw} timer={fr.timer} power={fr.power}",
        )
    )
    tgt_ok = fr.target_c is not None and 55 <= fr.target_c <= 75
    steps.append(
        StepResult(
            "setpoint",
            bool(tgt_ok),
            f"{fr.target_c} °C (D30)",
            "" if tgt_ok else "D30 not in 55–75 °C",
        )
    )
    temps = [
        ("t5c", fr.t5c_c),
        ("t3", fr.t3_c),
        ("t4", fr.t4_c),
        ("tp", fr.tp_c),
    ]
    for name, val in temps:
        ok = val is not None and -20 <= val <= 95
        steps.append(
            StepResult(
                f"temp_{name}",
                ok,
                f"{val:.1f} °C" if val is not None else "missing",
            )
        )
    return steps


def patch_frame(
    raw: bytes,
    *,
    target: int | None = None,
    mode: str | None = None,
    timer: bool | None = None,
) -> bytes:
    """Clone a 33-byte status frame and rewrite control bytes + checksum."""
    if len(raw) != FRAME_LEN:
        raise ValueError(f"need {FRAME_LEN}-byte frame, got {len(raw)}")
    b = bytearray(raw)
    tbit_now = bool(b[5] & 0x10)
    use_timer = tbit_now if timer is None else timer
    tbit = 0x10 if use_timer else 0
    if mode is not None:
        if mode not in MODE_TO_D6:
            raise ValueError(f"mode {mode}")
        base = MODE_TO_D6[mode]
        if mode == "off":
            b[5] = b[6] = base | tbit
        else:
            b[5] = base | tbit
            b[6] = 0x08 | tbit
    elif timer is not None:
        if timer:
            b[5] |= 0x10
            b[6] |= 0x10
        else:
            b[5] &= 0x0F
            b[6] &= 0x0F
            if (b[5] & 0x0F) == 0x04:
                b[6] = 0x04
    if target is not None:
        t = int(target)
        if t < 60 or t > 70:
            raise ValueError(f"target {t} outside 60–70")
        b[29] = t
    b[31] = checksum33(bytes(b))
    b[32] = EOF
    return bytes(b)


def recv_frames(link: Any, seconds: float, want: int = 8) -> list[bytes]:
    """Read CN30 frames from a TCP socket or SerialLink (read/write/close)."""
    if hasattr(link, "settimeout"):
        try:
            link.settimeout(1.0)
        except Exception:
            pass
    buf = b""
    out: list[bytes] = []
    deadline = time.time() + seconds
    while time.time() < deadline and len(out) < want:
        try:
            chunk = link.read(512) if hasattr(link, "read") else link.recv(512)
        except socket.timeout:
            continue
        except OSError:
            break
        if not chunk:
            time.sleep(0.05)
            continue
        buf += chunk
        got, buf = extract_frames(buf)
        out.extend(got)
    return out


def prove_tx(
    host: str | None = None,
    port: int = 502,
    *,
    serial: str | None = None,
    baud: int = 600,
    restore: bool = True,
) -> dict[str, Any]:
    """Nudge setpoint ±1 °C and wait for RX echo. Does not change mode."""
    from verify.serial_link import open_link

    result: dict[str, Any] = {"ok": False, "sent": None, "before": None, "after": None}
    link = open_link(serial=serial, baud=baud, host=host, port=port)
    try:
        before = recv_frames(link, 12.0, want=1)
        if not before:
            result["error"] = "no RX frame before TX"
            return result
        fr0 = parse_frame(before[-1])
        result["before"] = fr0.to_dict()
        if fr0.target_c is None:
            result["error"] = "no D30 on RX"
            return result
        cur = int(fr0.target_c)
        new_t = 60 if cur >= 61 else 62
        tx = patch_frame(before[-1], target=new_t)
        result["sent_hex"] = tx.hex(" ")
        result["sent_target"] = new_t
        time.sleep(0.4)
        _write(link, tx)
        after = recv_frames(link, 16.0, want=4)
        after = [f for f in after if f != tx]
        parsed = [parse_frame(x) for x in after]
        result["after"] = [p.to_dict() for p in parsed]
        result["ok"] = any(p.target_c == new_t for p in parsed)
        result["echoes"] = sum(1 for p in parsed if p.target_c == new_t)
        if restore:
            time.sleep(0.4)
            src = after[-1] if after else before[-1]
            back = patch_frame(src, target=cur)
            _write(link, back)
            rest = recv_frames(link, 12.0, want=3)
            rest = [f for f in rest if f != back]
            rparsed = [parse_frame(x) for x in rest]
            result["restore_ok"] = any(p.target_c == cur for p in rparsed)
            result["restored"] = [p.to_dict() for p in rparsed]
        return result
    finally:
        link.close()


def _write(link: Any, data: bytes) -> None:
    if hasattr(link, "write"):
        link.write(data)
    else:
        link.sendall(data)


def send_control(
    host: str | None = None,
    port: int = 502,
    *,
    serial: str | None = None,
    baud: int = 600,
    target: int | None = None,
    mode: str | None = None,
    timer: bool | None = None,
    wait_s: float = 14.0,
) -> dict[str, Any]:
    from verify.serial_link import open_link

    link = open_link(serial=serial, baud=baud, host=host, port=port)
    try:
        rx = recv_frames(link, 12.0, want=1)
        if not rx:
            return {"ok": False, "error": "no RX frame"}
        tx = patch_frame(rx[-1], target=target, mode=mode, timer=timer)
        time.sleep(0.4)
        _write(link, tx)
        after = recv_frames(link, wait_s, want=4)
        after = [f for f in after if f != tx]
        parsed = [parse_frame(x) for x in after]
        out: dict[str, Any] = {
            "ok": False,
            "sent": parse_frame(tx).to_dict(),
            "after": [p.to_dict() for p in parsed],
        }
        if not parsed:
            out["error"] = "no RX after TX"
            return out
        ok = True
        if target is not None:
            ok = ok and any(p.target_c == int(target) for p in parsed)
        if mode is not None:
            ok = ok and any(p.mode == mode for p in parsed)
        if timer is not None:
            ok = ok and any(bool(p.timer) == bool(timer) for p in parsed)
        out["ok"] = ok
        out["last"] = parsed[-1].to_dict()
        return out
    finally:
        link.close()
