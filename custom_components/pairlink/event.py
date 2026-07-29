"""PairLink button Event Entity."""

from __future__ import annotations

from homeassistant.components.event import EventDeviceClass, EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import COMMAND_TO_EVENT_TYPE, DOMAIN, EVENT_TYPES
from .models import PairLinkConfigEntry, RemoteEvent


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PairLinkConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the event entity for one switch."""
    async_add_entities([PairLinkButtonEvent(entry)])


class PairLinkButtonEvent(EventEntity):
    """Expose physical PairLink ON/OFF operations."""

    _attr_device_class = EventDeviceClass.BUTTON
    _attr_event_types = EVENT_TYPES
    _attr_has_entity_name = True
    _attr_translation_key = "button"

    def __init__(self, entry: PairLinkConfigEntry) -> None:
        """Initialize the entity from entry-owned runtime state."""
        self._session = entry.runtime_data
        credentials = self._session.credentials
        remote_id = credentials.remote_id.hex()
        self._attr_unique_id = f"{remote_id}_button"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, remote_id)},
            connections={(CONNECTION_BLUETOOTH, credentials.address)},
            name=entry.title,
            manufacturer="PairLink",
            model="PairLink-compatible switch",
        )

    @property
    def available(self) -> bool:
        """Return session availability from memory only."""
        return self._session.available

    async def async_added_to_hass(self) -> None:
        """Subscribe only while the entity belongs to Home Assistant."""
        await super().async_added_to_hass()
        self.async_on_remove(self._session.subscribe_events(self._handle_event))
        self.async_on_remove(self._session.subscribe_state(self._handle_state))

    @callback
    def _handle_event(self, event: RemoteEvent, repeat_count: int) -> None:
        """Publish one unique physical button operation."""
        event_type = COMMAND_TO_EVENT_TYPE[event.command]
        self._trigger_event(
            event_type,
            {
                "channel": event.remote_channel,
                "command": event.command,
                "command_hex": f"0x{event.command:02x}",
                "extra": event.extra.hex(),
                "repeat_count": repeat_count,
            },
        )
        self.async_write_ha_state()

    @callback
    def _handle_state(self) -> None:
        """Write availability changes."""
        self.async_write_ha_state()
