"""Tests for sensitive PairLink data models."""

from __future__ import annotations

import pytest

from custom_components.pairlink.models import PairLinkCredentials

ENTRY_DATA = {
    "address": "12:34:56:78:9A:BC",
    "remote_id": "bc9a78563412",
    "home_id": "11223344",
    "password": "TEST",
    "remote_channel": 1,
    "light_id": "665544332211",
}


def test_credentials_round_trip_and_safe_repr() -> None:
    """Config Entry serialization must round-trip without repr leakage."""
    credentials = PairLinkCredentials.from_entry_data(ENTRY_DATA)
    assert credentials.as_entry_data() == ENTRY_DATA
    rendered = repr(credentials)
    for secret in ("TEST", "11223344", "12:34:56:78:9A:BC"):
        assert secret not in rendered


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("remote_id", "00"),
        ("home_id", "00"),
        ("password", "éééé"),
        ("remote_channel", 300),
        ("light_id", "00"),
    ],
)
def test_invalid_entry_data(key: str, value: object) -> None:
    """Every persisted protocol field is validated at the boundary."""
    data = {**ENTRY_DATA, key: value}
    with pytest.raises(ValueError, match="invalid PairLink configuration"):
        PairLinkCredentials.from_entry_data(data)
