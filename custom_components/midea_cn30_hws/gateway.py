"""Read-only TCP client for an Elfin EW-11 transparent bridge.

The socket is a raw tunnel to CN30 (UART protocol NONE). This module never
writes. A byte on the wire can be taken as a second wire controller and has
latched keypad fault E2 on this board.
"""

from __future__ import annotations

import asyncio
import contextlib
import time

from .protocol import FRAME_LEN, SOF, Cn30Frame, feed

CONNECT_TIMEOUT = 10.0


class GatewayError(Exception):
    """Probe failed. ``code`` matches a config-flow error key."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


async def open_reader(
    host: str, port: int, *, timeout: float = CONNECT_TIMEOUT
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Open the bridge. The returned writer is for closing the socket only."""
    try:
        return await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=timeout,
        )
    except (TimeoutError, OSError) as err:
        raise GatewayError("cannot_connect") from err


async def probe_frame(
    host: str,
    port: int,
    *,
    timeout: float = 12.0,
) -> Cn30Frame:
    """Return one checksum-valid 33-byte frame. Send nothing."""
    reader, writer = await open_reader(host, port)
    buf = b""
    seen = 0
    try:
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                chunk = await asyncio.wait_for(reader.read(512), timeout=remaining)
            except TimeoutError:
                break
            if not chunk:
                break
            seen += len(chunk)
            frames, buf = feed(buf, chunk)
            for frame in frames:
                if (
                    frame.checksum_ok
                    and len(frame.raw) == FRAME_LEN
                    and frame.raw[:5] == SOF
                ):
                    return frame
        if seen:
            raise GatewayError("no_frame")
        raise GatewayError("timeout")
    finally:
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
