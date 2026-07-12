"""Per-route rate limit using slowapi."""

from slowapi import Limiter
from slowapi.util import get_remote_address

from cubeplex.config import config

limiter = Limiter(key_func=get_remote_address)

LOGIN_LIMIT = f"{config.get('auth.rate_limit.login_per_minute', 5)}/minute"
REGISTER_LIMIT = f"{config.get('auth.rate_limit.register_per_minute', 3)}/minute"
