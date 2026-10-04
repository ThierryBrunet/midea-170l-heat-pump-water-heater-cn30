"""Shared device info for CN30 entities."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, HW_VERSION, MANUFACTURER, MODEL
from .coordinator import Cn30Coordinator, Cn30Reading


class Cn30Entity(CoordinatorEntity[Cn30Coordinator]):
    """Base entity. Availability follows the last checksum-valid frame."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: Cn30Coordinator) -> None:
        super().__init__(coordinator)
        entry = coordinator.entry
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer=MANUFACTURER,
            model=MODEL,
            hw_version=HW_VERSION,
            configuration_url=f"http://{coordinator.host}/",
        )

    @property
    def available(self) -> bool:
        return self.coordinator.fresh

    @property
    def reading(self) -> Cn30Reading | None:
        return self.coordinator.reading
