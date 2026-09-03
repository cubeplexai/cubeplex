"""Per-route rate limit using slowapi."""

from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request

from cubeplex.config import config


def get_rate_limit_client(request: Request) -> str:
    """Return the original client address carried through the frontend proxy."""
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        client_address = forwarded_for.split(",", maxsplit=1)[0].strip()
        if client_address:
            return client_address
    return get_remote_address(request)


limiter = Limiter(
    key_func=get_rate_limit_client,
    storage_uri=str(
        config.get(
            "auth.rate_limit.storage_uri",
            config.get("redis.url", "redis://localhost:6379/0"),
        )
    ),
    key_prefix=f"{config.get('redis.key_prefix', 'cubeplex')}:rate-limit",
)

LOGIN_LIMIT = f"{config.get('auth.rate_limit.login_per_minute', 5)}/minute"
REGISTER_LIMIT = f"{config.get('auth.rate_limit.register_per_minute', 3)}/minute"
