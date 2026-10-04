"""Read-only water heater entity.

Supported features stay empty. Setpoint and mode services are not registered,
and the methods below refuse if something calls them anyway. Sending a guessed
CN30 frame on this board has latched E2 and has never moved the setpoint.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.water_heater import (
    STATE_ECO,
    STATE_ELECTRIC,
    STATE_OFF,
    STATE_PERFORMANCE,
    WaterHeaterEntity,
    WaterHeaterEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import Cn30Coordinator
from .entity import Cn30Entity
from .protocol import MODE_LABELS

_OPERATION = {
    "off": STATE_OFF,
    "eco": STATE_ECO,
    "performance": STATE_PERFORMANCE,
    "electric": STATE_ELECTRIC,
}

_READ_ONLY = (
    "This main board has no accepted CN30 command. "
    "The red wire-controller link is read-only."
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: Cn30Coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([MideaCn30WaterHeater(coordinator)])


class MideaCn30WaterHeater(Cn30Entity, WaterHeaterEntity):
    """Status of the tank. Controls are omitted on purpose."""

    _attr_name = None
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_precision = 0.5
    _attr_supported_features = WaterHeaterEntityFeature(0)
    _attr_operation_list = [STATE_OFF, STATE_ECO, STATE_PERFORMANCE, STATE_ELECTRIC]
    _attr_unique_id: str

    def __init__(self, coordinator: Cn30Coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}-water-heater"

    @property
    def current_operation(self) -> str | None:
        frame = self.reading.frame if self.reading else None
        if frame is None or frame.mode is None:
            return None
        if not frame.power:
            return STATE_OFF
        return _OPERATION.get(frame.mode)

    @property
    def current_temperature(self) -> float | None:
        frame = self.reading.frame if self.reading else None
        return None if frame is None else frame.t5c_c

    @property
    def target_temperature(self) -> float | None:
        frame = self.reading.frame if self.reading else None
        if frame is None or frame.target_c is None:
            return None
        return float(frame.target_c)

    @property
    def min_temp(self) -> float:
        frame = self.reading.frame if self.reading else None
        if frame is None or frame.mode_min_c is None:
            return 60.0
        return float(frame.mode_min_c)

    @property
    def max_temp(self) -> float:
        frame = self.reading.frame if self.reading else None
        if frame is None or frame.mode_max_c is None:
            return 70.0
        return float(frame.mode_max_c)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        frame = self.reading.frame if self.reading else None
        if frame is None or frame.mode_raw is None:
            return {"protocol": "cn30", "control": "read_only"}
        d6, d7 = frame.mode_raw
        return {
            "protocol": "cn30",
            "control": "read_only",
            "mode_label": MODE_LABELS.get(frame.mode or "", frame.mode),
            "mode_raw": f"{d6:02X}:{d7:02X}",
            "timer": frame.timer,
            "eheater_flag": frame.eheater_flag,
            "mode_min_c": frame.mode_min_c,
            "mode_max_c": frame.mode_max_c,
            "tp_c": frame.tp_c,
            "eev": frame.eev,
        }

    async def async_set_temperature(self, **kwargs: Any) -> None:
        raise HomeAssistantError(_READ_ONLY)

    async def async_set_operation_mode(self, operation_mode: str) -> None:
        raise HomeAssistantError(_READ_ONLY)
