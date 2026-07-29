"""PairLink Bluetooth advertisement parsing."""

from __future__ import annotations

import secrets
from collections.abc import Mapping
from typing import TYPE_CHECKING

from .const import (
    PAIRLINK_IDLE_ADVERTISEMENT,
    PAIRLINK_MARKER,
    PAIRLINK_REGISTRATION_ADVERTISEMENT,
)
from .models import PairLinkAdvertisement

if TYPE_CHECKING:
    from homeassistant.components.bluetooth import BluetoothServiceInfoBleak


def parse_manufacturer_value(value: bytes) -> PairLinkAdvertisement | None:
    """Parse one manufacturer value without relying on its company ID."""
    marker_index = value.find(PAIRLINK_MARKER)
    if marker_index < 0 or len(value) < marker_index + 3:
        return None
    data = value[marker_index:]
    adv_type = data[2]

    if adv_type == PAIRLINK_IDLE_ADVERTISEMENT:
        if len(data) < 13:
            return None
        return PairLinkAdvertisement(
            adv_type=adv_type,
            home_id=data[3:7],
            remote_id=data[7:13],
        )

    if adv_type == PAIRLINK_REGISTRATION_ADVERTISEMENT:
        if len(data) < 20:
            return None
        link_marker = data.find(b"\xf0\xfb", 11)
        if link_marker < 0 or len(data) < link_marker + 9:
            return None
        password = data[7:11]
        if not password.isascii():
            return None
        return PairLinkAdvertisement(
            adv_type=adv_type,
            home_id=data[3:7],
            password=password,
            remote_id=data[link_marker + 2 : link_marker + 8],
            remote_channel=data[link_marker + 8],
        )

    return None


def parse_manufacturer_data(
    manufacturer_data: Mapping[int, bytes],
) -> list[PairLinkAdvertisement]:
    """Parse every manufacturer value in an advertisement."""
    parsed: list[PairLinkAdvertisement] = []
    for value in manufacturer_data.values():
        if item := parse_manufacturer_value(bytes(value)):
            parsed.append(item)
    return parsed


def parse_service_info(
    service_info: BluetoothServiceInfoBleak,
) -> list[PairLinkAdvertisement]:
    """Parse PairLink data from Home Assistant Bluetooth service info."""
    return parse_manufacturer_data(service_info.manufacturer_data)


def find_advertisement(
    service_info: BluetoothServiceInfoBleak,
    *,
    expected_type: int | None = None,
    remote_id: bytes | None = None,
) -> PairLinkAdvertisement | None:
    """Find a complete advertisement matching the requested fields."""
    for advertisement in parse_service_info(service_info):
        if expected_type is not None and advertisement.adv_type != expected_type:
            continue
        if remote_id is not None and advertisement.remote_id != remote_id:
            continue
        if advertisement.is_complete_idle or advertisement.is_complete_registration:
            return advertisement
    return None


def remote_id_to_mac(remote_id: bytes) -> str:
    """Convert a six-byte little-endian remote ID to display MAC form."""
    if len(remote_id) != 6:
        raise ValueError("remote ID must be 6 bytes")
    return ":".join(f"{value:02X}" for value in reversed(remote_id))


def display_name(remote_id: bytes) -> str:
    """Return a privacy-conscious display name for a switch."""
    mac = remote_id_to_mac(remote_id)
    return f"PairLink switch {mac[-5:]}"


def generate_light_id() -> bytes:
    """Generate a random local/unicast identity in PairLink wire byte order."""
    canonical = bytearray(secrets.token_bytes(6))
    canonical[0] = (canonical[0] | 0x02) & 0xFE
    return bytes(reversed(canonical))
