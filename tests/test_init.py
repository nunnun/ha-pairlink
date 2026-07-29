"""Tests for Config Entry setup and cleanup."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.exceptions import ConfigEntryNotReady

from custom_components.pairlink import async_setup_entry, async_unload_entry

from .test_models import ENTRY_DATA


async def test_setup_starts_background_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Setup succeeds without waiting for the physical switch to be online."""
    hass = MagicMock()
    hass.data = {}
    hass.config_entries.async_forward_entry_setups = AsyncMock()
    entry = MagicMock(data=ENTRY_DATA)
    entry.runtime_data = None
    session = MagicMock()
    session.async_stop = AsyncMock()
    monkeypatch.setattr(
        "custom_components.pairlink.bluetooth.async_scanner_count",
        lambda *_args, **_kwargs: 1,
    )
    monkeypatch.setattr(
        "custom_components.pairlink._update_device_registry_address",
        MagicMock(),
    )
    session_factory = MagicMock(return_value=session)
    monkeypatch.setattr(
        "custom_components.pairlink.PairLinkSession",
        session_factory,
    )

    assert await async_setup_entry(hass, entry)

    assert entry.runtime_data is session
    assert isinstance(session_factory.call_args.kwargs["connection_lock"], asyncio.Lock)
    session.async_start.assert_called_once()
    hass.config_entries.async_forward_entry_setups.assert_awaited_once()


async def test_setup_shares_connection_lock_between_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All PairLink entries serialize connection setup on one adapter."""
    hass = MagicMock()
    hass.data = {}
    hass.config_entries.async_forward_entry_setups = AsyncMock()
    session_factory = MagicMock(return_value=MagicMock())
    monkeypatch.setattr(
        "custom_components.pairlink.bluetooth.async_scanner_count",
        lambda *_args, **_kwargs: 1,
    )
    monkeypatch.setattr(
        "custom_components.pairlink._update_device_registry_address",
        MagicMock(),
    )
    monkeypatch.setattr(
        "custom_components.pairlink.PairLinkSession",
        session_factory,
    )

    for _ in range(2):
        entry = MagicMock(data=ENTRY_DATA)
        entry.runtime_data = None
        await async_setup_entry(hass, entry)

    first_lock = session_factory.call_args_list[0].kwargs["connection_lock"]
    second_lock = session_factory.call_args_list[1].kwargs["connection_lock"]
    assert first_lock is second_lock


async def test_setup_retries_without_connectable_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing adapter is an integration setup problem, not device downtime."""
    hass = MagicMock()
    entry = MagicMock(data=ENTRY_DATA)
    monkeypatch.setattr(
        "custom_components.pairlink.bluetooth.async_scanner_count",
        lambda *_args, **_kwargs: 0,
    )

    with pytest.raises(ConfigEntryNotReady):
        await async_setup_entry(hass, entry)


async def test_unload_always_stops_session() -> None:
    """Successful platform unload awaits BLE cleanup."""
    hass = MagicMock()
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
    session = MagicMock()
    session.async_stop = AsyncMock()
    entry = MagicMock(runtime_data=session)

    assert await async_unload_entry(hass, entry)
    session.async_stop.assert_awaited_once()
