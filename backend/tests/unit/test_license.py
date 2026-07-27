"""Unit: license-key parsing, signature/expiry validation, feature gates."""

import base64
import json
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from cubeplex.plugins.license import (
    License,
    LicenseError,
    parse_license_key,
)


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _make_key(
    private_key: Ed25519PrivateKey,
    *,
    licensee: str = "Acme Corp",
    features: list[str] | None = None,
    issued_at: datetime | None = None,
    expires_at: datetime | None = None,
) -> str:
    now = datetime.now(UTC)
    payload = {
        "licensee": licensee,
        "features": features if features is not None else ["multi_org"],
        "issued_at": (issued_at or now).isoformat(),
        "expires_at": (expires_at or (now + timedelta(days=365))).isoformat(),
    }
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return f"CBX1.{_b64url(raw)}.{_b64url(private_key.sign(raw))}"


@pytest.fixture
def keypair() -> tuple[Ed25519PrivateKey, str]:
    private_key = Ed25519PrivateKey.generate()
    public_hex = private_key.public_key().public_bytes_raw().hex()
    return private_key, public_hex


def test_valid_key_parses(keypair: tuple[Ed25519PrivateKey, str]) -> None:
    private_key, public_hex = keypair
    lic = parse_license_key(_make_key(private_key), public_key_hex=public_hex)
    assert isinstance(lic, License)
    assert lic.licensee == "Acme Corp"
    assert lic.features == frozenset({"multi_org"})
    assert lic.expires_at.tzinfo is not None


def test_wrong_signer_rejected(keypair: tuple[Ed25519PrivateKey, str]) -> None:
    _, public_hex = keypair
    other = Ed25519PrivateKey.generate()
    with pytest.raises(LicenseError):
        parse_license_key(_make_key(other), public_key_hex=public_hex)


def test_tampered_payload_rejected(keypair: tuple[Ed25519PrivateKey, str]) -> None:
    private_key, public_hex = keypair
    key = _make_key(private_key)
    prefix, payload_b64, sig_b64 = key.split(".")
    raw = json.loads(base64.urlsafe_b64decode(payload_b64 + "=="))
    raw["features"] = ["multi_org", "sso", "audit"]
    forged = _b64url(json.dumps(raw, separators=(",", ":")).encode())
    with pytest.raises(LicenseError):
        parse_license_key(f"{prefix}.{forged}.{sig_b64}", public_key_hex=public_hex)


def test_expired_key_rejected(keypair: tuple[Ed25519PrivateKey, str]) -> None:
    private_key, public_hex = keypair
    key = _make_key(
        private_key,
        issued_at=datetime.now(UTC) - timedelta(days=400),
        expires_at=datetime.now(UTC) - timedelta(days=35),
    )
    with pytest.raises(LicenseError, match="expired"):
        parse_license_key(key, public_key_hex=public_hex)


def test_naive_timestamp_rejected(keypair: tuple[Ed25519PrivateKey, str]) -> None:
    """tz-aware only: a naive expires_at must not be silently treated as UTC."""
    private_key, public_hex = keypair
    payload = {
        "licensee": "Acme Corp",
        "features": ["multi_org"],
        "issued_at": datetime.now(UTC).isoformat(),
        "expires_at": (datetime.now(UTC) + timedelta(days=30)).replace(tzinfo=None).isoformat(),
    }
    raw = json.dumps(payload, separators=(",", ":")).encode()
    key = f"CBX1.{_b64url(raw)}.{_b64url(private_key.sign(raw))}"
    with pytest.raises(LicenseError, match="timezone-aware"):
        parse_license_key(key, public_key_hex=public_hex)


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "garbage",
        "CBX1.onlytwo",
        "CBX9.YQ.YQ",  # wrong prefix
        "CBX1.!!!.YQ",  # bad base64
    ],
)
def test_malformed_keys_rejected(bad: str, keypair: tuple[Ed25519PrivateKey, str]) -> None:
    _, public_hex = keypair
    with pytest.raises(LicenseError):
        parse_license_key(bad, public_key_hex=public_hex)


