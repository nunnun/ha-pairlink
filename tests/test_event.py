"""Tests for the PairLink Event Entity."""

from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.pairlink.event import PairLinkButtonEvent
from custom_components.pairlink.models import PairLinkCredentials, RemoteEvent


def test_event_entity_publishes_on_with_attributes() -> None:
    """The entity converts a decoded command to an Event Entity state."""
    credentials = PairLinkCredentials(
        address="12:34:56:78:9A:BC",
        remote_id=bytes.fromhex("bc9a78563412"),
        home_id=bytes.fromhex("11223344"),
        password=b"TEST",
        remote_channel=1,
        light_id=bytes.fromhex("665544332211"),
    )
    session = MagicMock(credentials=credentials, available=True)
    entry = MagicMock(runtime_data=session, title="PairLink switch 9A:BC")
    entity = PairLinkButtonEvent(entry)
    entity.entity_id = "event.pairlink_button"
    entity.async_write_ha_state = MagicMock()
    event = RemoteEvent(
        destination_vaddr=b"\xff" * 4,
        source_vaddr=bytes(4),
        remote_id=credentials.remote_id,
        remote_channel=1,
        command=1,
        extra=b"\x00",
    )

    entity._handle_event(event, 1)

    assert entity.state_attributes == {
        "event_type": "on",
        "channel": 1,
        "command": 1,
        "command_hex": "0x01",
        "extra": "00",
        "repeat_count": 1,
    }
    entity.async_write_ha_state.assert_called_once()
    assert entity.available
