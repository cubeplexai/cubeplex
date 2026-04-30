"""Symmetric authenticated encryption with pluggable backend.

CE default: Fernet (AES-128-CBC + HMAC-SHA256) via the cryptography library,
with MultiFernet for zero-downtime key rotation. EE may register a KMS-backed
EncryptionBackend without changing CredentialService callers.
"""

from typing import Protocol

from cryptography.fernet import Fernet, MultiFernet


class EncryptionBackend(Protocol):
    async def encrypt(self, plaintext: bytes) -> bytes: ...
    async def decrypt(self, ciphertext: bytes) -> bytes: ...


class FernetBackend:
    """CE default: Fernet + MultiFernet rotation. keys[0] encrypts; all decrypt."""

    def __init__(self, keys: list[bytes]) -> None:
        if not keys:
            raise ValueError("FernetBackend requires at least one key")
        self._fernet = MultiFernet([Fernet(k) for k in keys])

    async def encrypt(self, plaintext: bytes) -> bytes:
        return self._fernet.encrypt(plaintext)

    async def decrypt(self, ciphertext: bytes) -> bytes:
        return self._fernet.decrypt(ciphertext)
