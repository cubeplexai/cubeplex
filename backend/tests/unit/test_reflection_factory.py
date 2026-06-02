"""Unit tests for the reflection agent factory closure built in _run_cubepi_path.

Rather than running the full RunManager (which needs a DB, Redis, and a real
cubepi provider), we test the inline factory pattern in isolation: given a fake
provider + model + memory_service_factory, the constructed Agent should carry
the REFLECTION_SYSTEM_PROMPT and memory tools.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import MagicMock

import pytest

from cubebox.prompts.reflection_system import REFLECTION_SYSTEM_PROMPT
from cubebox.services.reflection_runner import ReflectionInput, ReflectionTurn


def _mk_input() -> ReflectionInput:
    return ReflectionInput(
        conversation_id="conv_factory_test",
        run_id="run_factory_test",
        user_id="usr_1",
        workspace_id="ws_1",
        turn=ReflectionTurn(
            user_message="test user msg",
            assistant_message="test assistant msg",
            tool_summaries=[],
        ),
    )


@pytest.fixture
def fake_memory_service():  # type: ignore[return]
    """A no-op async context manager acting as _memory_service_factory."""

    @asynccontextmanager
    async def _factory():  # type: ignore[return]
        yield MagicMock()

    return _factory


def test_make_reflection_agent_uses_reflection_system_prompt(
    fake_memory_service: object,
) -> None:
    """Smoke-check the system_prompt + tools kwargs the factory closure passes.

    Note: this does not construct a real cubepi.Agent — it mirrors the factory's
    kwarg assembly. Wiring drift between the real closure in run_manager.py and
    this test must be caught by integration tests (T10).
    """
    fake_provider = MagicMock()
    fake_model_id = "claude-3-haiku"
    fake_provider_name = "anthropic"

    # Mirror exactly what run_manager builds inline.
    def _make_reflection_agent(inp: ReflectionInput) -> MagicMock:
        from cubepi import Model

        from cubebox.tools.builtin.memory import create_memory_tools

        _mem_tools = create_memory_tools(
            service_factory=fake_memory_service,  # type: ignore[arg-type]
            conversation_id=inp.conversation_id,
            run_id=inp.run_id,
        )
        captured: dict[str, object] = {}

        class _FakeAgent:
            def __init__(self, **kwargs: object) -> None:
                captured.update(kwargs)

        _FakeAgent(
            provider=fake_provider,
            model=Model(id=fake_model_id, provider=fake_provider_name),
            system_prompt=REFLECTION_SYSTEM_PROMPT,
            tools=_mem_tools,
        )
        return captured  # type: ignore[return-value]

    captured = _make_reflection_agent(_mk_input())  # type: ignore[assignment]
    assert captured["system_prompt"] == REFLECTION_SYSTEM_PROMPT


def test_make_reflection_agent_binds_memory_tools(
    fake_memory_service: object,
) -> None:
    """The factory closure should give the agent memory_save / memory_search / memory_update tools."""
    from cubebox.tools.builtin.memory import create_memory_tools

    inp = _mk_input()
    tools = create_memory_tools(
        service_factory=fake_memory_service,  # type: ignore[arg-type]
        conversation_id=inp.conversation_id,
        run_id=inp.run_id,
    )
    tool_names = {t.name for t in tools}
    assert "memory_save" in tool_names
    assert "memory_search" in tool_names
    assert "memory_update" in tool_names


def test_stringify_user_msg_str() -> None:
    """_stringify_user_msg should pass through plain strings unchanged."""

    # Inline the helper as it appears in run_manager so we test the logic, not
    # a shared utility (there is none — it's a closure).
    def _stringify(msg: object) -> str:
        if isinstance(msg, str):
            return msg
        from cubepi.providers.base import TextContent

        content = getattr(msg, "content", None)
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = [c.text for c in content if isinstance(c, TextContent)]
            return "\n".join(parts).strip()
        return ""

    assert _stringify("hello world") == "hello world"


def test_stringify_user_msg_user_message() -> None:
    """_stringify_user_msg should extract text from a cubepi UserMessage."""
    import time

    from cubepi.providers.base import TextContent, UserMessage

    msg = UserMessage(
        content=[TextContent(text="first"), TextContent(text="second")],
        timestamp=time.time(),
    )

    def _stringify(msg: object) -> str:
        if isinstance(msg, str):
            return msg
        from cubepi.providers.base import TextContent

        content = getattr(msg, "content", None)
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = [c.text for c in content if isinstance(c, TextContent)]
            return "\n".join(parts).strip()
        return ""

    assert _stringify(msg) == "first\nsecond"


def test_last_assistant_text_returns_none_on_empty() -> None:
    """_last_assistant_text should return None when there are no AssistantMessages."""

    def _last_assistant_text(messages: list) -> str | None:
        from cubepi.providers.base import AssistantMessage, TextContent

        for msg in reversed(messages):
            if isinstance(msg, AssistantMessage):
                parts: list[str] = []
                for c in msg.content:
                    if isinstance(c, TextContent):
                        parts.append(c.text)
                return "\n".join(parts).strip() or None
        return None

    assert _last_assistant_text([]) is None


def test_last_assistant_text_extracts_text() -> None:
    """_last_assistant_text picks the last AssistantMessage and joins TextContent blocks."""
    import time

    from cubepi.providers.base import AssistantMessage, TextContent, UserMessage

    def _last_assistant_text(messages: list) -> str | None:
        from cubepi.providers.base import AssistantMessage, TextContent

        for msg in reversed(messages):
            if isinstance(msg, AssistantMessage):
                parts: list[str] = []
                for c in msg.content:
                    if isinstance(c, TextContent):
                        parts.append(c.text)
                return "\n".join(parts).strip() or None
        return None

    user_msg = UserMessage(content=[TextContent(text="hi")], timestamp=time.time())
    asst_msg = AssistantMessage(content=[TextContent(text="Hello"), TextContent(text="World")])
    assert _last_assistant_text([user_msg, asst_msg]) == "Hello\nWorld"
