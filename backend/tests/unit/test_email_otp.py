from unittest.mock import patch

import pytest

from cubebox.auth.email_otp import VerifyResult, verify_otp


class FakeRedis:
    """Minimal in-memory async fake of the Redis ops email_otp uses."""

    def __init__(self) -> None:
        self.store: dict[str, dict[str, str]] = {}
        self.ttl: dict[str, int] = {}

    async def hset(self, key, mapping=None, **kwargs):
        self.store.setdefault(key, {}).update(mapping or {})
        return True

    async def hgetall(self, key):
        return dict(self.store.get(key, {}))

    async def delete(self, *keys):
        n = 0
        for k in keys:
            if k in self.store:
                del self.store[k]
                n += 1
        return n

    async def exists(self, key):
        return 1 if key in self.store else 0

    async def expire(self, key, ttl):
        self.ttl[key] = int(ttl)
        return True

    async def incr(self, key):
        v = int(self.store.get(key, {}).get("v", "0")) + 1
        self.store.setdefault(key, {})["v"] = str(v)
        return v

    async def set(self, key, value, ex=None, **kwargs):
        self.store[key] = {"v": str(value)}
        if ex is not None:
            self.ttl[key] = int(ex)
        return True


@pytest.mark.asyncio
async def test_verify_success_deletes_key():
    fake = FakeRedis()
    await fake.hset("email_otp:a@b.com", mapping={"code": "123456", "attempts": "0"})
    with patch("cubebox.auth.email_otp.get_redis", return_value=fake):
        res = await verify_otp("a@b.com", "123456")
    assert isinstance(res, VerifyResult)
    assert res.ok is True
    assert "email_otp:a@b.com" not in fake.store  # success deletes (no replay)


@pytest.mark.asyncio
async def test_verify_wrong_code_increments_attempts():
    fake = FakeRedis()
    await fake.hset("email_otp:a@b.com", mapping={"code": "123456", "attempts": "0"})
    with patch("cubebox.auth.email_otp.get_redis", return_value=fake):
        res = await verify_otp("a@b.com", "000000")
    assert res.ok is False
    assert res.reason == "invalid_otp"
    assert res.remaining_attempts == 4  # 5 max - 1 used


@pytest.mark.asyncio
async def test_verify_missing_key_expired():
    fake = FakeRedis()
    with patch("cubebox.auth.email_otp.get_redis", return_value=fake):
        res = await verify_otp("a@b.com", "123456")
    assert res.ok is False
    assert res.reason == "expired_or_unknown"


@pytest.mark.asyncio
async def test_verify_max_attempts_invalidates():
    fake = FakeRedis()
    await fake.hset("email_otp:a@b.com", mapping={"code": "123456", "attempts": "4"})
    with patch("cubebox.auth.email_otp.get_redis", return_value=fake):
        res = await verify_otp("a@b.com", "000000")
    assert res.ok is False
    assert res.reason == "max_attempts_reached"
    assert "email_otp:a@b.com" not in fake.store  # key deleted
