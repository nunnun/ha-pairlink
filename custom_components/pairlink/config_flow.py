"""Config flow for the PairLink integration."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import voluptuous as vol
from bleak_retry_connector import BLEAK_RETRY_EXCEPTIONS
from homeassistant.components import bluetooth
from homeassistant.config_entries import (
    SOURCE_REAUTH,
    ConfigFlow,
    ConfigFlowResult,
)
from homeassistant.const import CONF_ADDRESS

from .aruba_compat import enable_aruba_uuid_compat
from .const import (
    CONF_LIGHT_ID,
    DOMAIN,
    PAIRLINK_REGISTRATION_ADVERTISEMENT,
    REGISTRATION_TIMEOUT,
)
from .discovery import (
    display_name,
    find_advertisement,
    generate_light_id,
    switch_unique_id,
)
from .models import PairLinkAdvertisement, PairLinkCredentials
from .session import (
    PairLinkAuthenticationError,
    PairLinkDisconnectedError,
    PairLinkLoginTimeout,
    async_validate_credentials,
)

_LOGGER = logging.getLogger(__name__)
_REGISTRATION_VALIDATION_ATTEMPTS = 2


@dataclass(frozen=True)
class _FlowTarget:
    """Non-secret flow target details."""

    address: str
    remote_id: bytes
    name: str


@dataclass(frozen=True)
class _RegistrationOutcome:
    """Expected result of the progress task."""

    credentials: PairLinkCredentials | None = None
    error: str | None = None


class PairLinkConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle PairLink setup and reauthentication."""

    VERSION = 2
    MINOR_VERSION = 0

    def __init__(self) -> None:
        """Initialize transient flow state."""
        self._target: _FlowTarget | None = None
        self._initial_registration: PairLinkAdvertisement | None = None
        self._registration_task: asyncio.Task[_RegistrationOutcome] | None = None
        self._discovered: dict[str, bluetooth.BluetoothServiceInfoBleak] = {}

    async def async_step_bluetooth(
        self,
        discovery_info: bluetooth.BluetoothServiceInfoBleak,
    ) -> ConfigFlowResult:
        """Handle manifest-driven Bluetooth discovery."""
        if not discovery_info.connectable:
            return self.async_abort(reason="not_connectable")
        if not await self._async_set_target_from_service_info(discovery_info):
            return self.async_abort(reason="not_supported")
        assert self._target is not None
        self._abort_if_unique_id_configured(
            updates={CONF_ADDRESS: self._target.address},
            reload_on_update=False,
        )
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Ask the user to confirm the discovered switch."""
        if user_input is not None:
            return await self.async_step_registration()
        return self.async_show_form(
            step_id="bluetooth_confirm",
            data_schema=vol.Schema({}),
            description_placeholders={"name": self._target.name},
        )

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """List currently visible, unconfigured PairLink switches."""
        if user_input is not None:
            service_info = self._discovered[user_input[CONF_ADDRESS]]
            if not await self._async_set_target_from_service_info(service_info):
                return self.async_abort(reason="not_supported")
            self._abort_if_unique_id_configured()
            return await self.async_step_bluetooth_confirm()

        configured_ids = {
            entry.unique_id
            for entry in self.hass.config_entries.async_entries(DOMAIN)
            if entry.unique_id is not None
        }
        configured_remote_ids = {
            entry.data.get("remote_id")
            for entry in self.hass.config_entries.async_entries(DOMAIN)
        }
        discovered_switch_ids: set[str] = set()
        self._discovered.clear()
        for service_info in bluetooth.async_discovered_service_info(
            self.hass, connectable=True
        ):
            advertisement = find_advertisement(service_info)
            if (
                advertisement is None
                or advertisement.remote_id is None
                or switch_unique_id(advertisement.remote_id) in configured_ids
                or advertisement.remote_id.hex() in configured_remote_ids
            ):
                continue
            switch_id = switch_unique_id(advertisement.remote_id)
            if switch_id in discovered_switch_ids:
                continue
            discovered_switch_ids.add(switch_id)
            self._discovered[service_info.address] = service_info

        if not self._discovered:
            return self.async_abort(reason="no_devices_found")

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ADDRESS): vol.In(
                        {
                            address: display_name(
                                find_advertisement(service_info).remote_id  # type: ignore[union-attr,arg-type]
                            )
                            for address, service_info in self._discovered.items()
                        }
                    )
                }
            ),
        )

    async def async_step_registration(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Show progress while credentials are captured and validated."""
        if self._registration_task is None:
            self._registration_task = self.hass.async_create_task(
                self._async_register_and_validate(),
                "PairLink registration",
            )
        if not self._registration_task.done():
            return self.async_show_progress(
                step_id="registration",
                progress_action="wait_for_registration",
                progress_task=self._registration_task,
            )
        return self.async_show_progress_done(next_step_id="registration_done")

    async def async_step_registration_done(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Create or update the Config Entry after progress completes."""
        assert self._registration_task is not None
        outcome = self._registration_task.result()
        if outcome.error is not None:
            return self.async_show_form(
                step_id="registration_retry",
                data_schema=vol.Schema({}),
                errors={"base": outcome.error},
            )
        credentials = outcome.credentials
        assert credentials is not None
        self._registration_task = None
        self._initial_registration = None

        await self.async_set_unique_id(switch_unique_id(credentials.remote_id))
        if self.source == SOURCE_REAUTH:
            self._abort_if_unique_id_mismatch()
            return self.async_update_and_abort(
                self._get_reauth_entry(),
                data=credentials.as_entry_data(),
            )

        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=display_name(credentials.remote_id),
            data=credentials.as_entry_data(),
        )

    async def async_step_registration_retry(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Allow registration to be retried without restarting the flow."""
        if user_input is not None:
            self._registration_task = None
            self._initial_registration = None
            return await self.async_step_registration()
        return self.async_show_form(
            step_id="registration_retry",
            data_schema=vol.Schema({}),
        )

    async def async_step_reauth(
        self,
        entry_data: dict[str, Any],
    ) -> ConfigFlowResult:
        """Start credential refresh for an existing entry."""
        entry = self._get_reauth_entry()
        try:
            credentials = PairLinkCredentials.from_entry_data(dict(entry.data))
        except ValueError:
            return self.async_abort(reason="invalid_stored_configuration")
        self._target = _FlowTarget(
            address=credentials.address,
            remote_id=credentials.remote_id,
            name=display_name(credentials.remote_id),
        )
        self.context["title_placeholders"] = {"name": self._target.name}
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Ask the user to place the same switch into registration mode."""
        if user_input is not None:
            return await self.async_step_registration()
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({}),
            description_placeholders={"name": self._target.name},
        )

    async def _async_set_target_from_service_info(
        self,
        service_info: bluetooth.BluetoothServiceInfoBleak,
    ) -> bool:
        """Parse discovery and initialize a flow target."""
        advertisement = find_advertisement(service_info)
        if advertisement is None or advertisement.remote_id is None:
            return False
        remote_id = advertisement.remote_id
        await self.async_set_unique_id(switch_unique_id(remote_id))
        name = display_name(remote_id)
        self._target = _FlowTarget(
            address=service_info.address,
            remote_id=remote_id,
            name=name,
        )
        if advertisement.is_complete_registration:
            self._initial_registration = advertisement
        self.context["title_placeholders"] = {"name": name}
        return True

    async def _async_register_and_validate(self) -> _RegistrationOutcome:
        """Capture a complete registration advertisement and test LOGIN."""
        target = self._target
        assert target is not None
        service_info: bluetooth.BluetoothServiceInfoBleak | None = None
        advertisement = self._initial_registration

        if advertisement is None:
            matched: PairLinkAdvertisement | None = None

            def _matches(
                candidate: bluetooth.BluetoothServiceInfoBleak,
            ) -> bool:
                nonlocal matched
                matched = find_advertisement(
                    candidate,
                    expected_type=PAIRLINK_REGISTRATION_ADVERTISEMENT,
                    remote_id=target.remote_id,
                )
                return bool(matched and matched.is_complete_registration)

            try:
                service_info = await bluetooth.async_process_advertisements(
                    self.hass,
                    _matches,
                    {
                        "address": target.address,
                        "connectable": True,
                    },
                    bluetooth.BluetoothScanningMode.PASSIVE,
                    REGISTRATION_TIMEOUT,
                )
            except TimeoutError:
                return _RegistrationOutcome(error="registration_timeout")
            advertisement = matched

        if advertisement is None or not advertisement.is_complete_registration:
            return _RegistrationOutcome(error="invalid_registration")
        assert advertisement.home_id is not None
        assert advertisement.password is not None
        assert advertisement.remote_id is not None
        assert advertisement.remote_channel is not None

        try:
            light_id = self._select_light_id()
            credentials = PairLinkCredentials(
                address=target.address,
                remote_id=advertisement.remote_id,
                home_id=advertisement.home_id,
                password=advertisement.password,
                remote_channel=advertisement.remote_channel,
                light_id=light_id,
            )
        except ValueError:
            return _RegistrationOutcome(error="invalid_registration")

        device = bluetooth.async_ble_device_from_address(
            self.hass,
            target.address,
            connectable=True,
        )
        if device is None and service_info is not None:
            device = service_info.device
        if device is None:
            return _RegistrationOutcome(error="cannot_connect")

        for attempt in range(_REGISTRATION_VALIDATION_ATTEMPTS):
            try:
                enable_aruba_uuid_compat()
                await async_validate_credentials(device, credentials)
            except PairLinkAuthenticationError, PairLinkLoginTimeout:
                return _RegistrationOutcome(error="invalid_auth")
            except PairLinkDisconnectedError:
                if attempt == _REGISTRATION_VALIDATION_ATTEMPTS - 1:
                    return _RegistrationOutcome(error="cannot_connect")
                _LOGGER.debug(
                    "PairLink disconnected during registration validation; retrying"
                )
            except BLEAK_RETRY_EXCEPTIONS:
                return _RegistrationOutcome(error="cannot_connect")
            except Exception as err:
                _LOGGER.error(
                    "Unexpected PairLink registration failure: %s",
                    type(err).__name__,
                )
                return _RegistrationOutcome(error="unknown")
            else:
                break
        return _RegistrationOutcome(credentials=credentials)

    def _select_light_id(self) -> bytes:
        """Reuse the installation identity or create a transport-neutral one."""
        if self.source == SOURCE_REAUTH:
            existing = self._get_reauth_entry().data.get(CONF_LIGHT_ID)
            if isinstance(existing, str):
                value = bytes.fromhex(existing)
                if len(value) == 6:
                    return value
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            existing = entry.data.get(CONF_LIGHT_ID)
            if not isinstance(existing, str):
                continue
            try:
                value = bytes.fromhex(existing)
            except ValueError:
                continue
            if len(value) == 6:
                return value
        return generate_light_id()
