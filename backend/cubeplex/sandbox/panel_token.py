"""Signed token for sandbox panel (browser/terminal) reverse-proxy access.

The panel URL embeds this short-lived HS256 JWT in its **path** so every relative
asset request and the WebSocket handshake the panel client makes carries the
credential (an iframe navigation and a WS sub-resource cannot attach auth
headers). The backend reverse-proxy route verifies it and forwards to the
cluster-internal opensandbox-server; see
docs/dev/specs/2026-07-26-sandbox-panel-proxy-design.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt

_ISS = "cubeplex:sandbox-panel"


@dataclass(frozen=True, slots=True)
class PanelClaims:
    sandbox_id: str
    port: int


def sign_panel_token(
    *,
    sandbox_id: str,
    port: int,
    secret: str,
    ttl: timedelta,
) -> str:
    now = datetime.now(UTC)
    claims = {
        "sid": sandbox_id,
        "port": int(port),
        "exp": int((now + ttl).timestamp()),
        "iat": int(now.timestamp()),
        "iss": _ISS,
    }
    return jwt.encode(claims, secret, algorithm="HS256")


def verify_panel_token(token: str, *, secret: str) -> PanelClaims:
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            issuer=_ISS,
        )
    except jwt.PyJWTError as exc:
        raise ValueError("Invalid or expired panel token") from exc
    return PanelClaims(sandbox_id=str(payload["sid"]), port=int(payload["port"]))


def get_panel_secret() -> str:
    from cubeplex.config import config

    return str(config.get("auth.jwt_secret", "CHANGE_ME"))


def get_panel_base_url() -> str:
    """Public base URL of the cubeplex backend, used as the panel origin.

    Reuses ``api.public_url`` (the backend's own public address). Empty when the
    operator has not configured a public URL — callers treat that as
    "panels unavailable" rather than minting an unreachable link.
    """
    from cubeplex.config import config

    return str(config.get("api.public_url", "") or "").rstrip("/")
