"""Flags painted from the CN30 status frame."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import Cn30Coordinator
from .entity import Cn30Entity
from .protocol import Cn30Frame


@dataclass(frozen=True, kw_only=True)
class Cn30BinaryDescription(BinarySensorEntityDescription):
    is_on_fn: Callable[[Cn30Frame], bool | None]


BINARY_SENSORS: tuple[Cn30BinaryDescription, ...] = (
    Cn30BinaryDescription(
        key="element",
        translation_key="element",
        name="Element",
        device_class=BinarySensorDeviceClass.HEAT,
        icon="mdi:heating-coil",
        is_on_fn=lambda frame: None
        if frame.eheater_flag is None
        else frame.eheater_flag != 0,
    ),
    Cn30BinaryDescription(
        key="timer",
        translation_key="timer",
        name="Timer",
        icon="mdi:timer-outline",
        is_on_fn=lambda frame: frame.timer,
    ),
    Cn30BinaryDescription(
        key="keypad_lock",
        translation_key="keypad_lock",
        name="Keypad lock bit",
        device_class=BinarySensorDeviceClass.LOCK,
        entity_category=EntityCategory.DIAGNOSTIC,
        is_on_fn=lambda frame: frame.lock_bit,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: Cn30Coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        MideaCn30BinarySensor(coordinator, desc) for desc in BINARY_SENSORS
    )


class MideaCn30BinarySensor(Cn30Entity, BinarySensorEntity):
    entity_description: Cn30BinaryDescription

    def __init__(
        self,
        coordinator: Cn30Coordinator,
        description: Cn30BinaryDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.entry.entry_id}-{description.key}"

    @property
    def is_on(self) -> bool | None:
        reading = self.reading
        if reading is None:
            return None
        return self.entity_description.is_on_fn(reading.frame)
