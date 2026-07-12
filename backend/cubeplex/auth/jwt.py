"""JWT cookie authentication backend."""

from fastapi_users.authentication import (
    AuthenticationBackend,
    CookieTransport,
    JWTStrategy,
)

from cubeplex.config import config


def _cookie_transport() -> CookieTransport:
    return CookieTransport(
        cookie_name=config.get("auth.cookie_name", "cubeplex_auth"),
        cookie_max_age=config.get("auth.jwt_lifetime_seconds", 86400),
        cookie_secure=config.get("auth.cookie_secure", False),
        cookie_httponly=True,
        cookie_samesite=config.get("auth.cookie_samesite", "lax"),
    )


def _jwt_strategy() -> JWTStrategy:  # type: ignore[type-arg]
    return JWTStrategy(
        secret=config.get("auth.jwt_secret", "CHANGE_ME"),
        lifetime_seconds=config.get("auth.jwt_lifetime_seconds", 86400),
    )


auth_backend = AuthenticationBackend(
    name="jwt-cookie",
    transport=_cookie_transport(),
    get_strategy=_jwt_strategy,
)
