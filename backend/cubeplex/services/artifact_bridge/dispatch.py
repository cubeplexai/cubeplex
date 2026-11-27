"""Validate, throttle, and invoke a registered artifact function.

Every call goes through here, so limits and error translation are defined once
for all present and future functions.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from typing import Any

from loguru import logger
from pydantic import ValidationError

from cubeplex.cache import RedisHandle
from cubeplex.config import config
from cubeplex.services.artifact_bridge.registry import get_function
from cubeplex.services.artifact_bridge.types import (
    ArtifactCallContext,
    ArtifactCallError,
    ArtifactCallFailed,
    ArtifactCallInvalidInput,
    ArtifactCallRateLimited,
    ArtifactFunction,
)


def _limit(key: str, default: float) -> float:
    return float(config.get(f"artifact_bridge.{key}", default))


async def _enforce_rate_limit(
    redis: RedisHandle,
    ctx: ArtifactCallContext,
    function: ArtifactFunction,
    limit: int,
) -> None:
    """Per-user, per-artifact, per-function fixed-window counter."""
    bucket = int(time.time() // 60)
    key = f"{redis.key_prefix}:artbridge:{ctx.user_id}:{ctx.artifact.id}:{function.name}:{bucket}"
    count = await redis.client.incr(key)
    if count == 1:
        # Two windows' worth so the counter survives the boundary without a
        # second round trip; the bucket suffix already rotates the key.
        await redis.client.expire(key, 120)
    if count > limit:
        raise ArtifactCallRateLimited(function.name)


async def _invoke(
    ctx: ArtifactCallContext,
    function: ArtifactFunction,
    raw_args: Mapping[str, Any],
    *,
    redis: RedisHandle,
) -> dict[str, Any]:
    try:
        args = function.input_model.model_validate(dict(raw_args))
    except ValidationError as exc:
        raise ArtifactCallInvalidInput(str(exc)) from exc

    limit = function.rate_limit_per_minute or int(_limit("rate_limit_per_minute", 20))
    await _enforce_rate_limit(redis, ctx, function, limit)

    timeout = function.timeout_seconds or _limit("timeout_seconds", 60)
    started = time.monotonic()
    try:
        async with asyncio.timeout(timeout):
            result = await function.handler(ctx, args)
    except ArtifactCallError:
        raise
    except TimeoutError:
        logger.warning("artifact bridge timeout: fn={} artifact={}", function.name, ctx.artifact.id)
        raise ArtifactCallFailed("timed out") from None
    except Exception as exc:  # handler faults must not leak provider internals
        logger.exception("artifact bridge error: fn={} artifact={}", function.name, ctx.artifact.id)
        raise ArtifactCallFailed("call failed") from exc

    logger.info(
        "artifact bridge: fn={} artifact={} user={} ms={}",
        function.name,
        ctx.artifact.id,
        ctx.user_id,
        int((time.monotonic() - started) * 1000),
    )
    return result


async def dispatch(
    ctx: ArtifactCallContext,
    name: str,
    raw_args: Mapping[str, Any],
    *,
    redis: RedisHandle,
) -> dict[str, Any]:
    """Invoke the named function on behalf of artifact HTML."""
    if not config.get("artifact_bridge.enabled", True):
        raise ArtifactCallFailed("artifact bridge disabled")
    return await _invoke(ctx, get_function(name), raw_args, redis=redis)
