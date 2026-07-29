"""Tests for the PairLink signal-strength Sensor Entity."""

from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.pairlink.models import PairLinkCredentials, SessionDiagnostics
from custom_components.pairlink.sensor import PairLinkSignalStrengthSensor


def test_signal_strength_sensor_exposes_latest_rssi() -> None:
    """The sensor exposes advertisement RSSI as a diagnostic measurement."""
    credentials = PairLinkCredentials(
        address="12:34:56:78:9A:BC",
        remote_id=bytes.fromhex("bc9a78563412"),
        home_id=bytes.fromhex("11223344"),
        password=b"TEST",
        remote_channel=1,
        light_id=bytes.fromhex("665544332211"),
    )
    diagnostics = SessionDiagnostics()
    session = MagicMock(credentials=credentials, diagnostics=diagnostics)
    entry = MagicMock(runtime_data=session, title="PairLink switch 9A:BC")
    entity = PairLinkSignalStrengthSensor(entry)

    assert entity.native_value is None
    assert not entity.available

    diagnostics.rssi = -87

    assert entity.native_value == -87
    assert entity.available
    assert entity.native_unit_of_measurement == "dBm"
