"""Tests for vault master key startup behavior."""

import pytest
from cryptography.fernet import Fernet


def test_build_encryption_backend_requires_vault_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CUBEPLEX_AUTH__VAULT_KEY", raising=False)
    from cubeplex.config import config

    original_config_key = config.get("auth.vault_key")
    config.set("auth.vault_key", "")

    from cubeplex.api.app import _build_encryption_backend

    try:
        with pytest.raises(RuntimeError, match="CUBEPLEX_AUTH__VAULT_KEY is required"):
            _build_encryption_backend()
    finally:
        config.set("auth.vault_key", original_config_key)


def test_build_encryption_backend_uses_env_key(monkeypatch: pytest.MonkeyPatch) -> None:
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("CUBEPLEX_AUTH__VAULT_KEY", key)

    from cubeplex.api.app import _build_encryption_backend
    from cubeplex.credentials.encryption import FernetBackend

    backend = _build_encryption_backend()

    assert isinstance(backend, FernetBackend)


def test_parse_vault_keys_accepts_comma_separated_keys() -> None:
    key1 = Fernet.generate_key()
    key2 = Fernet.generate_key()

    from cubeplex.credentials.keys import parse_vault_keys

    assert parse_vault_keys(f"{key1.decode()}, {key2.decode()}") == [key1, key2]


def test_parse_vault_keys_rejects_invalid_key() -> None:
    from cubeplex.credentials.keys import parse_vault_keys

    with pytest.raises(ValueError, match="Invalid CUBEPLEX_AUTH__VAULT_KEY"):
        parse_vault_keys("not-a-fernet-key")