def test_non_json_payload_rejected(keypair: tuple[Ed25519PrivateKey, str]) -> None:
    """A correctly signed non-JSON payload must still be refused."""
    private_key, public_hex = keypair
    raw = b"not json at all"
    key = f"CBX1.{_b64url(raw)}.{_b64url(private_key.sign(raw))}"
    with pytest.raises(LicenseError):
        parse_license_key(key, public_key_hex=public_hex)


def test_unprovisioned_signing_key_degrades_to_oss(
    monkeypatch: pytest.MonkeyPatch, keypair: tuple[Ed25519PrivateKey, str]
) -> None:
    """Until the production signer is provisioned, a real key must not validate."""
    import cubeplex.plugins.license as lic_mod

    private_key, _ = keypair
    monkeypatch.setattr(lic_mod, "LICENSE_PUBLIC_KEY_HEX", "")
    with pytest.raises(LicenseError, match="no license signing public key"):
        parse_license_key(_make_key(private_key))

    values = {"license.key": _make_key(private_key)}
    monkeypatch.setattr(lic_mod, "_config_get", lambda k: values.get(k))
    lic_mod.reset_license_cache_for_tests()
    assert lic_mod.get_edition() == "oss"
    lic_mod.reset_license_cache_for_tests()


def test_load_license_missing_key_is_oss(monkeypatch: pytest.MonkeyPatch) -> None:
    import cubeplex.plugins.license as lic_mod

    monkeypatch.setattr(lic_mod, "_config_get", lambda key: None)
    lic_mod.reset_license_cache_for_tests()
    assert lic_mod.load_license() is None
    assert lic_mod.get_edition() == "oss"
    assert lic_mod.get_features() == []
    assert lic_mod.has_feature("multi_org") is False
    lic_mod.reset_license_cache_for_tests()


def test_load_license_valid_key_is_ee(
    monkeypatch: pytest.MonkeyPatch, keypair: tuple[Ed25519PrivateKey, str]
) -> None:
    import cubeplex.plugins.license as lic_mod

    private_key, public_hex = keypair
    key = _make_key(private_key, features=["multi_org", "sso"])
    values = {"license.key": key, "license.public_key_hex": public_hex}
    monkeypatch.setattr(lic_mod, "_config_get", lambda k: values.get(k))
    lic_mod.reset_license_cache_for_tests()
    assert lic_mod.get_edition() == "ee"
    assert lic_mod.get_features() == ["multi_org", "sso"]
    assert lic_mod.has_feature("sso") is True
    lic_mod.reset_license_cache_for_tests()


def test_load_license_invalid_key_degrades_to_oss(
    monkeypatch: pytest.MonkeyPatch, keypair: tuple[Ed25519PrivateKey, str]
) -> None:
    import cubeplex.plugins.license as lic_mod

    _, public_hex = keypair
    values = {"license.key": "CBX1.bogus.bogus", "license.public_key_hex": public_hex}
    monkeypatch.setattr(lic_mod, "_config_get", lambda k: values.get(k))
    lic_mod.reset_license_cache_for_tests()
    assert lic_mod.load_license() is None
    assert lic_mod.get_edition() == "oss"
    lic_mod.reset_license_cache_for_tests()


def test_load_license_is_cached(
    monkeypatch: pytest.MonkeyPatch, keypair: tuple[Ed25519PrivateKey, str]
) -> None:
    """Config is read once; a hot path calling has_feature must not re-parse."""
    import cubeplex.plugins.license as lic_mod

    private_key, public_hex = keypair
    values = {"license.key": _make_key(private_key), "license.public_key_hex": public_hex}
    calls: list[str] = []

    def _counting_get(k: str) -> object | None:
        calls.append(k)
        return values.get(k)

    monkeypatch.setattr(lic_mod, "_config_get", _counting_get)
    lic_mod.reset_license_cache_for_tests()
    for _ in range(5):
        assert lic_mod.has_feature("multi_org") is True
    assert calls.count("license.key") == 1
    lic_mod.reset_license_cache_for_tests()
