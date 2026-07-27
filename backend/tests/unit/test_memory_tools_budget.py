"""Unit tests for memory tool create budget and normalize helpers."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest

from cubeplex.models.memory import MemoryScope, MemoryType
from cubeplex.repositories.memory import normalize_memory_content
from cubeplex.services.memory import CreateMemoryInput
from cubeplex.tools.builtin.memory import MemorySaveArgs, create_memory_tools


def test_normalize_collapses_whitespace_and_punct() -> None:
    assert normalize_memory_content("  foo   bar 。") == "foo bar"
    assert normalize_memory_content("用户喜欢X。") == normalize_memory_content("用户喜欢X")
    assert normalize_memory_content("raining") != normalize_memory_content("not raining")


class _Svc:
    def __init__(self) -> None:
        self.creates = 0

    async def create(self, inp: CreateMemoryInput) -> SimpleNamespace:
        self.creates += 1
        return SimpleNamespace(id=f"mem-{self.creates}")


@pytest.mark.asyncio
async def test_memory_save_respects_max_creates() -> None:
    svc = _Svc()

    @asynccontextmanager
    async def factory():  # type: ignore[return]
        yield svc

    tools = create_memory_tools(service_factory=factory, max_creates=2)
    save = next(t for t in tools if t.name == "memory_save")
    args = MemorySaveArgs(
        scope=MemoryScope.PERSONAL,
        type=MemoryType.PREFERENCE,
        content="pref a",
    )
    r1 = await save.execute("tc1", args)
    r2 = await save.execute("tc2", args.model_copy(update={"content": "pref b"}))
    r3 = await save.execute("tc3", args.model_copy(update={"content": "pref c"}))
    body1 = json.loads(r1.content[0].text)
    body2 = json.loads(r2.content[0].text)
    body3 = json.loads(r3.content[0].text)
    assert body1["status"] == "saved"
    assert body2["status"] == "saved"
    assert body3["status"] == "error"
    assert "budget" in body3["error"]
    assert svc.creates == 2


@pytest.mark.asyncio
async def test_memory_save_permission_error_returned() -> None:
    class _Bad:
        async def create(self, inp: CreateMemoryInput) -> Any:
            from cubeplex.services.memory import MemoryPermissionError

            raise MemoryPermissionError("personal memory requires workspace context")

    @asynccontextmanager
    async def factory():  # type: ignore[return]
        yield _Bad()

    tools = create_memory_tools(service_factory=factory)
    save = next(t for t in tools if t.name == "memory_save")
    args = MemorySaveArgs(
        scope=MemoryScope.PERSONAL,
        type=MemoryType.PREFERENCE,
        content="x",
    )
    result = await save.execute("tc", args)
    body = json.loads(result.content[0].text)
    assert body["status"] == "error"
    assert "workspace" in body["error"]


@pytest.mark.asyncio
async def test_memory_search_returns_items() -> None:
    item = SimpleNamespace(
        id="mem-1",
        scope=MemoryScope.PERSONAL,
        type=MemoryType.PREFERENCE,
        content="hello",
        confidence=0.9,
    )

    class _Repo:
        async def list(self, **_kw: Any) -> list[Any]:
            return [item]

    class _Ok:
        def __init__(self) -> None:
            self.repo = _Repo()

    @asynccontextmanager
    async def factory():  # type: ignore[return]
        yield _Ok()

    tools = create_memory_tools(service_factory=factory)
    search = next(t for t in tools if t.name == "memory_search")
    from cubeplex.tools.builtin.memory import MemorySearchArgs

    result = await search.execute("tc", MemorySearchArgs(query="hello"))
    body = json.loads(result.content[0].text)
    assert body["items"][0]["id"] == "mem-1"
