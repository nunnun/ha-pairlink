"""PairLink diagnostic sensors."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import (
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .models import PairLinkConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PairLinkConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up diagnostic sensors for one switch."""
    async_add_entities([PairLinkSignalStrengthSensor(entry)])


class PairLinkSignalStrengthSensor(SensorEntity):
    """Expose the latest advertisement RSSI for one PairLink switch."""

    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = SIGNAL_STRENGTH_DECIBELS_MILLIWATT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_translation_key = "signal_strength"

    def __init__(self, entry: PairLinkConfigEntry) -> None:
        """Initialize the entity from entry-owned runtime state."""
        self._session = entry.runtime_data
        credentials = self._session.credentials
        remote_id = credentials.remote_id.hex()
        self._attr_unique_id = f"{remote_id}_signal_strength"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, remote_id)},
            connections={(CONNECTION_BLUETOOTH, credentials.address)},
            name=entry.title,
            manufacturer="PairLink",
            model="PairLink-compatible switch",
        )

    @property
    def native_value(self) -> int | None:
        """Return the latest advertisement signal strength."""
        return self._session.diagnostics.rssi

    @property
    def available(self) -> bool:
        """Remain unavailable until the first advertisement is received."""
        return self.native_value is not None

    async def async_added_to_hass(self) -> None:
        """Subscribe only while the entity belongs to Home Assistant."""
        await super().async_added_to_hass()
        self.async_on_remove(self._session.subscribe_state(self._handle_update))

    @callback
    def _handle_update(self) -> None:
        """Write signal-strength changes."""
        self.async_write_ha_state()
