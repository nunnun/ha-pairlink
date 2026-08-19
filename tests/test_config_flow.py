"""Tests for discovery, registration, and credential validation."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant import data_entry_flow
from homeassistant.components import bluetooth
from homeassistant.config_entries import SOURCE_BLUETOOTH, SOURCE_REAUTH, SOURCE_USER

from custom_components.pairlink.config_flow import (
    PairLinkConfigFlow,
    _FlowTarget,
    _RegistrationOutcome,
)
from custom_components.pairlink.discovery import parse_manufacturer_value
from custom_components.pairlink.models import PairLinkCredentials
from custom_components.pairlink.session import PairLinkDisconnectedError

IDLE = bytes.fromhex("c0ff0511223344bc9a78563412")
REGISTRATION = bytes.fromhex("c0ff0d1122334454455354a3f0fbbc9a7856341201")


def _service_info(
    payload: bytes,
    *,
    address: str = "12:34:56:78:9A:BC",
    connectable: bool = True,
    source: str = "11:22:33:44:55:66",
):
    return SimpleNamespace(
        manufacturer_data={65535: payload},
        address=address,
        connectable=connectable,
        source=source,
        device=SimpleNamespace(address=address),
    )


def _flow(hass, source: str = SOURCE_USER) -> PairLinkConfigFlow:
    flow = PairLinkConfigFlow()
    flow.hass = hass
    flow.handler = "pairlink"
    flow.flow_id = "test-flow"
    flow.context = {"source": source}
    return flow


async def test_bluetooth_discovery_sets_stable_switch_mac(hass) -> None:
    """Bluetooth discovery uses switch MAC rather than the current AP source."""
    flow = _flow(hass, SOURCE_BLUETOOTH)
    flow.async_set_unique_id = AsyncMock()
    flow._abort_if_unique_id_configured = MagicMock()

    result = await flow.async_step_bluetooth(_service_info(IDLE))

    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "bluetooth_confirm"
    assert result["description_placeholders"] == {"name": "PairLink switch 9A:BC"}
    flow.async_set_unique_id.assert_awaited_once_with("12:34:56:78:9A:BC")
    flow._abort_if_unique_id_configured.assert_called_once_with(
        updates={"address": "12:34:56:78:9A:BC"},
        reload_on_update=False,
    )


async def test_two_aruba_sources_resolve_to_one_switch_identity(hass) -> None:
    """AP reporter MAC changes cannot create a second PairLink Config Entry."""
    unique_ids: list[str] = []
    for source in ("48:00:20:00:00:01", "48:00:20:00:00:02"):
        flow = _flow(hass, SOURCE_BLUETOOTH)

        async def _set_unique_id(value: str) -> None:
            unique_ids.append(value)

        flow.async_set_unique_id = _set_unique_id
        assert await flow._async_set_target_from_service_info(
            _service_info(IDLE, source=source)
        )

    assert unique_ids == ["12:34:56:78:9A:BC", "12:34:56:78:9A:BC"]


async def test_user_discovery_deduplicates_same_switch_across_routes(
    hass, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One physical switch is listed once even when routes expose new addresses."""
    flow = _flow(hass)
    first = _service_info(IDLE, address="12:34:56:78:9A:BC", source="48:00:20:00:00:01")
    second = _service_info(
        IDLE, address="AA:BB:CC:DD:EE:FF", source="48:00:20:00:00:02"
    )

    monkeypatch.setattr(
        "custom_components.pairlink.config_flow.bluetooth.async_discovered_service_info",
        lambda *_args, **_kwargs: [first, second],
    )
    result = await flow.async_step_user()

    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert list(flow._discovered) == ["12:34:56:78:9A:BC"]


async def test_nonconnectable_discovery_is_rejected(hass) -> None:
    """Listen-only proxy discoveries cannot set up a GATT integration."""
    flow = _flow(hass, SOURCE_BLUETOOTH)
    result = await flow.async_step_bluetooth(_service_info(IDLE, connectable=False))
    assert result["type"] is data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "not_connectable"


