"""Unit tests for building an OIDC config from a persisted connection row.

Split out of tests/unit/test_sso_oidc.py, which keeps the rest: the OIDC client
itself is shared with Google social login and stays in core. Only this mapping
knows about the connection shape, so only it followed the relocation.
"""

from __future__ import annotations

import pytest

pytest.importorskip("cubeplex_ee", reason="enterprise SSO lives in the optional package")

from cubeplex_ee.sso.oidc_config import oidc_config_from_connection  # noqa: E402

# Same values as tests/unit/test_sso_oidc.py, duplicated rather than imported:
# the two files now live in different lanes, and a cross-lane import would make
# the default suite depend on a module it skips.
ISSUER = "https://idp.example.com"
CLIENT_ID = "client-abc"
TOKEN_ENDPOINT = "https://idp.example.com/token"
JWKS_URI = "https://idp.example.com/jwks"
AUTHZ_ENDPOINT = "https://idp.example.com/authorize"
USERINFO_ENDPOINT = "https://idp.example.com/userinfo"

from cubeplex.models.sso_connection import SSOConnection  # noqa: E402

# ----------------------------------------------------------------------
# oidc_config_from_connection
# ----------------------------------------------------------------------


def test_oidc_config_from_connection_maps_fields() -> None:
    conn = SSOConnection(
        org_id="org-test",
        protocol="oidc",
        display_name="Acme OIDC",
        status="active",
        provisioning="auto",
        config={
            "issuer": ISSUER,
            "authorization_endpoint": AUTHZ_ENDPOINT,
            "token_endpoint": TOKEN_ENDPOINT,
            "jwks_uri": JWKS_URI,
            "client_id": CLIENT_ID,
            "userinfo_endpoint": USERINFO_ENDPOINT,
            "scopes": ["openid", "email"],
            "attribute_mapping": {"email": "preferred_email"},
        },
    )
    cfg = oidc_config_from_connection(conn)
    assert cfg.issuer == ISSUER
    assert cfg.authorization_endpoint == AUTHZ_ENDPOINT
    assert cfg.token_endpoint == TOKEN_ENDPOINT
    assert cfg.jwks_uri == JWKS_URI
    assert cfg.client_id == CLIENT_ID
    assert cfg.userinfo_endpoint == USERINFO_ENDPOINT
    assert cfg.scopes == ("openid", "email")
    assert cfg.attribute_mapping == {"email": "preferred_email"}


def test_oidc_config_from_connection_defaults_scopes_when_missing() -> None:
    conn = SSOConnection(
        org_id="org-test",
        protocol="oidc",
        display_name="Acme",
        config={
            "issuer": ISSUER,
            "authorization_endpoint": AUTHZ_ENDPOINT,
            "token_endpoint": TOKEN_ENDPOINT,
            "jwks_uri": JWKS_URI,
            "client_id": CLIENT_ID,
        },
    )
    cfg = oidc_config_from_connection(conn)
    assert cfg.scopes == ("openid", "email", "profile")
    assert cfg.userinfo_endpoint is None
    assert cfg.attribute_mapping is None
