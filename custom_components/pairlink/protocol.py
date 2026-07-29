"""Self-contained pure-Python PairLink protocol codec."""

from __future__ import annotations

from .models import LoginResponse, RemoteEvent

_SBOX = (
    0x63,
    0x7C,
    0x77,
    0x7B,
    0xF2,
    0x6B,
    0x6F,
    0xC5,
    0x30,
    0x01,
    0x67,
    0x2B,
    0xFE,
    0xD7,
    0xAB,
    0x76,
    0xCA,
    0x82,
    0xC9,
    0x7D,
    0xFA,
    0x59,
    0x47,
    0xF0,
    0xAD,
    0xD4,
    0xA2,
    0xAF,
    0x9C,
    0xA4,
    0x72,
    0xC0,
    0xB7,
    0xFD,
    0x93,
    0x26,
    0x36,
    0x3F,
    0xF7,
    0xCC,
    0x34,
    0xA5,
    0xE5,
    0xF1,
    0x71,
    0xD8,
    0x31,
    0x15,
    0x04,
    0xC7,
    0x23,
    0xC3,
    0x18,
    0x96,
    0x05,
    0x9A,
    0x07,
    0x12,
    0x80,
    0xE2,
    0xEB,
    0x27,
    0xB2,
    0x75,
    0x09,
    0x83,
    0x2C,
    0x1A,
    0x1B,
    0x6E,
    0x5A,
    0xA0,
    0x52,
    0x3B,
    0xD6,
    0xB3,
    0x29,
    0xE3,
    0x2F,
    0x84,
    0x53,
    0xD1,
    0x00,
    0xED,
    0x20,
    0xFC,
    0xB1,
    0x5B,
    0x6A,
    0xCB,
    0xBE,
    0x39,
    0x4A,
    0x4C,
    0x58,
    0xCF,
    0xD0,
    0xEF,
    0xAA,
    0xFB,
    0x43,
    0x4D,
    0x33,
    0x85,
    0x45,
    0xF9,
    0x02,
    0x7F,
    0x50,
    0x3C,
    0x9F,
    0xA8,
    0x51,
    0xA3,
    0x40,
    0x8F,
    0x92,
    0x9D,
    0x38,
    0xF5,
    0xBC,
    0xB6,
    0xDA,
    0x21,
    0x10,
    0xFF,
    0xF3,
    0xD2,
    0xCD,
    0x0C,
    0x13,
    0xEC,
    0x5F,
    0x97,
    0x44,
    0x17,
    0xC4,
    0xA7,
    0x7E,
    0x3D,
    0x64,
    0x5D,
    0x19,
    0x73,
    0x60,
    0x81,
    0x4F,
    0xDC,
    0x22,
    0x2A,
    0x90,
    0x88,
    0x46,
    0xEE,
    0xB8,
    0x14,
    0xDE,
    0x5E,
    0x0B,
    0xDB,
    0xE0,
    0x32,
    0x3A,
    0x0A,
    0x49,
    0x06,
    0x24,
    0x5C,
    0xC2,
    0xD3,
    0xAC,
    0x62,
    0x91,
    0x95,
    0xE4,
    0x79,
    0xE7,
    0xC8,
    0x37,
    0x6D,
    0x8D,
    0xD5,
    0x4E,
    0xA9,
    0x6C,
    0x56,
    0xF4,
    0xEA,
    0x65,
    0x7A,
    0xAE,
    0x08,
    0xBA,
    0x78,
    0x25,
    0x2E,
    0x1C,
    0xA6,
    0xB4,
    0xC6,
    0xE8,
    0xDD,
    0x74,
    0x1F,
    0x4B,
    0xBD,
    0x8B,
    0x8A,
    0x70,
    0x3E,
    0xB5,
    0x66,
    0x48,
    0x03,
    0xF6,
    0x0E,
    0x61,
    0x35,
    0x57,
    0xB9,
    0x86,
    0xC1,
    0x1D,
    0x9E,
    0xE1,
    0xF8,
    0x98,
    0x11,
    0x69,
    0xD9,
    0x8E,
    0x94,
    0x9B,
    0x1E,
    0x87,
    0xE9,
    0xCE,
    0x55,
    0x28,
    0xDF,
    0x8C,
    0xA1,
    0x89,
    0x0D,
    0xBF,
    0xE6,
    0x42,
    0x68,
    0x41,
    0x99,
    0x2D,
    0x0F,
    0xB0,
    0x54,
    0xBB,
    0x16,
)
_INV_SBOX = tuple(_SBOX.index(value) for value in range(256))
_RCON = (0x00, 0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36)


def _bytes_to_columns(block: bytes) -> list[list[int]]:
    return [list(block[offset : offset + 4]) for offset in range(0, len(block), 4)]


def _columns_to_bytes(columns: list[list[int]]) -> bytes:
    return bytes(value for column in columns for value in column)


