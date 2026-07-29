"""Tests for PairLink advertisement parsing and identities."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.pairlink.discovery import (
    find_advertisement,
    generate_light_id,
    parse_manufacturer_data,
    parse_manufacturer_value,
    remote_id_to_mac,
)


def test_idle_advertisement() -> None:
    """Parse a complete type-0x05 advertisement."""
    parsed = parse_manufacturer_value(bytes.fromhex("c0ff0511223344bc9a78563412"))
    assert parsed is not None
    assert parsed.is_complete_idle
    assert parsed.home_id == bytes.fromhex("11223344")
    assert parsed.remote_id == bytes.fromhex("bc9a78563412")
    assert parsed.password is None


def test_registration_advertisement() -> None:
    """Parse complete credentials without including them in repr."""
    parsed = parse_manufacturer_value(
        bytes.fromhex("c0ff0d1122334454455354a3f0fbbc9a7856341201")
    )
    assert parsed is not None
    assert parsed.is_complete_registration
    assert parsed.home_id == bytes.fromhex("11223344")
    assert parsed.password == b"TEST"
    assert parsed.remote_id == bytes.fromhex("bc9a78563412")
    assert parsed.remote_channel == 1
    assert "TEST" not in repr(parsed)
    assert "11223344" not in repr(parsed)


def test_company_prefix_and_multiple_values() -> None:
    """Company ID bytes and unrelated values must not affect parsing."""
    parsed = parse_manufacturer_data(
        {
            1: b"unrelated",
            65535: bytes.fromhex("ffffc0ff0511223344bc9a78563412"),
        }
    )
    assert len(parsed) == 1
    assert parsed[0].is_complete_idle


@pytest.mark.parametrize(
    "value",
    [
        b"",
        b"\xc0\xff",
        bytes.fromhex("c0ff051122"),
        bytes.fromhex("c0ff0d1122334454455354"),
        bytes.fromhex("c0ff0d11223344ffffffffa3f0fbbc9a7856341201"),
        bytes.fromhex("c0ff9911223344bc9a78563412"),
    ],
)
def test_malformed_advertisements_are_ignored(value: bytes) -> None:
    """Truncated, non-ASCII, and unknown advertisements are ignored."""
    assert parse_manufacturer_value(value) is None


def test_find_advertisement_filters_type_and_remote() -> None:
    """Service-info filtering must isolate the target switch."""
    service_info = SimpleNamespace(
        manufacturer_data={65535: bytes.fromhex("c0ff0511223344bc9a78563412")}
    )
    remote_id = bytes.fromhex("bc9a78563412")
    assert (
        find_advertisement(
            service_info,
            expected_type=0x05,
            remote_id=remote_id,
        )
        is not None
    )
    assert (
        find_advertisement(
            service_info,
            expected_type=0x0D,
            remote_id=remote_id,
        )
        is None
    )


def test_identity_helpers() -> None:
    """Generated receiver identities are valid local unicast identities."""
    assert remote_id_to_mac(bytes.fromhex("bc9a78563412")) == ("12:34:56:78:9A:BC")
    generated = generate_light_id()
    assert len(generated) == 6
    canonical_first = generated[-1]
    assert canonical_first & 0x01 == 0
    assert canonical_first & 0x02 == 0x02
