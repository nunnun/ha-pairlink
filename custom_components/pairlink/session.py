"""Connection lifecycle for PairLink switches."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from bleak import BleakClient
from bleak.backends.device import BLEDevice
from bleak_retry_connector import BLEAK_RETRY_EXCEPTIONS, establish_connection
from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant, callback
from homeassistant.util import dt as dt_util

from .const import (
    COMMAND_TO_EVENT_TYPE,
    DEDUPLICATION_WINDOW,
    FFD1_UUID,
    FFD2_UUID,
    LOGIN_RESPONSE_TIMEOUT,
    MAX_LOGIN_TIMEOUTS_BEFORE_REAUTH,
    NOTIFICATION_QUEUE_SIZE,
    POST_WELCOME_DELAY,
    RECONNECT_BACKOFF,
)
from .discovery import find_advertisement
from .models import (
    EventCallback,
    PairLinkConfigEntry,
    PairLinkCredentials,
    RemoteEvent,
    SessionDiagnostics,
    SessionState,
    StateCallback,
)
from .protocol import PairLinkCodec

_LOGGER = logging.getLogger(__name__)


class PairLinkError(Exception):
    """Base class for safe PairLink session errors."""


class PairLinkAuthenticationError(PairLinkError):
    """Credentials were rejected or could not validate a LOGIN response."""


class PairLinkLoginTimeout(PairLinkError):
    """The switch did not return a LOGIN response."""


class PairLinkDisconnectedError(PairLinkError):
    """The active BLE connection ended."""


class PairLinkConnection:
    """One authenticated BLE connection to one PairLink switch."""

    def __init__(
        self,
        credentials: PairLinkCredentials,
        codec: PairLinkCodec,
    ) -> None:
        """Initialize connection state."""
        self._credentials = credentials
        self._codec = codec
        self._client: BleakClient | None = None
        self._disconnect_event = asyncio.Event()
        self._notifications: asyncio.Queue[bytes] = asyncio.Queue(
            maxsize=NOTIFICATION_QUEUE_SIZE
        )
        self._backlog: deque[bytes] = deque()
        self._closing = False
        self.peer_crypto_vaddr: bytes | None = None
        self.dropped_notifications = 0

    @property
    def is_connected(self) -> bool:
        """Return whether Bleak still reports an active connection."""
        return bool(
            self._client
            and self._client.is_connected
            and not self._disconnect_event.is_set()
        )

    async def async_connect_and_authenticate(self, device: BLEDevice) -> None:
        """Connect, subscribe, authenticate, and initialize the session."""
        self._client = await establish_connection(
            BleakClient,
            device,
            "PairLink switch",
            disconnected_callback=self._disconnected_callback,
            use_services_cache=False,
        )
        await self._client.start_notify(FFD2_UUID, self._notification_callback)
        await self._client.write_gatt_char(
            FFD1_UUID,
            self._codec.make_login(),
            response=True,
        )

        try:
            async with asyncio.timeout(LOGIN_RESPONSE_TIMEOUT):
                login_packet = await self._wait_for_login_response()
        except TimeoutError as err:
            raise PairLinkLoginTimeout("LOGIN response timed out") from err

        try:
            response = self._codec.parse_login_response(login_packet)
        except ValueError as err:
            raise PairLinkAuthenticationError("LOGIN response was invalid") from err

        self.peer_crypto_vaddr = response.peer_crypto_vaddr
        await self._client.write_gatt_char(FFD1_UUID, b"\x01\x01", response=True)
        await asyncio.sleep(POST_WELCOME_DELAY)
        status = b"\x01\x02" + self._credentials.light_id + b"\x01\x00"
        await self._client.write_gatt_char(FFD1_UUID, status, response=True)

    async def _wait_for_login_response(self) -> bytes:
        """Wait for LOGIN while retaining unrelated notifications."""
        while True:
            packet = await self._async_next_live_packet()
            if packet is None:
                raise PairLinkDisconnectedError("disconnected during authentication")
            if packet.startswith(b"\x02\x19"):
                return packet
            self._backlog.append(packet)

    async def async_next_packet(self) -> bytes | None:
        """Return the next notification, or None after disconnect."""
        if self._backlog:
            return self._backlog.popleft()
        return await self._async_next_live_packet()

    async def _async_next_live_packet(self) -> bytes | None:
        """Wait for a new notification or an active disconnect."""
        if not self.is_connected:
            return None

        notification_task = asyncio.create_task(self._notifications.get())
        disconnect_task = asyncio.create_task(self._disconnect_event.wait())
        done, pending = await asyncio.wait(
            (notification_task, disconnect_task),
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        for task in pending:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if notification_task in done:
            return notification_task.result()
        return None

    async def async_close(self) -> None:
        """Stop notifications and disconnect this client."""
        self._closing = True
        client = self._client
        self._client = None
        self._disconnect_event.set()
        if client is None:
            return
        if client.is_connected:
            with contextlib.suppress(*BLEAK_RETRY_EXCEPTIONS):
                await client.stop_notify(FFD2_UUID)
            with contextlib.suppress(*BLEAK_RETRY_EXCEPTIONS):
                await client.disconnect()

    @callback
    def _notification_callback(self, _sender: Any, value: bytearray) -> None:
        """Queue notification bytes without parsing in Bleak's callback."""
        try:
            self._notifications.put_nowait(bytes(value))
        except asyncio.QueueFull:
            self.dropped_notifications += 1

    @callback
    def _disconnected_callback(self, _client: BleakClient) -> None:
        """Signal the connection loop without doing I/O."""
        if not self._closing:
            self._disconnect_event.set()


