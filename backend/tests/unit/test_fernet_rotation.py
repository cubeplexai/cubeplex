"""Unit tests for Fernet encryption + MultiFernet rotation."""

import pytest
from cryptography.fernet import Fernet, InvalidToken

from cubeplex.credentials.encryption import FernetBackend


@pytest.fixture
def k1() -> bytes:
    return Fernet.generate_key()


@pytest.fixture
def k2() -> bytes:
    return Fernet.generate_key()


async def test_roundtrip_single_key(k1: bytes) -> None:
    backend = FernetBackend([k1])
    plaintext = b"super-secret-token"
    ciphertext = await backend.encrypt(plaintext)
    assert ciphertext != plaintext
    assert await backend.decrypt(ciphertext) == plaintext


async def test_rotation_decrypts_old_ciphertext_with_new_key_first(k1: bytes, k2: bytes) -> None:
    """Encrypt with k1, then add k2 as new primary; old ciphertext must still decrypt."""
    old = FernetBackend([k1])
    cipher_old = await old.encrypt(b"hello")

    rotated = FernetBackend([k2, k1])
    assert await rotated.decrypt(cipher_old) == b"hello"


async def test_unknown_key_fails(k1: bytes, k2: bytes) -> None:
    old = FernetBackend([k1])
    cipher = await old.encrypt(b"hello")
    other = FernetBackend([k2])
    with pytest.raises(InvalidToken):
        await other.decrypt(cipher)


def test_empty_keys_rejected() -> None:
    with pytest.raises(ValueError, match="at least one"):
        FernetBackend([])