def _add_round_key(state: list[list[int]], key: list[list[int]]) -> None:
    for column in range(4):
        for row in range(4):
            state[column][row] ^= key[column][row]


def _sub_bytes(state: list[list[int]]) -> None:
    for column in range(4):
        for row in range(4):
            state[column][row] = _SBOX[state[column][row]]


def _inv_sub_bytes(state: list[list[int]]) -> None:
    for column in range(4):
        for row in range(4):
            state[column][row] = _INV_SBOX[state[column][row]]


def _shift_rows(state: list[list[int]]) -> None:
    state[0][1], state[1][1], state[2][1], state[3][1] = (
        state[1][1],
        state[2][1],
        state[3][1],
        state[0][1],
    )
    state[0][2], state[1][2], state[2][2], state[3][2] = (
        state[2][2],
        state[3][2],
        state[0][2],
        state[1][2],
    )
    state[0][3], state[1][3], state[2][3], state[3][3] = (
        state[3][3],
        state[0][3],
        state[1][3],
        state[2][3],
    )


def _inv_shift_rows(state: list[list[int]]) -> None:
    state[0][1], state[1][1], state[2][1], state[3][1] = (
        state[3][1],
        state[0][1],
        state[1][1],
        state[2][1],
    )
    state[0][2], state[1][2], state[2][2], state[3][2] = (
        state[2][2],
        state[3][2],
        state[0][2],
        state[1][2],
    )
    state[0][3], state[1][3], state[2][3], state[3][3] = (
        state[1][3],
        state[2][3],
        state[3][3],
        state[0][3],
    )


def _xtime(value: int) -> int:
    return ((value << 1) ^ (0x1B if value & 0x80 else 0)) & 0xFF


def _mix_column(column: list[int]) -> None:
    total = column[0] ^ column[1] ^ column[2] ^ column[3]
    first = column[0]
    column[0] ^= total ^ _xtime(column[0] ^ column[1])
    column[1] ^= total ^ _xtime(column[1] ^ column[2])
    column[2] ^= total ^ _xtime(column[2] ^ column[3])
    column[3] ^= total ^ _xtime(column[3] ^ first)


def _mix_columns(state: list[list[int]]) -> None:
    for column in state:
        _mix_column(column)


def _inv_mix_columns(state: list[list[int]]) -> None:
    for column in state:
        first = _xtime(_xtime(column[0] ^ column[2]))
        second = _xtime(_xtime(column[1] ^ column[3]))
        column[0] ^= first
        column[1] ^= second
        column[2] ^= first
        column[3] ^= second
    _mix_columns(state)


class AES128:
    """Minimal AES-128 block cipher used for low-volume PairLink packets."""

    block_size = 16
    rounds = 10

    def __init__(self, key: bytes) -> None:
        if len(key) != 16:
            raise ValueError("AES-128 key must be 16 bytes")
        columns = _bytes_to_columns(key)
        rcon_index = 1
        while len(columns) < 4 * (self.rounds + 1):
            word = columns[-1].copy()
            if len(columns) % 4 == 0:
                word.append(word.pop(0))
                word = [_SBOX[value] for value in word]
                word[0] ^= _RCON[rcon_index]
                rcon_index += 1
            word = [
                value ^ previous
                for value, previous in zip(word, columns[-4], strict=False)
            ]
            columns.append(word)
        self._round_keys = [columns[index : index + 4] for index in range(0, 44, 4)]

    def encrypt_block(self, block: bytes) -> bytes:
        """Encrypt one 16-byte block."""
        if len(block) != 16:
            raise ValueError("AES block must be 16 bytes")
        state = _bytes_to_columns(block)
        _add_round_key(state, self._round_keys[0])
        for round_index in range(1, self.rounds):
            _sub_bytes(state)
            _shift_rows(state)
            _mix_columns(state)
            _add_round_key(state, self._round_keys[round_index])
        _sub_bytes(state)
        _shift_rows(state)
        _add_round_key(state, self._round_keys[-1])
        return _columns_to_bytes(state)

    def decrypt_block(self, block: bytes) -> bytes:
        """Decrypt one 16-byte block."""
        if len(block) != 16:
            raise ValueError("AES block must be 16 bytes")
        state = _bytes_to_columns(block)
        _add_round_key(state, self._round_keys[-1])
        _inv_shift_rows(state)
        _inv_sub_bytes(state)
        for round_index in range(self.rounds - 1, 0, -1):
            _add_round_key(state, self._round_keys[round_index])
            _inv_mix_columns(state)
            _inv_shift_rows(state)
            _inv_sub_bytes(state)
        _add_round_key(state, self._round_keys[0])
        return _columns_to_bytes(state)


def _ecb_encrypt(cipher: AES128, plaintext: bytes) -> bytes:
    if len(plaintext) % AES128.block_size:
        raise ValueError("ECB input length must be a multiple of 16")
    return b"".join(
        cipher.encrypt_block(plaintext[offset : offset + 16])
        for offset in range(0, len(plaintext), 16)
    )