async def async_validate_credentials(
    device: BLEDevice,
    credentials: PairLinkCredentials,
) -> None:
    """Validate credentials with a short-lived full PairLink handshake."""
    codec = PairLinkCodec(credentials.home_id, credentials.password)
    connection = PairLinkConnection(credentials, codec)
    try:
        await connection.async_connect_and_authenticate(device)
    finally:
        await connection.async_close()


@dataclass
class _Burst:
    """One semantic retransmission burst."""

    first_seen: float
    repeat_count: int = 1


class EventDeduplicator:
    """Collapse repeated mesh copies while keeping per-session state."""

    def __init__(self, window: float = DEDUPLICATION_WINDOW) -> None:
        """Initialize the deduplication window."""
        self._window = window
        self._bursts: dict[tuple[bytes, bytes, int, int, bytes], _Burst] = {}

    def classify(self, event: RemoteEvent) -> tuple[bool, int]:
        """Return whether an event is a duplicate and its repeat count."""
        if self._window <= 0:
            return False, 1
        key = (
            event.source_vaddr,
            event.remote_id,
            event.remote_channel,
            event.command,
            event.extra,
        )
        now = time.monotonic()
        burst = self._bursts.get(key)
        if burst is not None and now - burst.first_seen < self._window:
            burst.repeat_count += 1
            return True, burst.repeat_count
        self._bursts[key] = _Burst(first_seen=now)
        self._bursts = {
            stale_key: stale_burst
            for stale_key, stale_burst in self._bursts.items()
            if now - stale_burst.first_seen < self._window
        }
        return False, 1


