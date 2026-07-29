"""Tests for connection, deduplication, and session isolation."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.pairlink.models import (
    PairLinkCredentials,
    RemoteEvent,
)
from custom_components.pairlink.protocol import PairLinkCodec
from custom_components.pairlink.session import (
    EventDeduplicator,
    PairLinkConnection,
    PairLinkDisconnectedError,
    PairLinkLoginTimeout,
    PairLinkSession,
)


@pytest.fixture
def credentials() -> PairLinkCredentials:
    """Return anonymized valid credentials."""
    return PairLinkCredentials(
        address="12:34:56:78:9A:BC",
        remote_id=bytes.fromhex("bc9a78563412"),
        home_id=bytes.fromhex("11223344"),
        password=b"TEST",
        remote_channel=1,
        light_id=bytes.fromhex("665544332211"),
    )


class _FakeClient:
    """Small Bleak client that answers LOGIN with an anonymized vector."""

    def __init__(self) -> None:
        self.is_connected = True
        self.notify_callback = None
        self.writes: list[tuple[str, bytes, bool]] = []
        self.stopped = False
        self.disconnected = False

    async def start_notify(self, _uuid, callback) -> None:
        self.notify_callback = callback

    async def write_gatt_char(self, uuid, packet, *, response) -> None:
        self.writes.append((uuid, bytes(packet), response))
        if packet.startswith(b"\x01\x19"):
            assert self.notify_callback is not None
            self.notify_callback(
                None,
                bytearray.fromhex("0219999cd93132539da135a6c3213d4665db"),
            )

    async def stop_notify(self, _uuid) -> None:
        self.stopped = True

    async def disconnect(self) -> None:
        self.is_connected = False
        self.disconnected = True


async def test_connection_performs_full_handshake(
    monkeypatch: pytest.MonkeyPatch,
    credentials: PairLinkCredentials,
) -> None:
    """The shared connection primitive must enforce management packet order."""
    client = _FakeClient()

    async def _establish(*_args, **_kwargs):
        assert _kwargs["use_services_cache"] is False
        return client

    monkeypatch.setattr(
        "custom_components.pairlink.session.establish_connection",
        _establish,
    )
    connection = PairLinkConnection(
        credentials,
        PairLinkCodec(credentials.home_id, credentials.password),
    )
    await connection.async_connect_and_authenticate(SimpleNamespace())
    assert connection.peer_crypto_vaddr == bytes.fromhex("a1b2c3d4")
    assert [packet[:2] for _, packet, _ in client.writes] == [
        b"\x01\x19",
        b"\x01\x01",
        b"\x01\x02",
    ]
    assert client.writes[-1][1] == (b"\x01\x02" + credentials.light_id + b"\x01\x00")
    await connection.async_close()
    assert client.stopped
    assert client.disconnected


async def test_login_wait_ends_immediately_on_disconnect(
    credentials: PairLinkCredentials,
) -> None:
    """Authentication does not wait for its timeout after the link drops."""
    connection = PairLinkConnection(
        credentials,
        PairLinkCodec(credentials.home_id, credentials.password),
    )
    client = SimpleNamespace(is_connected=True)
    connection._client = client
    wait_task = asyncio.create_task(connection._wait_for_login_response())
    await asyncio.sleep(0)

    client.is_connected = False
    connection._disconnected_callback(client)

    with pytest.raises(PairLinkDisconnectedError):
        await asyncio.wait_for(wait_task, timeout=0.1)


def _remote_event(command: int = 1) -> RemoteEvent:
    return RemoteEvent(
        destination_vaddr=b"\xff" * 4,
        source_vaddr=bytes(4),
        remote_id=bytes.fromhex("bc9a78563412"),
        remote_channel=1,
        command=command,
        extra=b"\x00",
    )


def test_deduplication_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only copies inside the semantic window are duplicates."""
    now = 100.0
    monkeypatch.setattr(
        "custom_components.pairlink.session.time.monotonic",
        lambda: now,
    )
    deduplicator = EventDeduplicator(window=12)
    assert deduplicator.classify(_remote_event()) == (False, 1)
    now = 105.0
    assert deduplicator.classify(_remote_event()) == (True, 2)
    now = 113.0
    assert deduplicator.classify(_remote_event()) == (False, 1)
    assert deduplicator.classify(_remote_event(command=2)) == (False, 1)


