"""Data models for the PairLink integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from .const import (
    CONF_HOME_ID,
    CONF_LIGHT_ID,
    CONF_PASSWORD,
    CONF_REMOTE_CHANNEL,
    CONF_REMOTE_ID,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

    from .session import PairLinkSession


class SessionState(StrEnum):
    """PairLink session states."""

    STOPPED = "stopped"
    RESOLVING = "resolving"
    CONNECTING = "connecting"
    AUTHENTICATING = "authenticating"
    READY = "ready"
    BACKOFF = "backoff"
    REAUTH_REQUIRED = "reauth_required"


@dataclass(frozen=True, repr=False)
class PairLinkAdvertisement:
    """Parsed PairLink manufacturer advertisement."""

    adv_type: int
    home_id: bytes | None = field(default=None, repr=False)
    password: bytes | None = field(default=None, repr=False)
    remote_id: bytes | None = field(default=None, repr=False)
    remote_channel: int | None = None

    @property
    def is_complete_idle(self) -> bool:
        """Return whether this is a complete type 0x05 advertisement."""
        return (
            self.adv_type == 0x05
            and self.home_id is not None
            and self.remote_id is not None
        )

    @property
    def is_complete_registration(self) -> bool:
        """Return whether this is a complete type 0x0d advertisement."""
        return (
            self.adv_type == 0x0D
            and self.home_id is not None
            and self.password is not None
            and self.remote_id is not None
            and self.remote_channel is not None
        )

    def __repr__(self) -> str:
        """Return a representation that cannot expose advertisement secrets."""
        return (
            f"PairLinkAdvertisement(adv_type=0x{self.adv_type:02x}, "
            f"complete={self.is_complete_idle or self.is_complete_registration})"
        )


@dataclass(frozen=True, repr=False)
class PairLinkCredentials:
    """Validated configuration needed for one PairLink session."""

    address: str = field(repr=False)
    remote_id: bytes = field(repr=False)
    home_id: bytes = field(repr=False)
    password: bytes = field(repr=False)
    remote_channel: int
    light_id: bytes = field(repr=False)

    def __post_init__(self) -> None:
        """Validate credential field sizes."""
        if not self.address:
            raise ValueError("address is required")
        if len(self.remote_id) != 6:
            raise ValueError("remote ID must be 6 bytes")
        if len(self.home_id) != 4:
            raise ValueError("home ID must be 4 bytes")
        if len(self.password) != 4 or not self.password.isascii():
            raise ValueError("password must be four ASCII bytes")
        if not 0 <= self.remote_channel <= 0xFF:
            raise ValueError("remote channel must fit in one byte")
        if len(self.light_id) != 6:
            raise ValueError("light ID must be 6 bytes")

    @classmethod
    def from_entry_data(cls, data: dict[str, object]) -> PairLinkCredentials:
        """Create credentials from Config Entry data."""
        address = data.get("address")
        password = data.get(CONF_PASSWORD)
        remote_channel = data.get(CONF_REMOTE_CHANNEL)
        if not isinstance(address, str):
            raise ValueError("invalid address")
        if not isinstance(password, str):
            raise ValueError("invalid password")
        if not isinstance(remote_channel, int):
            raise ValueError("invalid remote channel")
        try:
            password_bytes = password.encode("ascii")
            return cls(
                address=address,
                remote_id=bytes.fromhex(_require_string(data, CONF_REMOTE_ID)),
                home_id=bytes.fromhex(_require_string(data, CONF_HOME_ID)),
                password=password_bytes,
                remote_channel=remote_channel,
                light_id=bytes.fromhex(_require_string(data, CONF_LIGHT_ID)),
            )
        except (UnicodeEncodeError, ValueError) as err:
            raise ValueError("invalid PairLink configuration") from err

    def as_entry_data(self) -> dict[str, str | int]:
        """Return serializable Config Entry data."""
        return {
            "address": self.address,
            CONF_REMOTE_ID: self.remote_id.hex(),
            CONF_HOME_ID: self.home_id.hex(),
            CONF_PASSWORD: self.password.decode("ascii"),
            CONF_REMOTE_CHANNEL: self.remote_channel,
            CONF_LIGHT_ID: self.light_id.hex(),
        }

    def __repr__(self) -> str:
        """Return a representation that cannot expose credentials."""
        return "PairLinkCredentials(<redacted>)"


def _require_string(data: dict[str, object], key: str) -> str:
    """Return a required string Config Entry value."""
    value = data.get(key)
    if not isinstance(value, str):
        raise ValueError(f"invalid {key}")
    return value


@dataclass(frozen=True, repr=False)
class LoginResponse:
    """Validated LOGIN response."""

    peer_crypto_vaddr: bytes = field(repr=False)

    def __repr__(self) -> str:
        """Return a representation that cannot expose session material."""
        return "LoginResponse(<redacted>)"


@dataclass(frozen=True, repr=False)
class RemoteEvent:
    """Decoded PairLink Remote event."""

    destination_vaddr: bytes = field(repr=False)
    source_vaddr: bytes = field(repr=False)
    remote_id: bytes = field(repr=False)
    remote_channel: int
    command: int
    extra: bytes = field(repr=False)

    def __repr__(self) -> str:
        """Return a representation without stable device identifiers."""
        return (
            "RemoteEvent("
            f"remote_channel={self.remote_channel}, command=0x{self.command:02x}, "
            f"extra_length={len(self.extra)})"
        )


@dataclass
class SessionDiagnostics:
    """Non-secret diagnostic state for a PairLink session."""

    state: SessionState = SessionState.STOPPED
    connected: bool = False
    rssi: int | None = None
    last_ready_at: datetime | None = None
    last_event_at: datetime | None = None
    connection_attempts: int = 0
    disconnect_count: int = 0
    decoded_event_count: int = 0
    duplicate_count: int = 0
    unknown_packet_count: int = 0
    dropped_notification_count: int = 0

    def as_dict(self) -> dict[str, str | int | bool | None]:
        """Return serializable non-secret diagnostics."""
        return {
            "state": self.state,
            "connected": self.connected,
            "rssi": self.rssi,
            "last_ready_at": _isoformat(self.last_ready_at),
            "last_event_at": _isoformat(self.last_event_at),
            "connection_attempts": self.connection_attempts,
            "disconnect_count": self.disconnect_count,
            "decoded_event_count": self.decoded_event_count,
            "duplicate_count": self.duplicate_count,
            "unknown_packet_count": self.unknown_packet_count,
            "dropped_notification_count": self.dropped_notification_count,
        }


def _isoformat(value: datetime | None) -> str | None:
    """Convert a timestamp to a JSON-safe string."""
    return value.isoformat() if value is not None else None


if TYPE_CHECKING:
    PairLinkConfigEntry = ConfigEntry[PairLinkSession]
else:
    PairLinkConfigEntry = Any
EventCallback = Callable[[RemoteEvent, int], None]
StateCallback = Callable[[], None]
