"""Privacy-preserving diagnostics for PairLink."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import (
    CONF_HOME_ID,
    CONF_LIGHT_ID,
    CONF_PASSWORD,
    CONF_REMOTE_ID,
)
from .models import PairLinkConfigEntry

_TO_REDACT = {
    "address",
    CONF_REMOTE_ID,
    CONF_HOME_ID,
    CONF_PASSWORD,
    CONF_LIGHT_ID,
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: PairLinkConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics without credentials or stable identifiers."""
    return {
        "entry": async_redact_data(dict(entry.data), _TO_REDACT),
        "session": entry.runtime_data.diagnostics.as_dict(),
    }
