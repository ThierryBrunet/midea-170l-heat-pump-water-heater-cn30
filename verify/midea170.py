"""Midea 170L (HP170 / RSJ-15/190RDN3-C) Modbus TCP client.

Matches 0xAHA/Midea-Heat-Pump-HA profile midea_170l.json.
Default path: EW-11A at WaterHeater.lan:502, unit 1.
"""

from __future__ import annotations

import socket
import time
from dataclasses import dataclass, field
from typing import Any

from pymodbus.client import ModbusTcpClient
from pymodbus.exceptions import ModbusException

DEFAULT_HOST = "192.168.31.219"
DEFAULT_PORT = 502
DEFAULT_UNIT = 1
DEFAULT_TIMEOUT = 3.0

# Holding registers (function 3 / 6) — same as HA profile
REG_POWER = 0
REG_MODE = 1
REG_TARGET = 2
REG_STERILIZE = 3
REG_T5U = 101  # tank top
REG_T5L = 102  # tank bottom / current temp
REG_T3 = 103  # condenser
REG_T4 = 104  # outdoor
REG_TP = 105  # exhaust (raw °C)
REG_TH = 106  # suction

MODE_ECO = 1
MODE_PERFORMANCE = 2  # hybrid / heat-pump boost
MODE_ELECTRIC = 4
MODE_NAMES = {
    MODE_ECO: "eco",
    MODE_PERFORMANCE: "performance",
    MODE_ELECTRIC: "electric",
}
MODE_LABELS = {
    "eco": "Eco",
    "performance": "Hybrid",
    "electric": "E-Heater",
    "off": "Off",
}
NAME_TO_MODE = {v: k for k, v in MODE_NAMES.items()}

SENSOR_SCALE = 0.5
SENSOR_OFFSET = -15.0
TARGET_MIN = 60
TARGET_MAX = 70


def scale_sensor(raw: int) -> float:
    return (raw * SENSOR_SCALE) + SENSOR_OFFSET


def plausible_c(c: float) -> bool:
    return -25.0 <= c <= 95.0


@dataclass
class StepResult:
    name: str
    ok: bool
    detail: str
    hint: str = ""


@dataclass
class Status:
    connected: bool
    host: str
    port: int
    unit: int
    elapsed_ms: float
    power: bool | None = None
    mode_raw: int | None = None
    mode: str | None = None
    target_c: float | None = None
    sterilize: bool | None = None
    t5u: float | None = None
    t5l: float | None = None
    t3: float | None = None
    t4: float | None = None
    tp: float | None = None
    th: float | None = None
    raw: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "connected": self.connected,
            "host": self.host,
            "port": self.port,
            "unit": self.unit,
            "elapsed_ms": round(self.elapsed_ms, 1),
            "power": self.power,
            "mode_raw": self.mode_raw,
            "mode": self.mode,
            "operation": (self.mode if self.power else "off") if self.power is not None else None,
            "target_c": self.target_c,
            "sterilize": self.sterilize,
            "temps": {
                "t5u": self.t5u,
                "t5l": self.t5l,
                "t3": self.t3,
                "t4": self.t4,
                "tp": self.tp,
                "th": self.th,
            },
            "raw": self.raw,
            "errors": self.errors,
        }


