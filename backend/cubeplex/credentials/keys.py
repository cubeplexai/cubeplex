"""Vault master key parsing and validation."""

from cryptography.fernet import Fernet


def parse_vault_keys(raw: str) -> list[bytes]:
    """Parse comma-separated Fernet keys for MultiFernet rotation."""
    keys = [part.strip().encode() for part in raw.split(",") if part.strip()]
    if not keys:
        raise ValueError("Invalid CUBEPLEX_AUTH__VAULT_KEY: at least one Fernet key is required")

    for key in keys:
        try:
            Fernet(key)
        except ValueError as exc:
            raise ValueError("Invalid CUBEPLEX_AUTH__VAULT_KEY: expected Fernet key(s)") from exc

    return keys
