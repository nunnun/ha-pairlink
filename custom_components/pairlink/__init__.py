"""The PairLink Home Assistant integration."""

from __future__ import annotations

import asyncio
import logging

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN, PLATFORMS
from .models import PairLinkConfigEntry, PairLinkCredentials
from .session import PairLinkSession

_LOGGER = logging.getLogger(__name__)
_CONNECTION_LOCK = "connection_lock"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PairLinkConfigEntry,
) -> bool:
    """Set up one PairLink switch without waiting for it to be online."""
    try:
        credentials = PairLinkCredentials.from_entry_data(dict(entry.data))
    except ValueError as err:
        raise ConfigEntryError(
            translation_domain=DOMAIN,
            translation_key="invalid_configuration",
        ) from err

    if bluetooth.async_scanner_count(hass, connectable=True) == 0:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="no_connectable_adapter",
        )

    _update_device_registry_address(hass, entry, credentials.address)
    domain_data = hass.data.setdefault(DOMAIN, {})
    connection_lock = domain_data.setdefault(_CONNECTION_LOCK, asyncio.Lock())
    session = PairLinkSession(
        hass,
        entry,
        credentials,
        connection_lock=connection_lock,
    )
    entry.runtime_data = session
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    session.async_start()
    return True


async def async_unload_entry(
    hass: HomeAssistant,
    entry: PairLinkConfigEntry,
) -> bool:
    """Unload platforms and always release the BLE session."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    await entry.runtime_data.async_stop()
    return unload_ok


async def _async_update_listener(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> None:
    """Reload once after Config Entry data or options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_remove_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> None:
    """Make a removed switch discoverable again without restarting HA."""
    address = entry.data.get("address")
    if isinstance(address, str):
        bluetooth.async_rediscover_address(hass, address)


def _update_device_registry_address(
    hass: HomeAssistant,
    entry: ConfigEntry,
    address: str,
) -> None:
    """Replace stale Bluetooth connections after an address change."""
    registry = dr.async_get(hass)
    connections = {(dr.CONNECTION_BLUETOOTH, address)}
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        if device.connections != connections:
            registry.async_update_device(device.id, new_connections=connections)
