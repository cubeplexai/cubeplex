"""JWT token for IM identity linking.

The IM /link command signs a short-lived token encoding the sender's IM
identity and their claimed email. The browser confirmation endpoint
decodes it and checks the email against the logged-in cubeplex user.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt

_ISS = "cubeplex:im-link"
_TTL = timedelta(minutes=10)


@dataclass(frozen=True, slots=True)
class LinkClaims:
    im_user_id: str
    email: str
    account_id: str
    workspace_id: str
    platform: str
    chat_id: str = ""


def sign_link_token(
    *,
    im_user_id: str,
    email: str,
    account_id: str,
    workspace_id: str,
    platform: str,
    secret: str,
    chat_id: str = "",
) -> str:
    now = datetime.now(UTC)
    claims = {
        "sub": im_user_id,
        "email": email.strip().lower(),
        "act": account_id,
        "ws": workspace_id,
        "plt": platform,
        "cid": chat_id,
        "exp": int((now + _TTL).timestamp()),
        "iat": int(now.timestamp()),
        "iss": _ISS,
    }
    return jwt.encode(claims, secret, algorithm="HS256")


def get_jwt_secret() -> str:
    from cubeplex.config import config

    return str(config.get("auth.jwt_secret", "CHANGE_ME"))


def get_frontend_base_url() -> str:
    from cubeplex.config import config

    return str(config.get("frontend_base_url", "http://localhost:3000")).rstrip("/")


def verify_link_token(token: str, *, secret: str) -> LinkClaims:
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            issuer=_ISS,
        )
    except jwt.PyJWTError as exc:
        raise ValueError("Invalid or expired link token") from exc
    return LinkClaims(
        im_user_id=payload["sub"],
        email=payload["email"],
        account_id=payload["act"],
        workspace_id=payload["ws"],
        platform=payload["plt"],
        chat_id=str(payload.get("cid") or ""),
    )