def _ecb_decrypt(cipher: AES128, ciphertext: bytes) -> bytes:
    if len(ciphertext) % AES128.block_size:
        raise ValueError("ECB input length must be a multiple of 16")
    return b"".join(
        cipher.decrypt_block(ciphertext[offset : offset + 16])
        for offset in range(0, len(ciphertext), 16)
    )


def _pkcs7_pad(data: bytes) -> bytes:
    padding = AES128.block_size - len(data) % AES128.block_size
    return data + bytes([padding]) * padding


def _pkcs7_unpad(data: bytes) -> bytes:
    if not data or len(data) % AES128.block_size:
        raise ValueError("invalid PKCS#7 block length")
    padding = data[-1]
    if not 1 <= padding <= AES128.block_size:
        raise ValueError("invalid PKCS#7 padding length")
    if data[-padding:] != bytes([padding]) * padding:
        raise ValueError("invalid PKCS#7 padding bytes")
    return data[:-padding]


def _xor_repeating(data: bytes, mask: bytes) -> bytes:
    if not mask:
        raise ValueError("XOR mask must not be empty")
    return bytes(value ^ mask[index % len(mask)] for index, value in enumerate(data))


class PairLinkCodec:
    """Codec for PairLink's fixed-four-character-password protocol."""

    def __init__(self, home_id: bytes, password: bytes) -> None:
        if len(home_id) != 4:
            raise ValueError("home ID must be 4 bytes")
        if len(password) != 4:
            raise ValueError("password must be 4 bytes")
        self._home_id = home_id
        self._password = password
        interleaved = bytes(
            value for pair in zip(home_id, password, strict=False) for value in pair
        )
        self._login_aes = AES128(interleaved + b"LOGINKEY")
        self._event_aes = AES128(interleaved + b"EVENTKEY")

    def make_login(self, own_vaddr: bytes = bytes(4)) -> bytes:
        """Create a wire-format LOGIN request."""
        if len(own_vaddr) != 4:
            raise ValueError("own virtual address must be 4 bytes")
        plaintext = self._home_id + self._password + own_vaddr + bytes([4]) * 4
        return b"\x01\x19" + self._login_aes.encrypt_block(plaintext)

    def parse_login_response(self, packet: bytes) -> LoginResponse:
        """Validate and parse a wire-format LOGIN response."""
        if len(packet) != 18 or packet[:2] != b"\x02\x19":
            raise ValueError("invalid LOGIN response")
        plaintext = self._login_aes.decrypt_block(packet[2:])
        try:
            unpadded = _pkcs7_unpad(plaintext)
        except ValueError as err:
            raise ValueError("invalid LOGIN response") from err
        if len(unpadded) != 12:
            raise ValueError("invalid LOGIN response length")
        if unpadded[:4] != self._home_id or unpadded[4:8] != self._password:
            raise ValueError("LOGIN response credentials do not match")
        return LoginResponse(peer_crypto_vaddr=unpadded[8:12])

    def decrypt_data_event(self, packet: bytes, peer_crypto_vaddr: bytes) -> bytes:
        """Decrypt a category-6 event to normalized category-3 plaintext."""
        if len(peer_crypto_vaddr) != 4:
            raise ValueError("peer virtual address must be 4 bytes")
        if len(packet) < 22 or packet[0] != 0x06:
            raise ValueError("invalid encrypted data event")
        masked_ciphertext = packet[6:]
        if len(masked_ciphertext) % AES128.block_size:
            raise ValueError("invalid encrypted data event length")
        ciphertext = _xor_repeating(masked_ciphertext, peer_crypto_vaddr)
        payload = _pkcs7_unpad(_ecb_decrypt(self._event_aes, ciphertext))
        return b"\x03" + packet[1:6] + payload

    def encrypt_data_event(self, packet: bytes, peer_crypto_vaddr: bytes) -> bytes:
        """Encrypt normalized category-3 plaintext for test-vector verification."""
        if len(peer_crypto_vaddr) != 4:
            raise ValueError("peer virtual address must be 4 bytes")
        if len(packet) < 6 or packet[0] != 0x03:
            raise ValueError("invalid plaintext data event")
        ciphertext = _ecb_encrypt(self._event_aes, _pkcs7_pad(packet[6:]))
        return b"\x06" + packet[1:6] + _xor_repeating(ciphertext, peer_crypto_vaddr)

    @staticmethod
    def parse_remote_event(packet: bytes) -> RemoteEvent:
        """Parse a normalized Remote channel event."""
        if len(packet) < 19 or packet[0] != 0x03:
            raise ValueError("invalid plaintext data event")
        if packet[5] != 0x21 or packet[10] != 0x52:
            raise ValueError("unsupported PairLink data event")
        return RemoteEvent(
            destination_vaddr=packet[1:5],
            source_vaddr=packet[6:10],
            remote_id=packet[11:17],
            remote_channel=packet[17],
            command=packet[18],
            extra=packet[19:],
        )
