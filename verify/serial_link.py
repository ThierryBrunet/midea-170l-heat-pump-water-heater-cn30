"""Byte stream for CN30: EW-11 TCP or USB-UART + MAX485 (same 600 8N1 bytes).

Cheap TTL-to-RS485 modules auto-DE from TX. They often echo TX on RX;
callers should drop a copy of the just-sent frame from the RX buffer.
"""

from __future__ import annotations

import socket
import time
from typing import Protocol

DEFAULT_SERIAL = "/dev/ttyUSB0"
DEFAULT_BAUD = 600


class ByteLink(Protocol):
    def read(self, n: int = 512) -> bytes: ...
    def write(self, data: bytes) -> None: ...
    def close(self) -> None: ...


class TcpLink:
    def __init__(self, host: str, port: int = 502, timeout: float = 8.0) -> None:
        self._s = socket.create_connection((host, port), timeout=timeout)
        self._s.settimeout(1.0)

    def read(self, n: int = 512) -> bytes:
        try:
            return self._s.recv(n)
        except socket.timeout:
            return b""

    def write(self, data: bytes) -> None:
        self._s.sendall(data)

    def close(self) -> None:
        try:
            self._s.close()
        except OSError:
            pass


class SerialLink:
    def __init__(self, port: str = DEFAULT_SERIAL, baud: int = DEFAULT_BAUD, timeout: float = 1.0) -> None:
        import serial  # type: ignore

        self._s = serial.Serial(
            port=port,
            baudrate=baud,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=timeout,
            write_timeout=2.0,
            xonxoff=False,
            rtscts=False,
            dsrdtr=False,
        )
        # MAX485 auto-DE: idle TX high. Small settle after open.
        time.sleep(0.05)
        self._s.reset_input_buffer()

    def read(self, n: int = 512) -> bytes:
        return self._s.read(n)

    def write(self, data: bytes) -> None:
        self._s.write(data)
        self._s.flush()
        # 33 bytes @ 600 8N1 ≈ 550 ms on the wire; DE must stay until last stop bit.
        time.sleep(len(data) * 11 / max(self._s.baudrate, 1) + 0.02)

    def close(self) -> None:
        try:
            self._s.close()
        except OSError:
            pass


def open_link(
    *,
    serial: str | None = None,
    baud: int = DEFAULT_BAUD,
    host: str | None = None,
    port: int = 502,
) -> ByteLink:
    if serial:
        return SerialLink(serial, baud)
    if not host:
        raise ValueError("serial= or host= required")
    return TcpLink(host, port)
