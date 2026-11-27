"""Unit contracts for bridge dispatch: validation, throttling, error mapping."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest
from pydantic import BaseModel

from cubeplex.cache import RedisHandle
from cubeplex.config import config
from cubeplex.models import Artifact, Role
from cubeplex.services.artifact_bridge import dispatch
from cubeplex.services.artifact_bridge.dispatch import _invoke
from cubeplex.services.artifact_bridge.types import (
    ArtifactBridgeDisabled,
    ArtifactCallContext,
    ArtifactCallFailed,
    ArtifactCallInvalidInput,
    ArtifactCallRateLimited,
    ArtifactFunction,
)


class _FakeRedisClient:
    def __init__(self) -> None:
        self.counters: dict[str, int] = {}
        self.expirations: dict[str, int] = {}

    async def set(self, key: str, value: int, ex: int | None = None, nx: bool = False) -> bool:
        if nx and key in self.counters:
            return False
        self.counters[key] = int(value)
        if ex is not None:
            self.expirations[key] = ex
        return True

    async def incr(self, key: str) -> int:
        self.counters[key] = self.counters.get(key, 0) + 1
        return self.counters[key]

    async def expire(self, key: str, ttl: int) -> bool:
        self.expirations[key] = ttl
        return True


class _EchoInput(BaseModel):
    text: str


def _redis() -> RedisHandle:
    return RedisHandle(client=_FakeRedisClient(), key_prefix="unit")  # type: ignore[arg-type]


def _ctx() -> ArtifactCallContext:
    artifact = Artifact(
        org_id="org-00000000000000",
        workspace_id="ws-00000000000000",
        conversation_id="conv-00000000000000",
        name="chart.html",
        artifact_type="website",
        path="chart.html",
    )
    return ArtifactCallContext(
        org_id="org-00000000000000",
        workspace_id="ws-00000000000000",
        user_id="usr-00000000000000",
        role=Role.MEMBER,
        conversation_id="conv-00000000000000",
        artifact=artifact,
        session=None,  # type: ignore[arg-type]
    )


def _function(handler: Any, **overrides: Any) -> ArtifactFunction:
    return ArtifactFunction(
        name=overrides.pop("name", "echo"),
        description="echo",
        input_model=_EchoInput,
        handler=handler,
        **overrides,
    )


async def _echo(ctx: ArtifactCallContext, args: _EchoInput) -> dict[str, Any]:
    return {"text": args.text}


async def test_valid_args_invoke_handler() -> None:
    result = await _invoke(_ctx(), _function(_echo), {"text": "hi"}, redis=_redis())
    assert result == {"text": "hi"}


async def test_invalid_args_are_rejected() -> None:
    with pytest.raises(ArtifactCallInvalidInput):
        await _invoke(_ctx(), _function(_echo), {"nope": 1}, redis=_redis())


async def test_handler_error_becomes_call_failed() -> None:
    async def _boom(ctx: ArtifactCallContext, args: _EchoInput) -> dict[str, Any]:
        raise RuntimeError("provider exploded")

    with pytest.raises(ArtifactCallFailed) as excinfo:
        await _invoke(_ctx(), _function(_boom), {"text": "hi"}, redis=_redis())
    assert "provider exploded" not in str(excinfo.value)


async def test_timeout_becomes_call_failed() -> None:
    async def _slow(ctx: ArtifactCallContext, args: _EchoInput) -> dict[str, Any]:
        await asyncio.sleep(0.5)
        return {"text": args.text}

    with pytest.raises(ArtifactCallFailed):
        await _invoke(
            _ctx(), _function(_slow, timeout_seconds=0.05), {"text": "hi"}, redis=_redis()
        )


async def test_rate_limit_blocks_past_budget() -> None:
    redis = _redis()
    ctx = _ctx()
    function = _function(_echo, rate_limit_per_minute=2)
    for _ in range(2):
        await _invoke(ctx, function, {"text": "hi"}, redis=redis)
    with pytest.raises(ArtifactCallRateLimited):
        await _invoke(ctx, function, {"text": "hi"}, redis=redis)


async def test_rate_limit_window_rotates(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = {"now": 1000.0}
    monkeypatch.setattr(time, "time", lambda: clock["now"])
    redis = _redis()
    ctx = _ctx()
    function = _function(_echo, rate_limit_per_minute=1)
    await _invoke(ctx, function, {"text": "hi"}, redis=redis)
    with pytest.raises(ArtifactCallRateLimited):
        await _invoke(ctx, function, {"text": "hi"}, redis=redis)
    clock["now"] += 61  # next minute bucket
    assert await _invoke(ctx, function, {"text": "hi"}, redis=redis) == {"text": "hi"}


async def test_rate_limit_is_per_user_and_artifact() -> None:
    redis = _redis()
    function = _function(_echo, rate_limit_per_minute=1)
    first = _ctx()
    await _invoke(first, function, {"text": "hi"}, redis=redis)
    # A different artifact (fresh context) has its own budget.
    assert await _invoke(_ctx(), function, {"text": "hi"}, redis=redis) == {"text": "hi"}
    with pytest.raises(ArtifactCallRateLimited):
        await _invoke(first, function, {"text": "hi"}, redis=redis)


async def test_dispatch_refuses_when_disabled() -> None:
    previous = config.get("artifact_bridge.enabled", True)
    config.set("artifact_bridge.enabled", False)
    try:
        with pytest.raises(ArtifactBridgeDisabled):
            await dispatch(_ctx(), "chat", {"prompt": "hi"}, redis=_redis())
    finally:
        config.set("artifact_bridge.enabled", previous)
