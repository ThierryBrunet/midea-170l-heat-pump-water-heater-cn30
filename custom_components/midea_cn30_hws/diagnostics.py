"""Downloadable diagnostics. No secrets, no bus writes."""

from __future__ import annotations

import time
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_STALE_SECONDS, DEFAULT_STALE_SECONDS, DOMAIN
from .coordinator import Cn30Coordinator

_REDACT: set[str] = set()


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    coordinator: Cn30Coordinator = hass.data[DOMAIN][entry.entry_id]
    reading = coordinator.reading
    age = None
    if reading is not None:
        age = round(time.monotonic() - reading.monotonic, 1)
    payload = {
        "title": entry.title,
        "host": coordinator.host,
        "port": coordinator.port,
        "stale_seconds": entry.options.get(CONF_STALE_SECONDS, DEFAULT_STALE_SECONDS),
        "connected": coordinator.connected,
        "fresh": coordinator.fresh,
        "last_error": coordinator.last_error,
        "frame_age_s": age,
        "control": "read_only",
        "bus": "CN30 600 8N1 status broadcast",
        "frame": reading.frame.to_dict() if reading else None,
    }
    return async_redact_data(payload, _REDACT)
