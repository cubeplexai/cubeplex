"""Building an OIDC client config out of a persisted connection row.

The rest of ``cubeplex.sso.oidc`` is shared with Google social login and stays
in core; only this function knows about ``SSOConnection``.
"""

from __future__ import annotations

from cubeplex.models.sso_connection import SSOConnection
from cubeplex.sso.oidc import OIDCConfig


def oidc_config_from_connection(connection: SSOConnection) -> OIDCConfig:
    """Build an OIDCConfig from a persisted SSOConnection's JSONB config."""
    cfg = connection.config
    return OIDCConfig(
        issuer=cfg["issuer"],
        authorization_endpoint=cfg["authorization_endpoint"],
        token_endpoint=cfg["token_endpoint"],
        jwks_uri=cfg["jwks_uri"],
        client_id=cfg["client_id"],
        userinfo_endpoint=cfg.get("userinfo_endpoint"),
        scopes=tuple(cfg.get("scopes", ["openid", "email", "profile"])),
        attribute_mapping=cfg.get("attribute_mapping"),
    )
