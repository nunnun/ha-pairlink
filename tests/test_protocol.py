"""Tests for the self-contained PairLink codec."""

from __future__ import annotations

import pytest

from custom_components.pairlink.protocol import AES128, PairLinkCodec


def test_fips_197_known_answer() -> None:
    """AES must match the official FIPS-197 vector."""
    cipher = AES128(bytes.fromhex("000102030405060708090a0b0c0d0e0f"))
    plaintext = bytes.fromhex("00112233445566778899aabbccddeeff")
    ciphertext = bytes.fromhex("69c4e0d86a7b0430d8cdb78070b4c55a")
    assert cipher.encrypt_block(plaintext) == ciphertext
    assert cipher.decrypt_block(ciphertext) == plaintext


def test_login_and_remote_event_vectors() -> None:
    """LOGIN, ON, and OFF must match the anonymized captured vectors."""
    codec = PairLinkCodec(bytes.fromhex("11223344"), b"TEST")
    peer = bytes.fromhex("a1b2c3d4")

    assert codec.make_login().hex() == ("0119d27abad16ba4e0969dac065916eb085b")
    response = codec.parse_login_response(
        bytes.fromhex("0219999cd93132539da135a6c3213d4665db")
    )
    assert response.peer_crypto_vaddr == peer

    vectors = (
        (
            "06ffffffff210d4073c52f4a9e587b295f58878c714a",
            "03ffffffff210000000052bc9a78563412010100",
            0x01,
        ),
        (
            "06ffffffff211b2b9ff7fe8c270d9841d9c6f5b60ac8",
            "03ffffffff210000000052bc9a78563412010200",
            0x02,
        ),
    )
    for wire_hex, plaintext_hex, command in vectors:
        wire = bytes.fromhex(wire_hex)
        plaintext = codec.decrypt_data_event(wire, peer)
        assert plaintext.hex() == plaintext_hex
        event = codec.parse_remote_event(plaintext)
        assert event.remote_id.hex() == "bc9a78563412"
        assert event.remote_channel == 1
        assert event.command == command
        assert event.extra == b"\x00"
        assert codec.encrypt_data_event(plaintext, peer) == wire


@pytest.mark.parametrize(
    ("home_id", "password"),
    [
        (b"\x00" * 3, b"TEST"),
        (b"\x00" * 4, b"BAD"),
    ],
)
def test_rejects_invalid_credentials(home_id: bytes, password: bytes) -> None:
    """Credential lengths are protocol invariants."""
    with pytest.raises(ValueError):
        PairLinkCodec(home_id, password)


def test_rejects_invalid_login_response_without_exposing_secret() -> None:
    """Wrong LOGIN material must fail with a generic exception."""
    codec = PairLinkCodec(bytes.fromhex("11223344"), b"TEST")
    with pytest.raises(ValueError, match="LOGIN response"):
        codec.parse_login_response(b"\x02\x19" + bytes(16))


@pytest.mark.parametrize(
    "packet",
    [
        b"",
        b"\x06" + bytes(20),
        b"\x05" + bytes(21),
        b"\x06" + bytes(22),
    ],
)
def test_rejects_malformed_encrypted_events(packet: bytes) -> None:
    """Malformed category-6 packets must never be partially decoded."""
    codec = PairLinkCodec(bytes.fromhex("11223344"), b"TEST")
    with pytest.raises(ValueError):
        codec.decrypt_data_event(packet, bytes.fromhex("a1b2c3d4"))


def test_rejects_non_remote_plaintext() -> None:
    """Only channel-0x21 Remote payloads are supported."""
    with pytest.raises(ValueError, match="unsupported"):
        PairLinkCodec.parse_remote_event(
            bytes.fromhex("03ffffffff220000000052bc9a78563412010100")
        )
