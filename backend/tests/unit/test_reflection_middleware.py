"""Unit tests for ReflectionMiddleware."""

from __future__ import annotations

import pytest
from cubepi.agent.types import AgentContext
from cubepi.providers.base import UserMessage

from cubebox.middleware.reflection import ReflectionMiddleware
from cubebox.prompts.reflection import REFLECTION_PROMPT


def _mk_ctx() -> AgentContext:
    return AgentContext(system_prompt="", messages=[])


@pytest.mark.asyncio
async def test_on_run_end_returns_user_message() -> None:
    mw = ReflectionMiddleware()
    result = await mw.on_run_end(_mk_ctx())
    assert result is not None
    assert len(result) == 1
    assert isinstance(result[0], UserMessage)


@pytest.mark.asyncio
async def test_on_run_end_content_is_reflection_prompt() -> None:
    mw = ReflectionMiddleware()
    result = await mw.on_run_end(_mk_ctx())
    assert result is not None
    msg = result[0]
    assert isinstance(msg, UserMessage)
    text = msg.content[0].text  # type: ignore[attr-defined]
    assert text == REFLECTION_PROMPT


@pytest.mark.asyncio
async def test_on_run_end_metadata_is_reflection() -> None:
    mw = ReflectionMiddleware()
    result = await mw.on_run_end(_mk_ctx())
    assert result is not None
    msg = result[0]
    assert isinstance(msg, UserMessage)
    assert msg.metadata.get("is_reflection") is True