async def test_registration_captures_and_validates_credentials(
    hass,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A complete registration and successful LOGIN create safe entry data."""
    flow = _flow(hass)
    flow._target = _FlowTarget(
        address="12:34:56:78:9A:BC",
        remote_id=bytes.fromhex("bc9a78563412"),
        name="PairLink switch 9A:BC",
    )
    flow._initial_registration = parse_manufacturer_value(REGISTRATION)
    monkeypatch.setattr(
        "custom_components.pairlink.config_flow.bluetooth.async_ble_device_from_address",
        lambda *_args, **_kwargs: SimpleNamespace(),
    )
    validate = AsyncMock()
    monkeypatch.setattr(
        "custom_components.pairlink.config_flow.async_validate_credentials",
        validate,
    )
    monkeypatch.setattr(
        "custom_components.pairlink.config_flow.generate_light_id",
        lambda: bytes.fromhex("665544332211"),
    )

    outcome = await flow._async_register_and_validate()

    assert outcome.error is None
    assert outcome.credentials is not None
    assert outcome.credentials.as_entry_data() == {
        "address": "12:34:56:78:9A:BC",
        "remote_id": "bc9a78563412",
        "home_id": "11223344",
        "password": "TEST",
        "remote_channel": 1,
        "light_id": "665544332211",
    }
    validate.assert_awaited_once()


async def test_registration_ignores_another_switch(
    hass,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unrelated type-0x0d packet cannot complete the target flow."""
    flow = _flow(hass)
    flow._target = _FlowTarget(
        address="12:34:56:78:9A:BC",
        remote_id=bytes.fromhex("bc9a78563412"),
        name="PairLink switch 9A:BC",
    )
    wrong = _service_info(bytes.fromhex("c0ff0d1122334454455354a3f0fb01020304050601"))
    right = _service_info(REGISTRATION)

    async def _process(_hass, predicate, _match_dict, mode, _timeout):
        assert mode is bluetooth.BluetoothScanningMode.PASSIVE
        assert not predicate(wrong)
        assert predicate(right)
        return right

    monkeypatch.setattr(
        "custom_components.pairlink.config_flow.bluetooth.async_process_advertisements",
        _process,
    )
    monkeypatch.setattr(
        "custom_components.pairlink.config_flow.bluetooth.async_ble_device_from_address",
        lambda *_args, **_kwargs: SimpleNamespace(),
    )
    monkeypatch.setattr(
        "custom_components.pairlink.config_flow.async_validate_credentials",
        AsyncMock(),
    )

    outcome = await flow._async_register_and_validate()

    assert outcome.error is None
    assert outcome.credentials is not None
    assert outcome.credentials.remote_id == bytes.fromhex("bc9a78563412")


async def test_registration_retries_a_transient_disconnect(
    hass,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dropped first authentication connection is retried once."""
    flow = _flow(hass)
    flow._target = _FlowTarget(
        address="12:34:56:78:9A:BC",
        remote_id=bytes.fromhex("bc9a78563412"),
        name="PairLink switch 9A:BC",
    )
    flow._initial_registration = parse_manufacturer_value(REGISTRATION)
    monkeypatch.setattr(
        "custom_components.pairlink.config_flow.bluetooth.async_ble_device_from_address",
        lambda *_args, **_kwargs: SimpleNamespace(),
    )
    validate = AsyncMock(
        side_effect=[
            PairLinkDisconnectedError("disconnected during authentication"),
            None,
        ]
    )
    monkeypatch.setattr(
        "custom_components.pairlink.config_flow.async_validate_credentials",
        validate,
    )

    outcome = await flow._async_register_and_validate()

    assert outcome.error is None
    assert outcome.credentials is not None
    assert validate.await_count == 2


async def test_registration_reports_repeated_disconnect(
    hass,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repeated authentication disconnects become a retryable connection error."""
    flow = _flow(hass)
    flow._target = _FlowTarget(
        address="12:34:56:78:9A:BC",
        remote_id=bytes.fromhex("bc9a78563412"),
        name="PairLink switch 9A:BC",
    )
    flow._initial_registration = parse_manufacturer_value(REGISTRATION)
    monkeypatch.setattr(
        "custom_components.pairlink.config_flow.bluetooth.async_ble_device_from_address",
        lambda *_args, **_kwargs: SimpleNamespace(),
    )
    validate = AsyncMock(
        side_effect=PairLinkDisconnectedError("disconnected during authentication")
    )
    monkeypatch.setattr(
        "custom_components.pairlink.config_flow.async_validate_credentials",
        validate,
    )

    outcome = await flow._async_register_and_validate()

    assert outcome.error == "cannot_connect"
    assert validate.await_count == 2


async def test_registration_timeout_is_retryable(
    hass,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Timeout becomes a translated form error, not an escaped exception."""
    flow = _flow(hass)
    flow._target = _FlowTarget(
        address="12:34:56:78:9A:BC",
        remote_id=bytes.fromhex("bc9a78563412"),
        name="PairLink switch 9A:BC",
    )
    monkeypatch.setattr(
        "custom_components.pairlink.config_flow.bluetooth.async_process_advertisements",
        AsyncMock(side_effect=TimeoutError),
    )

    outcome = await flow._async_register_and_validate()

    assert outcome.error == "registration_timeout"


async def test_progress_task_finishes_with_create_entry(
    hass,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Progress lifecycle reaches a Config Entry without a second task."""
    flow = _flow(hass)
    flow._target = _FlowTarget(
        address="12:34:56:78:9A:BC",
        remote_id=bytes.fromhex("bc9a78563412"),
        name="PairLink switch 9A:BC",
    )
    flow._initial_registration = parse_manufacturer_value(REGISTRATION)
    flow.async_set_unique_id = AsyncMock()
    flow._abort_if_unique_id_configured = MagicMock()
    hass.async_create_task = lambda coro, _name: asyncio.create_task(coro)
    monkeypatch.setattr(
        "custom_components.pairlink.config_flow.bluetooth.async_ble_device_from_address",
        lambda *_args, **_kwargs: SimpleNamespace(),
    )
    monkeypatch.setattr(
        "custom_components.pairlink.config_flow.async_validate_credentials",
        AsyncMock(),
    )

    progress = await flow.async_step_registration()
    assert progress["type"] is data_entry_flow.FlowResultType.SHOW_PROGRESS
    await flow._registration_task
    done = await flow.async_step_registration()
    assert done["type"] is data_entry_flow.FlowResultType.SHOW_PROGRESS_DONE
    result = await flow.async_step_registration_done()
    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["data"]["password"] == "TEST"


async def test_reauth_updates_existing_entry_without_creating_another(
    hass,
) -> None:
    """Successful reauth replaces data on the original Config Entry."""
    flow = _flow(hass, SOURCE_REAUTH)
    credentials = PairLinkCredentials(
        address="12:34:56:78:9A:BC",
        remote_id=bytes.fromhex("bc9a78563412"),
        home_id=bytes.fromhex("11223344"),
        password=b"TEST",
        remote_channel=1,
        light_id=bytes.fromhex("665544332211"),
    )

    async def _outcome() -> _RegistrationOutcome:
        return _RegistrationOutcome(credentials=credentials)

    flow._registration_task = asyncio.create_task(_outcome())
    await flow._registration_task
    flow.async_set_unique_id = AsyncMock()
    flow._abort_if_unique_id_mismatch = MagicMock()
    entry = MagicMock()
    flow._get_reauth_entry = MagicMock(return_value=entry)
    expected = {"type": data_entry_flow.FlowResultType.ABORT}
    flow.async_update_and_abort = MagicMock(return_value=expected)

    result = await flow.async_step_registration_done()

    assert result is expected
    flow._abort_if_unique_id_mismatch.assert_called_once_with()
    flow.async_update_and_abort.assert_called_once_with(
        entry,
        data=credentials.as_entry_data(),
    )