def test_session_decodes_and_isolates_events(
    credentials: PairLinkCredentials,
) -> None:
    """The default session emits every rapid physical button operation."""
    hass = MagicMock()
    entry = MagicMock(entry_id="entry-a")
    session = PairLinkSession(hass, entry, credentials)
    received: list[tuple[RemoteEvent, int]] = []
    session.subscribe_events(lambda event, repeat: received.append((event, repeat)))
    connection = SimpleNamespace(peer_crypto_vaddr=bytes.fromhex("a1b2c3d4"))
    on_wire = bytes.fromhex("06ffffffff210d4073c52f4a9e587b295f58878c714a")

    session._process_packet(on_wire, connection)
    session._process_packet(on_wire, connection)

    assert [item[0].command for item in received] == [1, 1]
    assert session.diagnostics.decoded_event_count == 2
    assert session.diagnostics.duplicate_count == 0


def test_session_rejects_wrong_remote_and_unknown_command(
    credentials: PairLinkCredentials,
) -> None:
    """Only the configured remote/channel and known commands may emit events."""
    session = PairLinkSession(
        MagicMock(),
        MagicMock(entry_id="entry-a"),
        credentials,
    )
    received: list[tuple[RemoteEvent, int]] = []
    session.subscribe_events(lambda event, repeat: received.append((event, repeat)))
    connection = SimpleNamespace(peer_crypto_vaddr=bytes.fromhex("a1b2c3d4"))
    base = bytearray.fromhex("03ffffffff210000000052bc9a78563412010100")

    wrong_remote = base.copy()
    wrong_remote[11] ^= 0xFF
    session._process_packet(
        session.codec.encrypt_data_event(
            bytes(wrong_remote),
            connection.peer_crypto_vaddr,
        ),
        connection,
    )
    unknown_command = base.copy()
    unknown_command[18] = 0x7F
    session._process_packet(
        session.codec.encrypt_data_event(
            bytes(unknown_command),
            connection.peer_crypto_vaddr,
        ),
        connection,
    )

    assert received == []
    assert session.diagnostics.unknown_packet_count == 2


def test_two_sessions_do_not_share_dedup_state(
    credentials: PairLinkCredentials,
) -> None:
    """The same wire event remains unique in two independent entries."""
    connection = SimpleNamespace(peer_crypto_vaddr=bytes.fromhex("a1b2c3d4"))
    on_wire = bytes.fromhex("06ffffffff210d4073c52f4a9e587b295f58878c714a")
    sessions = [
        PairLinkSession(MagicMock(), MagicMock(entry_id=f"entry-{index}"), credentials)
        for index in range(2)
    ]
    received = [[], []]
    for index, session in enumerate(sessions):
        session.subscribe_events(
            lambda event, repeat, target=received[index]: target.append((event, repeat))
        )
        session._process_packet(on_wire, connection)
    assert [len(events) for events in received] == [1, 1]
    assert [session.diagnostics.decoded_event_count for session in sessions] == [1, 1]


def test_three_home_id_mismatches_start_reauth(
    credentials: PairLinkCredentials,
) -> None:
    """A sustained Home ID change requests new registration credentials."""
    hass = MagicMock()
    entry = MagicMock(entry_id="entry-a")
    session = PairLinkSession(hass, entry, credentials)
    service_info = SimpleNamespace(
        manufacturer_data={65535: bytes.fromhex("c0ff0599887766bc9a78563412")},
        rssi=-88,
    )

    for _ in range(3):
        session._async_seen(service_info, MagicMock())

    entry.async_start_reauth.assert_called_once_with(hass)


def test_advertisement_updates_signal_strength(
    credentials: PairLinkCredentials,
) -> None:
    """The latest advertisement RSSI is published to subscribed entities."""
    session = PairLinkSession(
        MagicMock(),
        MagicMock(entry_id="entry-a"),
        credentials,
    )
    listener = MagicMock()
    session.subscribe_state(listener)
    service_info = SimpleNamespace(manufacturer_data={}, rssi=-87)

    session._async_seen(service_info, MagicMock())

    assert session.diagnostics.rssi == -87
    listener.assert_called_once()

    service_info.rssi = -127
    session._async_seen(service_info, MagicMock())

    assert session.diagnostics.rssi == -87
    listener.assert_called_once()


