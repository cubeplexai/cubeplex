from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import MagicMock

import pytest

from cubeplex.models.memory import MemoryScope, MemorySourceType, MemoryType
from cubeplex.services.reflection_context import (
    reflection_source_active,
    set_reflection_source,
)


def test_reflection_source_default_inactive() -> None:
    assert reflection_source_active() is False


def test_reflection_source_scoped_context() -> None:
    assert reflection_source_active() is False
    with set_reflection_source():
        assert reflection_source_active() is True
    assert reflection_source_active() is False


def test_reflection_enum_value() -> None:
    assert MemorySourceType.REFLECTION.value == "reflection"


@pytest.mark.asyncio
async def test_memory_save_uses_reflection_source_when_active() -> None:
    from cubeplex.tools.builtin.memory import create_memory_tools

    created_inputs: list[Any] = []

    class _FakeSvc:
        async def create(self, inp: Any) -> Any:
            created_inputs.append(inp)
            obj = MagicMock()
            obj.id = "mem_abc"
            return obj

        repo = MagicMock()

    fake_svc = _FakeSvc()

    @asynccontextmanager  # type: ignore[misc]
    async def _factory():  # type: ignore[misc]
        yield fake_svc

    tools = create_memory_tools(
        service_factory=_factory,
        conversation_id="conv_x",
        run_id="run_y",
    )
    save_tool = next(t for t in tools if t.name == "memory_save")

    args = save_tool.parameters(
        scope=MemoryScope.PERSONAL,
        type=MemoryType.PREFERENCE,
        content="prefers Chinese",
        confidence=0.9,
    )

    with set_reflection_source():
        await save_tool.execute("tc1", args, signal=None, on_update=None)

    assert len(created_inputs) == 1
    assert created_inputs[0].source_type == MemorySourceType.REFLECTION


@pytest.mark.asyncio
async def test_memory_save_uses_conversation_source_when_inactive() -> None:
    from cubeplex.tools.builtin.memory import create_memory_tools

    created_inputs: list[Any] = []

    class _FakeSvc:
        async def create(self, inp: Any) -> Any:
            created_inputs.append(inp)
            obj = MagicMock()
            obj.id = "mem_abc"
            return obj

        repo = MagicMock()

    fake_svc = _FakeSvc()

    @asynccontextmanager  # type: ignore[misc]
    async def _factory():  # type: ignore[misc]
        yield fake_svc

    tools = create_memory_tools(
        service_factory=_factory,
        conversation_id="conv_x",
        run_id="run_y",
    )
    save_tool = next(t for t in tools if t.name == "memory_save")

    args = save_tool.parameters(
        scope=MemoryScope.PERSONAL,
        type=MemoryType.PREFERENCE,
        content="prefers English",
        confidence=0.8,
    )

    # No set_reflection_source() — ContextVar is inactive; default branch must apply.
    await save_tool.execute("tc2", args, signal=None, on_update=None)

    assert len(created_inputs) == 1
    assert created_inputs[0].source_type == MemorySourceType.CONVERSATION
