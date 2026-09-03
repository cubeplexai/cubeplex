"""Tests for vault master key startup behavior."""

import pytest
from cryptography.fernet import Fernet


@pytest.mark.parametrize(
    ("setting", "value", "message"),
    [
        ("auth.jwt_secret", "REPLACE_ME", "placeholder"),
        ("auth.jwt_secret", "CHANGE_ME_IN_PRODUCTION_NOT_SECURE", "placeholder"),
        ("auth.csrf_secret", "USE ENV", "placeholder"),
        ("auth.jwt_secret", "too-short", "at least 32 characters"),
        ("auth.csrf_secret", "", "is required"),
    ],
)
def test_validate_auth_secrets_rejects_unsafe_production_values(
    monkeypatch: pytest.MonkeyPatch,
    setting: str,
    value: str,
    message: str,
) -> None:
    monkeypatch.setenv("ENV_FOR_DYNACONF", "production")
    from cubeplex.config import config

    original_value = config.get(setting)
    original_jwt_secret = config.get("auth.jwt_secret")
    original_csrf_secret = config.get("auth.csrf_secret")
    config.set("auth.jwt_secret", "a" * 32)
    config.set("auth.csrf_secret", "b" * 32)
    config.set(setting, value)

    from cubeplex.api.app import validate_auth_secrets

    try:
        with pytest.raises(RuntimeError, match=message):
            validate_auth_secrets()
    finally:
        config.set(setting, original_value)
        config.set("auth.jwt_secret", original_jwt_secret)
        config.set("auth.csrf_secret", original_csrf_secret)


def test_validate_auth_secrets_allows_test_profile_placeholders(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENV_FOR_DYNACONF", "test")

    from cubeplex.api.app import validate_auth_secrets

    validate_auth_secrets()


def test_validate_auth_secrets_accepts_strong_production_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENV_FOR_DYNACONF", "production")
    from cubeplex.config import config

    original_jwt_secret = config.get("auth.jwt_secret")
    original_csrf_secret = config.get("auth.csrf_secret")
    config.set("auth.jwt_secret", "a" * 32)
    config.set("auth.csrf_secret", "b" * 32)

    from cubeplex.api.app import validate_auth_secrets

    try:
        validate_auth_secrets()
    finally:
        config.set("auth.jwt_secret", original_jwt_secret)
        config.set("auth.csrf_secret", original_csrf_secret)


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
