"""Hold one read-only TCP session and publish each valid CN30 frame."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
import logging
import time

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import CONF_HOST, CONF_PORT, CONF_STALE_SECONDS, DEFAULT_STALE_SECONDS, DOMAIN
from .gateway import CONNECT_TIMEOUT, GatewayError, open_reader
from .protocol import FRAME_LEN, SOF, Cn30Frame, feed

_LOGGER = logging.getLogger(__name__)

# How often to re-check a quiet bus so entities can go unavailable.
_STALE_TICK = 5.0
_BACKOFF_MAX = 60.0


@dataclass(frozen=True)
class Cn30Reading:
    """One accepted status frame and when it arrived."""

    frame: Cn30Frame
    monotonic: float
    wall: float


class Cn30Coordinator(DataUpdateCoordinator[Cn30Reading]):
    """Push updates from the heater. The socket is never written."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=None,
        )
        self.entry = entry
        self.host: str = entry.data[CONF_HOST]
        self.port: int = int(entry.data[CONF_PORT])
        self.connected = False
        self.last_error: str | None = None
        self._latest: Cn30Reading | None = None
        self._cond = asyncio.Condition()
        self._stop = asyncio.Event()
        self._listen_task: asyncio.Task[None] | None = None
        self._stale_task: asyncio.Task[None] | None = None
        self._marked_stale = False

    @property
    def stale_seconds(self) -> float:
        return float(self.entry.options.get(CONF_STALE_SECONDS, DEFAULT_STALE_SECONDS))

    @property
    def fresh(self) -> bool:
        reading = self._latest
        if reading is None or not reading.frame.checksum_ok:
            return False
        return (time.monotonic() - reading.monotonic) <= self.stale_seconds

    @property
    def reading(self) -> Cn30Reading | None:
        return self._latest

    async def async_start(self) -> None:
        self._stop.clear()
        self._listen_task = asyncio.create_task(self._listen(), name="cn30-listen")
        self._stale_task = asyncio.create_task(self._watch_stale(), name="cn30-stale")

    async def async_shutdown(self) -> None:
        self._stop.set()
        tasks = [task for task in (self._listen_task, self._stale_task) if task]
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._listen_task = None
        self._stale_task = None
        async with self._cond:
            self._cond.notify_all()

    async def _async_update_data(self) -> Cn30Reading:
        if self.fresh and self._latest is not None:
            return self._latest
        try:
            async with self._cond:
                await asyncio.wait_for(self._cond.wait_for(self._is_fresh), timeout=16)
        except TimeoutError as err:
            raise UpdateFailed(
                f"No checksum-valid CN30 frame from {self.host}:{self.port}"
            ) from err
        if self._latest is None:
            raise UpdateFailed("CN30 listener stopped")
        return self._latest

    def _is_fresh(self) -> bool:
        return self.fresh

    async def _watch_stale(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=_STALE_TICK)
                return
            except TimeoutError:
                pass
            if self._latest is not None and not self.fresh:
                if not self._marked_stale:
                    self._marked_stale = True
                    self.async_update_listeners()
            else:
                self._marked_stale = False

    async def _listen(self) -> None:
        backoff = 2.0
        while not self._stop.is_set():
            writer: asyncio.StreamWriter | None = None
            try:
                _reader, writer = await open_reader(
                    self.host, self.port, timeout=CONNECT_TIMEOUT
                )
                self.connected = True
                self.last_error = None
                backoff = 2.0
                _LOGGER.info("CN30 bridge open %s:%s (read only)", self.host, self.port)
                await self._read_loop(_reader)
            except asyncio.CancelledError:
                raise
            except GatewayError as err:
                self.connected = False
                self.last_error = err.code
                _LOGGER.warning("CN30 bridge %s:%s %s", self.host, self.port, err.code)
            except Exception as err:
                self.connected = False
                self.last_error = str(err)
                _LOGGER.warning("CN30 bridge %s:%s closed: %s", self.host, self.port, err)
            finally:
                self.connected = False
                if writer is not None:
                    writer.close()
                    with contextlib.suppress(Exception):
                        await writer.wait_closed()
            if self._stop.is_set():
                break
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _BACKOFF_MAX)

    async def _read_loop(self, reader: asyncio.StreamReader) -> None:
        buf = b""
        while not self._stop.is_set():
            try:
                chunk = await asyncio.wait_for(reader.read(512), timeout=30)
            except TimeoutError:
                continue
            if not chunk:
                raise ConnectionError("bridge closed the socket")
            frames, buf = feed(buf, chunk)
            accepted: Cn30Frame | None = None
            for frame in frames:
                if (
                    frame.checksum_ok
                    and len(frame.raw) == FRAME_LEN
                    and frame.raw[:5] == SOF
                ):
                    accepted = frame
                elif frame.checksum_ok:
                    _LOGGER.debug("Ignoring short CN30 frame: %s", frame.hex)
                else:
                    _LOGGER.debug("Bad CN30 frame: %s", frame.notes)
            if accepted is None:
                continue
            reading = Cn30Reading(
                frame=accepted,
                monotonic=time.monotonic(),
                wall=time.time(),
            )
            async with self._cond:
                self._latest = reading
                self._marked_stale = False
                self._cond.notify_all()
            self.async_set_updated_data(reading)
            _LOGGER.debug(
                "CN30 %s target=%s t5c=%s",
                accepted.mode,
                accepted.target_c,
                accepted.t5c_c,
            )
