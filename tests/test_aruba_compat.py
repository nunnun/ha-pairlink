"""Tests for the narrowly scoped Aruba active-GATT compatibility layer."""

from __future__ import annotations

import sys
from types import ModuleType

from custom_components.pairlink.aruba_compat import (
    _native_pairlink_uuid_encoder,
    enable_aruba_uuid_compat,
)


def test_only_pairlink_and_health_uuids_use_native_16_bit_form() -> None:
    """Unrelated vendor UUIDs must retain the Aruba encoder's behavior."""
    calls: list[str | None] = []

    def original(value: str | None) -> bytes:
        calls.append(value)
        return b"original"

    encoder = _native_pairlink_uuid_encoder(original)

    assert encoder("FFD1") == bytes.fromhex("ffd1")
    assert encoder("00002A00-0000-1000-8000-00805F9B34FB") == bytes.fromhex("2a00")
    assert encoder("12345678-1234-5678-1234-567812345678") == b"original"
    assert calls == ["12345678-1234-5678-1234-567812345678"]


def test_affected_aruba_encoder_is_patched_once(monkeypatch) -> None:
    """The runtime hook is safe to call from setup and config flow."""
    package = ModuleType("custom_components.aruba_ble_proxy")
    package.__path__ = []
    iot_package = ModuleType("custom_components.aruba_ble_proxy.aruba_iot_ble")
    iot_package.__path__ = []
    active = ModuleType("custom_components.aruba_ble_proxy.aruba_iot_ble.active")

    def affected_encoder(value: str | None) -> bytes:
        compact = str(value).replace("-", "")
        if len(compact) == 4:
            compact = f"0000{compact}00001000800000805f9b34fb"
        return bytes.fromhex(compact)

    active.uuid_to_bytes = affected_encoder
    iot_package.active = active
    monkeypatch.setitem(sys.modules, package.__name__, package)
    monkeypatch.setitem(sys.modules, iot_package.__name__, iot_package)
    monkeypatch.setitem(sys.modules, active.__name__, active)

    assert enable_aruba_uuid_compat()
    assert active.uuid_to_bytes("ffd2") == bytes.fromhex("ffd2")
    assert not enable_aruba_uuid_compat()
