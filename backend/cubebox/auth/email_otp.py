"""Email OTP verification service — Redis-backed, no HTTP.

Replaces the magic-link (JWT-in-URL) verification entirely. The OTP is a
short numeric code stored in Redis with a TTL; success deletes the key so
it cannot be replayed. Brute-force is bounded by max_attempts and a
per-email send rate limit.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass

from redis.asyncio import Redis

from cubebox.cache import get_redis
from cubebox.config import config
from cubebox.services.email import get_email_service

_OTP_KEY = "email_otp:{email}"
_SENT_KEY = "email_otp_sent:{email}"
_RL_KEY = "email_otp_rl:{email}"


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    reason: str | None
    remaining_attempts: int | None


def is_email_verification_enabled() -> bool:
    raw = str(config.get("auth.email_verification.enabled", "auto")).lower()
    if raw == "true":
        return True
    if raw == "false":
        return False
    # auto
    return str(config.get("email.backend", "log")).lower() == "smtp"


def _code_length() -> int:
    return int(config.get("auth.email_verification.code_length", 6))


def _ttl() -> int:
    return int(config.get("auth.email_verification.code_ttl_seconds", 600))


def _max_attempts() -> int:
    return int(config.get("auth.email_verification.max_attempts", 5))


def _cooldown() -> int:
    return int(config.get("auth.email_verification.resend_cooldown_seconds", 60))


def _rate_limit_per_hour() -> int:
    return int(config.get("auth.email_verification.rate_limit_per_hour", 10))


def _gen_code(length: int) -> str:
    digits = "0123456789"
    return "".join(secrets.choice(digits) for _ in range(length))


class _CooldownError(Exception):
    pass


class _RateLimitError(Exception):
    pass


async def issue_otp(email: str) -> str:
    """Generate + store an OTP for `email`, send it, return the code.

    Raises _CooldownError if a resend is requested too soon, _RateLimitError
    if the per-hour send cap is exceeded. Callers translate these into HTTP.
    """
    redis = get_redis()
    sent_key = _SENT_KEY.format(email=email)
    rl_key = _RL_KEY.format(email=email)

    if await redis.exists(sent_key):
        raise _CooldownError
    sends = await redis.incr(rl_key)
    if sends == 1:
        await redis.expire(rl_key, 3600)
    if sends > _rate_limit_per_hour():
        raise _RateLimitError

    code = _gen_code(_code_length())
    key = _OTP_KEY.format(email=email)
    await redis.hset(key, mapping={"code": code, "attempts": "0"})  # type: ignore[misc]
    await redis.expire(key, _ttl())
    await redis.set(sent_key, "1", ex=_cooldown())

    await get_email_service().send(
        to=email,
        subject="Your cubebox verification code",
        template="email_otp_verification",
        context={"code": code, "ttl_minutes": str(_ttl() // 60)},
    )
    return code


async def verify_otp(email: str, code: str) -> VerifyResult:
    redis: Redis = get_redis()
    key = _OTP_KEY.format(email=email)
    data = await redis.hgetall(key)  # type: ignore[misc]
    if not data:
        return VerifyResult(ok=False, reason="expired_or_unknown", remaining_attempts=None)

    stored = data.get("code")
    attempts = int(data.get("attempts", "0"))
    if stored is not None and secrets.compare_digest(str(stored), code):
        await redis.delete(key)
        return VerifyResult(ok=True, reason=None, remaining_attempts=None)

    attempts += 1
    if attempts >= _max_attempts():
        await redis.delete(key)
        return VerifyResult(ok=False, reason="max_attempts_reached", remaining_attempts=0)
    await redis.hset(key, mapping={"attempts": str(attempts)})  # type: ignore[misc]
    return VerifyResult(
        ok=False,
        reason="invalid_otp",
        remaining_attempts=_max_attempts() - attempts,
    )
