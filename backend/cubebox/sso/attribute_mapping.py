"""Attribute mapping — normalize raw IdP responses via config-driven mapping.

OIDC has standard defaults; SAML requires explicit configuration.
Google social login uses hardcoded standard claims.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class AttributeMappingError(Exception):
    """A required attribute could not be resolved from the IdP response."""


# OIDC standard claim names (OpenID Connect Core 1.0)
OIDC_DEFAULTS: dict[str, str] = {
    "id": "sub",
    "email": "email",
    "name": "name",
    "avatar": "picture",
}


@dataclass(frozen=True)
class MappedAttributes:
    id: str
    email: str
    name: str | None
    avatar: str | None
    raw: dict[str, Any]


def apply_mapping(
    raw_attributes: dict[str, Any],
    mapping: dict[str, str],
    *,
    protocol: str,
) -> MappedAttributes:
    """Apply attribute mapping to raw IdP response.

    For OIDC, missing mapping keys fall back to OIDC_DEFAULTS.
    For SAML, all keys must be explicitly configured.
    """
    effective = dict(mapping)
    if protocol == "oidc":
        for key, default in OIDC_DEFAULTS.items():
            effective.setdefault(key, default)

    def _resolve(key: str, required: bool) -> str | None:
        attr_name = effective.get(key)
        if attr_name is None:
            if required:
                raise AttributeMappingError(f"No mapping configured for required attribute '{key}'")
            return None
        value = raw_attributes.get(attr_name)
        if isinstance(value, list):
            value = value[0] if value else None
        if value is None and required:
            raise AttributeMappingError(
                f"Required attribute '{key}' (mapped to '{attr_name}') not found in IdP response"
            )
        return str(value) if value is not None else None

    ext_id = _resolve("id", required=True)
    email = _resolve("email", required=True)
    name = _resolve("name", required=False)
    avatar = _resolve("avatar", required=False)
    assert ext_id is not None
    assert email is not None

    return MappedAttributes(id=ext_id, email=email, name=name, avatar=avatar, raw=raw_attributes)