class PairLinkSession:
    """Maintain one switch's independent authenticated GATT session."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: PairLinkConfigEntry,
        credentials: PairLinkCredentials,
        connection_lock: asyncio.Lock | None = None,
    ) -> None:
        """Initialize a PairLink session."""
        self.hass = hass
        self.entry = entry
        self.credentials = credentials
        self._connection_lock = connection_lock or asyncio.Lock()
        self.codec = PairLinkCodec(credentials.home_id, credentials.password)
        self.diagnostics = SessionDiagnostics()
        self._connection: PairLinkConnection | None = None
        self._connection_task: asyncio.Task[None] | None = None
        self._reauth_close_task: asyncio.Task[None] | None = None
        self._cancel_bluetooth_callback: Callable[[], None] | None = None
        self._event_callbacks: set[EventCallback] = set()
        self._state_callbacks: set[StateCallback] = set()
        self._deduplicator = EventDeduplicator()
        self._wake_event = asyncio.Event()
        self._stopping = False
        self._reauth_started = False
        self._consecutive_login_timeouts = 0
        self._home_id_mismatch_count = 0
        self._failure_reported = False

    @property
    def available(self) -> bool:
        """Return whether the switch is authenticated and ready."""
        return self.diagnostics.state is SessionState.READY

    @callback
    def async_start(self) -> None:
        """Start advertisement tracking and the background connection loop."""
        if self._connection_task is not None:
            return
        self._stopping = False
        self._cancel_bluetooth_callback = bluetooth.async_register_callback(
            self.hass,
            self._async_seen,
            {
                "address": self.credentials.address,
                "connectable": True,
            },
            bluetooth.BluetoothScanningMode.PASSIVE,
        )
        self._connection_task = self.entry.async_create_background_task(
            self.hass,
            self._async_connection_loop(),
            f"PairLink session {self.entry.entry_id}",
        )

    async def async_stop(self) -> None:
        """Stop reconnecting and release every BLE resource."""
        self._stopping = True
        if self._cancel_bluetooth_callback is not None:
            self._cancel_bluetooth_callback()
            self._cancel_bluetooth_callback = None
        task = self._connection_task
        self._connection_task = None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        reauth_close_task = self._reauth_close_task
        self._reauth_close_task = None
        if (
            reauth_close_task is not None
            and reauth_close_task is not asyncio.current_task()
        ):
            with contextlib.suppress(asyncio.CancelledError):
                await reauth_close_task
        await self._async_close_connection()
        self._set_state(SessionState.STOPPED, connected=False)

    @callback
    def subscribe_events(self, listener: EventCallback) -> Callable[[], None]:
        """Subscribe to unique decoded button events."""
        self._event_callbacks.add(listener)
        return lambda: self._event_callbacks.discard(listener)

    @callback
    def subscribe_state(self, listener: StateCallback) -> Callable[[], None]:
        """Subscribe to availability state changes."""
        self._state_callbacks.add(listener)
        return lambda: self._state_callbacks.discard(listener)

    @callback
    def _async_seen(
        self,
        service_info: bluetooth.BluetoothServiceInfoBleak,
        _change: bluetooth.BluetoothChange,
    ) -> None:
        """Wake a backed-off session as soon as its switch advertises."""
        if service_info.rssi > -127 and self.diagnostics.rssi != service_info.rssi:
            self.diagnostics.rssi = service_info.rssi
            self._notify_state()
        advertisement = find_advertisement(
            service_info,
            remote_id=self.credentials.remote_id,
        )
        if advertisement is not None and advertisement.home_id is not None:
            if advertisement.home_id == self.credentials.home_id:
                self._home_id_mismatch_count = 0
            else:
                self._home_id_mismatch_count += 1
                if self._home_id_mismatch_count >= 3:
                    self._start_reauth()
        self._wake_event.set()

    async def _async_connection_loop(self) -> None:
        """Resolve, connect, authenticate, listen, and reconnect forever."""
        backoff_index = 0
        while not self._stopping and not self._reauth_started:
            try:
                self._set_state(SessionState.RESOLVING, connected=False)
                device = bluetooth.async_ble_device_from_address(
                    self.hass,
                    self.credentials.address,
                    connectable=True,
                )
                if device is None:
                    await self._async_backoff(RECONNECT_BACKOFF[backoff_index])
                    backoff_index = min(backoff_index + 1, len(RECONNECT_BACKOFF) - 1)
                    continue

                self.diagnostics.connection_attempts += 1
                self._set_state(SessionState.CONNECTING, connected=False)
                connection = PairLinkConnection(self.credentials, self.codec)
                self._connection = connection
                async with self._connection_lock:
                    if self._stopping or self._reauth_started:
                        await self._async_close_connection()
                        break
                    self._set_state(SessionState.AUTHENTICATING, connected=True)
                    await connection.async_connect_and_authenticate(device)
                if self._reauth_started:
                    await self._async_close_connection()
                    break
                self._consecutive_login_timeouts = 0
                backoff_index = 0
                self._set_state(SessionState.READY, connected=True)
                self.diagnostics.last_ready_at = dt_util.utcnow()
                if self._failure_reported:
                    _LOGGER.info("PairLink switch connection recovered")
                    self._failure_reported = False
                await self._async_process_notifications(connection)
                raise PairLinkDisconnectedError("connection ended")
            except asyncio.CancelledError:
                raise
            except PairLinkAuthenticationError:
                await self._async_close_connection()
                self._start_reauth()
            except PairLinkLoginTimeout:
                self._consecutive_login_timeouts += 1
                await self._async_close_connection()
                if self._consecutive_login_timeouts >= MAX_LOGIN_TIMEOUTS_BEFORE_REAUTH:
                    self._start_reauth()
                else:
                    self._report_connection_failure("authentication timed out")
                    await self._async_backoff(RECONNECT_BACKOFF[backoff_index])
                    backoff_index = min(backoff_index + 1, len(RECONNECT_BACKOFF) - 1)
            except (*BLEAK_RETRY_EXCEPTIONS, PairLinkDisconnectedError) as err:
                if isinstance(err, PairLinkDisconnectedError):
                    self.diagnostics.disconnect_count += 1
                await self._async_close_connection()
                if self._reauth_started:
                    break
                self._report_connection_failure(type(err).__name__)
                await self._async_backoff(RECONNECT_BACKOFF[backoff_index])
                backoff_index = min(backoff_index + 1, len(RECONNECT_BACKOFF) - 1)
            except Exception as err:  # Defensive isolation for a background task.
                await self._async_close_connection()
                if self._reauth_started:
                    break
                self._report_connection_failure(type(err).__name__)
                await self._async_backoff(RECONNECT_BACKOFF[backoff_index])
                backoff_index = min(backoff_index + 1, len(RECONNECT_BACKOFF) - 1)

    async def _async_process_notifications(
        self, connection: PairLinkConnection
    ) -> None:
        """Process packets until the active connection ends."""
        while connection.is_connected and not self._stopping:
            packet = await connection.async_next_packet()
            if packet is None:
                return
            self._process_packet(packet, connection)

    @callback
    def _process_packet(
        self,
        packet: bytes,
        connection: PairLinkConnection,
    ) -> None:
        """Decode and publish one notification without exposing packet data."""
        peer_crypto_vaddr = connection.peer_crypto_vaddr
        if not packet.startswith(b"\x06") or peer_crypto_vaddr is None:
            self.diagnostics.unknown_packet_count += 1
            return
        try:
            plaintext = self.codec.decrypt_data_event(packet, peer_crypto_vaddr)
            event = self.codec.parse_remote_event(plaintext)
        except ValueError:
            self.diagnostics.unknown_packet_count += 1
            return
        if (
            event.remote_id != self.credentials.remote_id
            or event.remote_channel != self.credentials.remote_channel
        ):
            self.diagnostics.unknown_packet_count += 1
            return
        if event.command not in COMMAND_TO_EVENT_TYPE:
            self.diagnostics.unknown_packet_count += 1
            return
        duplicate, repeat_count = self._deduplicator.classify(event)
        if duplicate:
            self.diagnostics.duplicate_count += 1
            return
        self.diagnostics.decoded_event_count += 1
        self.diagnostics.last_event_at = dt_util.utcnow()
        for listener in tuple(self._event_callbacks):
            try:
                listener(event, repeat_count)
            except Exception:  # Entity callbacks must not kill BLE processing.
                _LOGGER.exception("PairLink event subscriber failed")

    async def _async_backoff(self, delay: float) -> None:
        """Wait for retry, shortened by a new advertisement."""
        if self._reauth_started:
            return
        self._set_state(SessionState.BACKOFF, connected=False)
        self._wake_event.clear()
        try:
            async with asyncio.timeout(delay):
                await self._wake_event.wait()
        except TimeoutError:
            pass

    async def _async_close_connection(self) -> None:
        """Close and forget the current connection."""
        connection = self._connection
        self._connection = None
        if connection is None:
            return
        await connection.async_close()
        self.diagnostics.dropped_notification_count += connection.dropped_notifications
        self.diagnostics.connected = False

    @callback
    def _start_reauth(self) -> None:
        """Start exactly one reauthentication flow."""
        if self._reauth_started or self._stopping:
            return
        self._reauth_started = True
        self._set_state(SessionState.REAUTH_REQUIRED, connected=False)
        _LOGGER.warning("PairLink credentials must be refreshed")
        if self._connection is not None:
            self._reauth_close_task = self.entry.async_create_task(
                self.hass,
                self._async_close_for_reauth(),
                "Close PairLink connection for reauthentication",
            )
        self._wake_event.set()
        self.entry.async_start_reauth(self.hass)

    async def _async_close_for_reauth(self) -> None:
        """Release an active GATT connection when reauth is requested."""
        await self._async_close_connection()

    def _report_connection_failure(self, error_name: str) -> None:
        """Log only the first repeated connection failure at warning level."""
        if not self._failure_reported:
            _LOGGER.warning("PairLink switch connection unavailable")
            self._failure_reported = True
        else:
            _LOGGER.debug("PairLink reconnect failed: %s", error_name)

    @callback
    def _set_state(self, state: SessionState, *, connected: bool) -> None:
        """Update state and notify entities when something changed."""
        if self.diagnostics.state is state and self.diagnostics.connected is connected:
            return
        self.diagnostics.state = state
        self.diagnostics.connected = connected
        self._notify_state()

    @callback
    def _notify_state(self) -> None:
        """Notify entities after runtime state or signal strength changes."""
        for listener in tuple(self._state_callbacks):
            listener()
