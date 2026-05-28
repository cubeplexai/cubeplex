"""Per-trigger Redis token-bucket rate limiter."""

from __future__ import annotations

from redis.asyncio import Redis

_LUA = """
local rate = tonumber(ARGV[1]) / 60.0
local burst = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local data = redis.call('HMGET', KEYS[1], 'tokens', 'last')
local tokens = tonumber(data[1])
local last = tonumber(data[2])
if tokens == nil then tokens = burst end
if last == nil then last = now end
local delta = math.max(0, now - last)
tokens = math.min(burst, tokens + delta * rate)
local ok = 0
if tokens >= 1 then
  tokens = tokens - 1
  ok = 1
end
redis.call('HMSET', KEYS[1], 'tokens', tostring(tokens), 'last', tostring(now))
redis.call('EXPIRE', KEYS[1], 120)
return ok
"""


async def allow(
    redis: Redis,
    *,
    key_prefix: str,
    trigger_id: str,
    rate_per_min: int,
    burst: int,
    now: float,
) -> bool:
    key = f"{key_prefix}:trig:rl:{trigger_id}"
    result = await redis.eval(_LUA, 1, key, rate_per_min, burst, now)  # type: ignore[misc]
    return bool(int(result))