async def test_reauth_closes_an_active_connection(
    credentials: PairLinkCredentials,
) -> None:
    """Reauthentication immediately releases an obsolete GATT session."""
    hass = MagicMock()
    entry = MagicMock(entry_id="entry-a")
    entry.async_create_task.side_effect = lambda _hass, coroutine, _name: (
        asyncio.create_task(coroutine)
    )
    session = PairLinkSession(hass, entry, credentials)
    connection = SimpleNamespace(
        async_close=AsyncMock(),
        dropped_notifications=0,
    )
    session._connection = connection

    session._start_reauth()
    assert session._reauth_close_task is not None
    await session._reauth_close_task

    connection.async_close.assert_awaited_once()
    assert session._connection is None
    assert session.diagnostics.state.value == "reauth_required"
    entry.async_start_reauth.assert_called_once_with(hass)


async def test_three_login_timeouts_start_one_reauth(
    monkeypatch: pytest.MonkeyPatch,
    credentials: PairLinkCredentials,
) -> None:
    """Repeated authenticated-transport timeouts require fresh credentials."""

    class _TimeoutConnection:
        dropped_notifications = 0

        def __init__(self, *_args) -> None:
            pass

        async def async_connect_and_authenticate(self, _device) -> None:
            raise PairLinkLoginTimeout

        async def async_close(self) -> None:
            pass

    hass = MagicMock()
    entry = MagicMock(entry_id="entry-a")
    session = PairLinkSession(hass, entry, credentials)
    monkeypatch.setattr(
        "custom_components.pairlink.session.bluetooth.async_ble_device_from_address",
        lambda *_args, **_kwargs: SimpleNamespace(),
    )
    monkeypatch.setattr(
        "custom_components.pairlink.session.PairLinkConnection",
        _TimeoutConnection,
    )
    session._async_backoff = AsyncMock()

    await session._async_connection_loop()

    assert session.diagnostics.connection_attempts == 3
    entry.async_start_reauth.assert_called_once_with(hass)


async def test_sessions_serialize_connection_and_authentication(
    monkeypatch: pytest.MonkeyPatch,
    credentials: PairLinkCredentials,
) -> None:
    """Two sessions never start GATT setup concurrently on one adapter."""
    first_started = asyncio.Event()
    allow_first_to_finish = asyncio.Event()
    second_started = asyncio.Event()
    created = 0

    class _SerializedConnection:
        dropped_notifications = 0

        def __init__(self, *_args) -> None:
            nonlocal created
            created += 1
            self.number = created

        async def async_connect_and_authenticate(self, _device) -> None:
            if self.number == 1:
                first_started.set()
                await allow_first_to_finish.wait()
                raise PairLinkDisconnectedError
            second_started.set()
            raise PairLinkDisconnectedError

        async def async_close(self) -> None:
            pass

    monkeypatch.setattr(
        "custom_components.pairlink.session.bluetooth.async_ble_device_from_address",
        lambda *_args, **_kwargs: SimpleNamespace(),
    )
    monkeypatch.setattr(
        "custom_components.pairlink.session.PairLinkConnection",
        _SerializedConnection,
    )
    shared_lock = asyncio.Lock()
    sessions = [
        PairLinkSession(
            MagicMock(),
            MagicMock(entry_id=f"entry-{index}"),
            credentials,
            connection_lock=shared_lock,
        )
        for index in range(2)
    ]
    for session in sessions:
        session._async_backoff = AsyncMock(side_effect=asyncio.CancelledError)

    tasks = [
        asyncio.create_task(session._async_connection_loop()) for session in sessions
    ]
    await first_started.wait()
    await asyncio.sleep(0)
    assert not second_started.is_set()

    allow_first_to_finish.set()
    await second_started.wait()
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def test_stop_cancels_task_and_closes_connection(
    credentials: PairLinkCredentials,
) -> None:
    """Unload cleanup owns both the reconnect task and active client."""
    hass = MagicMock()
    entry = MagicMock(entry_id="entry-a")
    session = PairLinkSession(hass, entry, credentials)
    session._cancel_bluetooth_callback = MagicMock()
    connection = SimpleNamespace(
        async_close=AsyncMock(),
        dropped_notifications=2,
    )
    session._connection = connection
    session._connection_task = asyncio.create_task(asyncio.sleep(60))

    await session.async_stop()

    connection.async_close.assert_awaited_once()
    assert session.diagnostics.dropped_notification_count == 2
    assert session._connection_task is None
