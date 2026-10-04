"""CN30 status frames from the residential HP170 red wire-controller header.

This is the 33-byte broadcast on PCB silk ``R-ZT15/190(C)-A[AC128]``
(Freescale MC9S08AC128). It is not Modbus, and it is not the register map
used by 0xAHA/Midea-Heat-Pump-HA (9600 8N1, slave 1, PQE/CN21-style boards).

Live line: 600 8N1, no parity, one checksum-valid frame about every 4 s.
Header ``FE AA 00 00 FF``, terminator ``55``.
Checksum at index 31: ``(168 - sum(bytes 0..30)) & 0xFF``.
Temperature scale for T5/T3/T4: ``(raw * 0.5) - 15``.
TP (discharge air) is byte 22 as an unscaled integer °C (same as Modbus 105).
EEV opening is byte 20 as an integer. The 2026-10-04 09:22 panel query
matched this map: T5L 29, T4 26, T3 23, Tp 25, EEV 60. TH suction has no
separate byte; at that snapshot it equalled T3 with the compressor off.

Keep ``parse_frame`` aligned with ``verify/cn30.py``. This module only decodes.
Nothing here builds or sends a bus frame.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SOF = bytes((0xFE, 0xAA, 0x00, 0x00, 0xFF))
EOF = 0x55
FRAME_LEN = 33
FRAME_LEN_SHORT = 30

# Low nibble of byte 5 (D6) is the live mode. 0x10 on byte 5 or 6 is the timer.
MODE_BY_D6 = {
    0x01: "eco",
    0x02: "performance",
    0x04: "off",
    0x09: "eco",
    0x0A: "performance",
    0x0C: "electric",
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


def checksum33(data: bytes) -> int:
    """(168 - sum of bytes 0..30) mod 256."""
    return (168 - sum(data[:31])) & 0xFF


def checksum30(data: bytes) -> int:
    return (144 - sum(data[:29])) & 0xFF


def decode_mode(d6: int) -> str | None:
    """Exact byte first, then the low nibble.

    ``14 14`` is E-Heater plus timer (2026-10-04 glass), not Off. Looking up
    the low nibble ``0x04`` first folded that pair into Off.
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


def extract_frames(buf: bytes) -> tuple[list[bytes], bytes]:
    """Split a byte stream into candidate frames. Return (frames, remainder)."""
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
    lock_bit: bool | None
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
            "lock_bit": self.lock_bit,
            "notes": self.notes,
        }


def parse_frame(raw: bytes) -> Cn30Frame:
    hx = raw.hex(" ")
    notes: list[str] = []
    lock_bit: bool | None = None
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
        lock_bit = bool(raw[11] & 0x20)
    elif len(raw) == FRAME_LEN_SHORT:
        ok = raw[28] == checksum30(raw) and raw[29] == EOF
        if not ok:
            notes.append(f"cs30 got {raw[28]:02X} expect {checksum30(raw):02X}")
        notes.append("30-byte shortened frame")
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
            lock_bit=None,
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
        lock_bit=lock_bit,
        hex=hx,
        notes=notes,
    )


def feed(buf: bytes, chunk: bytes, *, limit: int = 4096) -> tuple[list[Cn30Frame], bytes]:
    """Append one TCP/UART chunk and pull every complete frame out of the buffer."""
    if chunk:
        buf += chunk
    raws, buf = extract_frames(buf)
    if len(buf) > limit:
        buf = buf[-64:]
    return [parse_frame(raw) for raw in raws], buf
