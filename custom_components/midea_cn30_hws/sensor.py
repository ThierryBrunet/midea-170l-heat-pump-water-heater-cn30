"""Temperatures and limits taken from the CN30 status frame."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import Cn30Coordinator, Cn30Reading
from .entity import Cn30Entity
from .protocol import Cn30Frame

_TEMP = {
    "device_class": SensorDeviceClass.TEMPERATURE,
    "native_unit_of_measurement": UnitOfTemperature.CELSIUS,
    "state_class": SensorStateClass.MEASUREMENT,
    "suggested_display_precision": 1,
}


@dataclass(frozen=True, kw_only=True)
class Cn30SensorDescription(SensorEntityDescription):
    value_fn: Callable[[Cn30Reading], datetime | float | int | str | None]


def _mode_code(frame: Cn30Frame) -> str | None:
    if frame.mode_raw is None:
        return None
    d6, d7 = frame.mode_raw
    return f"{d6:02X}:{d7:02X}"


SENSORS: tuple[Cn30SensorDescription, ...] = (
    Cn30SensorDescription(
        key="t5c",
        translation_key="t5c",
        name="T5L Tank",
        value_fn=lambda reading: reading.frame.t5c_c,
        **_TEMP,
    ),
    Cn30SensorDescription(
        key="t3",
        translation_key="t3",
        name="T3 Evaporator",
        value_fn=lambda reading: reading.frame.t3_c,
        **_TEMP,
    ),
    Cn30SensorDescription(
        key="t4",
        translation_key="t4",
        name="T4 Ambient",
        value_fn=lambda reading: reading.frame.t4_c,
        **_TEMP,
    ),
    Cn30SensorDescription(
        key="tp",
        translation_key="tp",
        name="TP Discharge Air",
        suggested_display_precision=0,
        value_fn=lambda reading: reading.frame.tp_c,
        **{k: v for k, v in _TEMP.items() if k != "suggested_display_precision"},
    ),
    Cn30SensorDescription(
        key="eev",
        translation_key="eev",
        name="EEV",
        icon="mdi:valve",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda reading: reading.frame.eev,
    ),
    Cn30SensorDescription(
        key="th",
        translation_key="th",
        name="TH Suction",
        entity_registry_enabled_default=False,
        value_fn=lambda reading: reading.frame.th_c,
        **_TEMP,
    ),
    Cn30SensorDescription(
        key="target",
        translation_key="target",
        name="Target temperature",
        suggested_display_precision=0,
        value_fn=lambda reading: reading.frame.target_c,
        **{k: v for k, v in _TEMP.items() if k != "suggested_display_precision"},
    ),
    Cn30SensorDescription(
        key="mode_max",
        translation_key="mode_max",
        name="Mode maximum",
        suggested_display_precision=0,
        entity_registry_enabled_default=False,
        value_fn=lambda reading: reading.frame.mode_max_c,
        **{k: v for k, v in _TEMP.items() if k != "suggested_display_precision"},
    ),
    Cn30SensorDescription(
        key="mode_min",
        translation_key="mode_min",
        name="Mode minimum",
        suggested_display_precision=0,
        entity_registry_enabled_default=False,
        value_fn=lambda reading: reading.frame.mode_min_c,
        **{k: v for k, v in _TEMP.items() if k != "suggested_display_precision"},
    ),
    Cn30SensorDescription(
        key="mode_code",
        translation_key="mode_code",
        name="Mode code",
        icon="mdi:format-list-bulleted-type",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda reading: _mode_code(reading.frame),
    ),
    Cn30SensorDescription(
        key="last_frame",
        translation_key="last_frame",
        name="Last frame",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda reading: datetime.fromtimestamp(reading.wall, timezone.utc),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: Cn30Coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(MideaCn30Sensor(coordinator, desc) for desc in SENSORS)


class MideaCn30Sensor(Cn30Entity, SensorEntity):
    entity_description: Cn30SensorDescription

    def __init__(
        self,
        coordinator: Cn30Coordinator,
        description: Cn30SensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.entry.entry_id}-{description.key}"

    @property
    def native_value(self) -> datetime | float | int | str | None:
        reading = self.reading
        if reading is None:
            return None
        return self.entity_description.value_fn(reading)