class Midea170:
    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        unit: int = DEFAULT_UNIT,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.host = host
        self.port = port
        self.unit = unit
        self.timeout = timeout
        self._client: ModbusTcpClient | None = None

    def tcp_probe(self) -> tuple[bool, str, float]:
        t0 = time.perf_counter()
        try:
            with socket.create_connection((self.host, self.port), timeout=self.timeout):
                return True, "TCP open", (time.perf_counter() - t0) * 1000
        except OSError as exc:
            return False, str(exc), (time.perf_counter() - t0) * 1000

    def connect(self) -> None:
        if self._client and self._client.connected:
            return
        self._client = ModbusTcpClient(
            host=self.host, port=self.port, timeout=self.timeout, retries=0
        )
        if not self._client.connect():
            raise ConnectionError(f"Modbus TCP connect failed {self.host}:{self.port}")

    def close(self) -> None:
        if self._client:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None

    def _kw(self) -> dict[str, int]:
        return {"device_id": self.unit}

    def read_holding(self, address: int, count: int = 1) -> list[int]:
        self.connect()
        assert self._client is not None
        try:
            rr = self._client.read_holding_registers(address=address, count=count, **self._kw())
        except TypeError:
            rr = self._client.read_holding_registers(address=address, count=count, slave=self.unit)
        if rr is None or rr.isError():
            raise ModbusException(str(rr))
        return list(rr.registers)

    def write_holding(self, address: int, value: int) -> None:
        self.connect()
        assert self._client is not None
        try:
            wr = self._client.write_register(address=address, value=int(value), **self._kw())
        except TypeError:
            wr = self._client.write_register(address=address, value=int(value), slave=self.unit)
        if wr is None or wr.isError():
            raise ModbusException(str(wr))

    def read_one(self, address: int) -> int:
        return self.read_holding(address, 1)[0]

    def status(self) -> Status:
        t0 = time.perf_counter()
        st = Status(connected=False, host=self.host, port=self.port, unit=self.unit, elapsed_ms=0)
        ok, detail, _ = self.tcp_probe()
        if not ok:
            st.errors.append(f"tcp: {detail}")
            st.elapsed_ms = (time.perf_counter() - t0) * 1000
            return st
        try:
            self.connect()
            st.connected = True
            raw0 = self.read_one(REG_POWER)
            raw1 = self.read_one(REG_MODE)
            raw2 = self.read_one(REG_TARGET)
            st.raw["power"] = raw0
            st.raw["mode"] = raw1
            st.raw["target"] = raw2
            st.power = bool(raw0)
            st.mode_raw = raw1
            st.mode = MODE_NAMES.get(raw1, f"unknown:{raw1}")
            st.target_c = float(raw2)
            try:
                raw3 = self.read_one(REG_STERILIZE)
                st.raw["sterilize"] = raw3
                st.sterilize = bool(raw3)
            except Exception as exc:
                st.errors.append(f"sterilize(3): {exc}")
            sensors = [
                ("t5u", REG_T5U, True),
                ("t5l", REG_T5L, True),
                ("t3", REG_T3, True),
                ("t4", REG_T4, True),
                ("tp", REG_TP, False),
                ("th", REG_TH, True),
            ]
            for name, addr, scaled in sensors:
                try:
                    raw = self.read_one(addr)
                    st.raw[name] = raw
                    val = scale_sensor(raw) if scaled else float(raw)
                    setattr(st, name, val)
                except Exception as exc:
                    st.errors.append(f"{name}({addr}): {exc}")
        except Exception as exc:
            st.errors.append(str(exc))
        st.elapsed_ms = (time.perf_counter() - t0) * 1000
        return st

    def set_power(self, on: bool) -> None:
        self.write_holding(REG_POWER, 1 if on else 0)

    def set_mode(self, name: str) -> None:
        key = name.strip().lower()
        if key not in NAME_TO_MODE:
            raise ValueError(f"mode must be one of {list(NAME_TO_MODE)}")
        self.write_holding(REG_MODE, NAME_TO_MODE[key])
        self.write_holding(REG_POWER, 1)

    def set_target(self, celsius: int) -> None:
        c = int(celsius)
        if c < TARGET_MIN or c > TARGET_MAX:
            raise ValueError(f"target {c} outside {TARGET_MIN}-{TARGET_MAX} °C")
        self.write_holding(REG_TARGET, c)

    def set_sterilize(self, on: bool) -> None:
        self.write_holding(REG_STERILIZE, 1 if on else 0)

    def selftest(self, writes: bool = False) -> list[StepResult]:
        steps: list[StepResult] = []
        ok, detail, ms = self.tcp_probe()
        steps.append(
            StepResult(
                "tcp_502",
                ok,
                f"{self.host}:{self.port} {detail} ({ms:.0f} ms)",
                "" if ok else "EW-11A down, wrong IP, or socket not TCP-SERVER 502.",
            )
        )
        if not ok:
            steps.append(
                StepResult(
                    "rs485",
                    False,
                    "skipped",
                    "Fix TCP first. Then check A/B, GND, 9600 8N1, unit 1.",
                )
            )
            return steps

        try:
            raw = self.read_one(REG_POWER)
            steps.append(
                StepResult(
                    "read_power",
                    True,
                    f"holding[0]={raw} ({'ON' if raw else 'OFF'})",
                    "",
                )
            )
        except Exception as exc:
            steps.append(
                StepResult(
                    "read_power",
                    False,
                    str(exc),
                    "TCP works (EW-11A) but no Modbus slave. Check RS485 A/B (swap if needed), "
                    "GND, 5–18 V on EW-11A, heater powered, UART 9600 half-duplex Modbus, unit 1.",
                )
            )
            return steps

        for name, addr in (("mode", REG_MODE), ("target", REG_TARGET)):
            try:
                v = self.read_one(addr)
                extra = MODE_NAMES.get(v, "") if name == "mode" else f"{v} °C"
                steps.append(StepResult(f"read_{name}", True, f"holding[{addr}]={v} {extra}".strip()))
            except Exception as exc:
                steps.append(StepResult(f"read_{name}", False, str(exc), "Control register missing."))

        try:
            self.read_one(REG_STERILIZE)
            steps.append(StepResult("read_sterilize", True, "holding[3] ok"))
        except Exception as exc:
            steps.append(
                StepResult(
                    "read_sterilize",
                    False,
                    str(exc),
                    "Optional on some boards; HA still uses register 3 when present.",
                )
            )

        bad_temps = 0
        for name, addr, scaled in (
            ("t5u", REG_T5U, True),
            ("t5l", REG_T5L, True),
            ("t3", REG_T3, True),
            ("t4", REG_T4, True),
            ("tp", REG_TP, False),
            ("th", REG_TH, True),
        ):
            try:
                raw = self.read_one(addr)
                c = scale_sensor(raw) if scaled else float(raw)
                ok_t = plausible_c(c)
                if not ok_t:
                    bad_temps += 1
                steps.append(
                    StepResult(
                        f"temp_{name}",
                        ok_t,
                        f"holding[{addr}] raw={raw} → {c:.1f} °C",
                        "" if ok_t else "Out of range: open sensor, A/B noise, or wrong scale.",
                    )
                )
            except Exception as exc:
                bad_temps += 1
                steps.append(StepResult(f"temp_{name}", False, str(exc), "Sensor block 101–106 unread."))

        if writes:
            snap: dict[str, int] = {}
            try:
                for addr in (REG_POWER, REG_MODE, REG_TARGET):
                    snap[addr] = self.read_one(addr)
                self.write_holding(REG_TARGET, snap[REG_TARGET])
                back = self.read_one(REG_TARGET)
                steps.append(
                    StepResult(
                        "write_target_roundtrip",
                        back == snap[REG_TARGET],
                        f"wrote {snap[REG_TARGET]}, read {back}",
                    )
                )
                self.write_holding(REG_POWER, snap[REG_POWER])
                backp = self.read_one(REG_POWER)
                steps.append(
                    StepResult(
                        "write_power_roundtrip",
                        backp == snap[REG_POWER],
                        f"wrote {snap[REG_POWER]}, read {backp}",
                    )
                )
            except Exception as exc:
                steps.append(
                    StepResult(
                        "writes",
                        False,
                        str(exc),
                        "Reads work but writes fail: unit read-only, or EW-11 UART protocol not Modbus.",
                    )
                )
        else:
            steps.append(
                StepResult(
                    "writes",
                    True,
                    "skipped (read-only). Pass --writes for target/power round-trip.",
                )
            )

        return steps
