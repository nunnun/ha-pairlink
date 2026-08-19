"""Narrow compatibility support for Aruba's active GATT transport."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

_LOGGER = logging.getLogger(__name__)

# AOS matches Bluetooth SIG 16-bit UUIDs by their native two-byte wire form.
# aruba-ble-proxy 1.1.1 expands these values to the 128-bit Bluetooth base UUID,
# which AOS 8.13 then rejects as characteristicNotFound. Limit the workaround to
# the UUIDs used by this integration; unrelated Aruba clients remain untouched.
_PAIRLINK_SHORT_UUIDS = {
    "1800",
    "2a00",
    "ffd0",
    "ffd1",
    "ffd2",
}
_BLUETOOTH_BASE_SUFFIX = "00001000800000805f9b34fb"
_PATCH_MARKER = "_pairlink_native_16bit_uuids"


def enable_aruba_uuid_compat() -> bool:
    """Patch an affected Aruba encoder, returning whether it was changed."""
    try:
        from custom_components.aruba_ble_proxy.aruba_iot_ble import active
    except ImportError:
        return False

    encoder = getattr(active, "uuid_to_bytes", None)
    if not callable(encoder) or getattr(encoder, _PATCH_MARKER, False):
        return False
    try:
        if encoder("ffd0") == bytes.fromhex("ffd0"):
            return False
    except TypeError, ValueError:
        return False

    active.uuid_to_bytes = _native_pairlink_uuid_encoder(encoder)
    _LOGGER.info("Enabled native 16-bit UUID encoding for Aruba PairLink GATT")
    return True


def _native_pairlink_uuid_encoder(
    original: Callable[[str | None], bytes],
) -> Callable[[str | None], bytes]:
    """Wrap one Aruba UUID encoder without changing unrelated UUIDs."""

    def _encode(value: str | None) -> bytes:
        short = _pairlink_short_uuid(value)
        if short is not None:
            return bytes.fromhex(short)
        return original(value)

    setattr(_encode, _PATCH_MARKER, True)
    return _encode


def _pairlink_short_uuid(value: Any) -> str | None:
    if value is None:
        return None
    compact = str(value).strip().lower().replace("{", "").replace("}", "")
    compact = compact.replace("-", "")
    if compact in _PAIRLINK_SHORT_UUIDS:
        return compact
    if (
        len(compact) == 32
        and compact.startswith("0000")
        and compact[8:] == _BLUETOOTH_BASE_SUFFIX
        and compact[4:8] in _PAIRLINK_SHORT_UUIDS
    ):
        return compact[4:8]
    return None
