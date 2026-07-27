"""Offline license-key verification + feature gates for EE.

Key format: ``CBX1.<b64url(payload-json)>.<b64url(ed25519 signature)>``.
The signature covers the exact payload bytes, so no JSON canonicalization is
required. The embedded public key is the production signer; ``license.public_key_hex``
config exists so tests/dev can use their own keypair — a production deployment never
sets it.

Verification is offline by design: no phone-home, no license server, works
air-gapped. See docs/dev/specs/2026-07-07-oss-ee-split-design.md §7.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

logger = logging.getLogger(__name__)

# Production signing public key (raw Ed25519, 32 bytes, hex). The private half
# never enters this repo.
#
# NOT YET PROVISIONED. Generate the keypair on a machine you control, outside any
# agent session or shared transcript — the private half is the only thing that can
# sign customer licenses:
#
#     cd backend && uv run python scripts/dev/license_keygen.py genkey
#
# Paste the printed public_key_hex here; store the private half in a password
# manager. Until then any configured license.key fails verification and the
# deployment runs as OSS, which is the safe direction.
LICENSE_PUBLIC_KEY_HEX = ""

_KEY_PREFIX = "CBX1"

FEATURE_MULTI_ORG = "multi_org"


class LicenseError(Exception):
    """Invalid, tampered, malformed, or expired license key."""


@dataclass(frozen=True)
class License:
    licensee: str
    features: frozenset[str]
    issued_at: datetime
    expires_at: datetime


def _b64url_decode(segment: str) -> bytes:
    pad = "=" * (-len(segment) % 4)
    try:
        return base64.urlsafe_b64decode(segment + pad)
    except (binascii.Error, ValueError) as exc:
        raise LicenseError("malformed base64 segment") from exc


def _parse_ts(payload: dict[str, object], field: str) -> datetime:
    raw = payload.get(field)
    if not isinstance(raw, str):
        raise LicenseError(f"missing {field}")
    try:
        ts = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise LicenseError(f"bad {field}") from exc
    if ts.tzinfo is None:
        raise LicenseError(f"{field} must be timezone-aware")
    return ts


def parse_license_key(
    key: str,
    *,
    public_key_hex: str | None = None,
    now: datetime | None = None,
) -> License:
    """Verify signature + expiry and return the License. Raises LicenseError."""
    parts = key.split(".")
    if len(parts) != 3 or parts[0] != _KEY_PREFIX:
        raise LicenseError("malformed license key")
    payload_raw = _b64url_decode(parts[1])
    signature = _b64url_decode(parts[2])

    pub_hex = public_key_hex or LICENSE_PUBLIC_KEY_HEX
    if not pub_hex:
        raise LicenseError(
            "no license signing public key in this build; set license.public_key_hex "
            "for dev, or provision LICENSE_PUBLIC_KEY_HEX for a release"
        )
    try:
        public_key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(pub_hex))
    except ValueError as exc:
        raise LicenseError("bad public key") from exc
    try:
        public_key.verify(signature, payload_raw)
    except InvalidSignature as exc:
        raise LicenseError("signature verification failed") from exc

    try:
        payload = json.loads(payload_raw)
    except ValueError as exc:
        raise LicenseError("payload is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise LicenseError("payload is not an object")

    licensee = payload.get("licensee")
    features = payload.get("features")
    if not isinstance(licensee, str) or not isinstance(features, list):
        raise LicenseError("payload missing licensee/features")

    issued_at = _parse_ts(payload, "issued_at")
    expires_at = _parse_ts(payload, "expires_at")
    if (now or datetime.now(UTC)) >= expires_at:
        raise LicenseError("license expired")

    return License(
        licensee=licensee,
        features=frozenset(str(f) for f in features),
        issued_at=issued_at,
        expires_at=expires_at,
    )


def _config_get(key: str) -> object | None:
    from cubeplex.config import config

    value: object | None = config.get(key, None)
    return value


_license_loaded = False
_license: License | None = None


def load_license() -> License | None:
    """Parse the configured license key once; invalid/missing → None (OSS)."""
    global _license_loaded, _license
    if _license_loaded:
        return _license
    key = _config_get("license.key")
    pub = _config_get("license.public_key_hex")
    _license = None
    if isinstance(key, str) and key.strip():
        try:
            _license = parse_license_key(
                key.strip(),
                public_key_hex=pub.strip() if isinstance(pub, str) and pub.strip() else None,
            )
            logger.info(
                "license loaded: licensee=%s features=%s expires=%s",
                _license.licensee,
                sorted(_license.features),
                _license.expires_at.isoformat(),
            )
        except LicenseError as exc:
            logger.warning("configured license.key is invalid, running as OSS: %s", exc)
    _license_loaded = True
    return _license


def reset_license_cache_for_tests() -> None:
    global _license_loaded, _license
    _license_loaded = False
    _license = None


def has_feature(name: str) -> bool:
    lic = load_license()
    return lic is not None and name in lic.features


def get_edition() -> Literal["oss", "ee"]:
    return "ee" if load_license() is not None else "oss"


def get_features() -> list[str]:
    lic = load_license()
    return sorted(lic.features) if lic else []
