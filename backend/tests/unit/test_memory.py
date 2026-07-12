"""Memory tools ported to cubepi (M2.2) — unit tests."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any

import pytest
from cubepi.agent.types import AgentTool

from cubeplex.models.memory import MemoryScope, MemoryStatus, MemoryType
from cubeplex.services.memory import MemoryPermissionError
from cubeplex.services.memory_screen import MemoryScreenError
from cubeplex.tools.builtin.memory import (
    MemorySaveArgs,
    MemorySearchArgs,
    MemoryUpdateArgs,
    create_memory_tools,
)

# ---------------------------------------------------------------------------
# Fake MemoryService
# ---------------------------------------------------------------------------


def _fake_memory_item(
    memory_id: str = "mem-1",
    scope: MemoryScope = MemoryScope.PERSONAL,
    type_: MemoryType = MemoryType.PREFERENCE,
    content: str = "test content",
    confidence: float = 0.8,
) -> Any:
    """Build a minimal fake MemoryItem-shaped object."""
    # use a SimpleNamespace-like approach via a dynamic class
    return type(
        "_FakeItem",
        (),
        {
            "id": memory_id,
            "scope": scope,
            "type": type_,
            "content": content,
            "confidence": confidence,
        },
    )()


class _FakeMemoryService:
    """Test double for MemoryService."""

    def __init__(self) -> None:
        self.created: list[Any] = []
        self.updated: list[tuple[str, dict[str, Any]]] = []
        self._raise_on_create: Exception | None = None
        self._raise_on_update: Exception | None = None
        self._search_results: list[Any] = []
        self._update_result: Any = _fake_memory_item(memory_id="mem-updated")

        # MemoryService exposes .repo; provide a minimal async fake
        self.repo = _FakeRepo(self)

    async def create(self, inp: Any) -> Any:
        if self._raise_on_create is not None:
            raise self._raise_on_create
        item = _fake_memory_item(content=inp.content)
        self.created.append(inp)
        return item

    async def update(
        self,
        memory_id: str,
        *,
        content: str | None = None,
        type_: Any = None,
        confidence: float | None = None,
        status: Any = None,
    ) -> Any:
        if self._raise_on_update is not None:
            raise self._raise_on_update
        self.updated.append((memory_id, {"content": content, "type_": type_}))
        return self._update_result


class _FakeRepo:
    def __init__(self, svc: _FakeMemoryService) -> None:
        self._svc = svc

    async def list(self, **kwargs: Any) -> list[Any]:
        return self._svc._search_results


@pytest.fixture
def fake_svc() -> _FakeMemoryService:
    return _FakeMemoryService()


@pytest.fixture
def service_factory(fake_svc: _FakeMemoryService):  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def _factory():
        yield fake_svc

    return _factory


# ---------------------------------------------------------------------------
# Factory shape tests
# ---------------------------------------------------------------------------


def test_create_memory_tools_returns_three_agent_tools(
    service_factory: Any,
) -> None:
    """Factory returns exactly three cubepi.AgentTool instances."""
    tools = create_memory_tools(service_factory=service_factory)
    assert len(tools) == 3
    for t in tools:
        assert isinstance(t, AgentTool)


def test_create_memory_tools_tool_names(service_factory: Any) -> None:
    """Tool names are stable across the cubepi migration (prompt-cache contract)."""
    tools = create_memory_tools(service_factory=service_factory)
    names = {t.name for t in tools}
    assert names == {"memory_save", "memory_search", "memory_update"}


def test_create_memory_tools_parameters_are_pydantic(service_factory: Any) -> None:
    """Each tool's parameters attribute is a Pydantic BaseModel subclass."""
    from pydantic import BaseModel

    tools = create_memory_tools(service_factory=service_factory)
    for t in tools:
        assert issubclass(t.parameters, BaseModel), f"{t.name} parameters not BaseModel"


# ---------------------------------------------------------------------------
# memory_save
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_memory_save_writes_to_service(
    service_factory: Any,
    fake_svc: _FakeMemoryService,
) -> None:
    """memory_save calls service.create and returns a saved status."""
    tools = create_memory_tools(service_factory=service_factory)
    save_tool = next(t for t in tools if t.name == "memory_save")

    args = MemorySaveArgs(
        scope=MemoryScope.PERSONAL,
        type=MemoryType.PREFERENCE,
        content="user prefers dark mode",
    )
    result = await save_tool.execute("tc-1", args, signal=None, on_update=None)

    assert len(result.content) == 1
    payload = json.loads(result.content[0].text)
    assert payload["status"] == "saved"
    assert "memory_id" in payload
    assert len(fake_svc.created) == 1


@pytest.mark.asyncio
async def test_memory_save_returns_error_on_permission_error(
    service_factory: Any,
    fake_svc: _FakeMemoryService,
) -> None:
    """memory_save wraps MemoryPermissionError as an error status payload."""
    fake_svc._raise_on_create = MemoryPermissionError("workspace context required")
    tools = create_memory_tools(service_factory=service_factory)
    save_tool = next(t for t in tools if t.name == "memory_save")

    args = MemorySaveArgs(
        scope=MemoryScope.WORKSPACE,
        type=MemoryType.PROCEDURE,
        content="deploy procedure",
    )
    result = await save_tool.execute("tc-2", args, signal=None, on_update=None)
    payload = json.loads(result.content[0].text)
    assert payload["status"] == "error"
    assert "workspace" in payload["error"].lower()


@pytest.mark.asyncio
async def test_memory_save_returns_rejected_on_screen_error(
    service_factory: Any,
    fake_svc: _FakeMemoryService,
) -> None:
    """memory_save wraps MemoryScreenError as a rejected status payload."""
    fake_svc._raise_on_create = MemoryScreenError("content violates policy")
    tools = create_memory_tools(service_factory=service_factory)
    save_tool = next(t for t in tools if t.name == "memory_save")

    args = MemorySaveArgs(
        scope=MemoryScope.WORKSPACE,
        type=MemoryType.ORG_POLICY,
        content="some policy",
    )
    result = await save_tool.execute("tc-3", args, signal=None, on_update=None)
    payload = json.loads(result.content[0].text)
    assert payload["status"] == "rejected"


@pytest.mark.asyncio
async def test_memory_save_forwards_conversation_id(
    fake_svc: _FakeMemoryService,
) -> None:
    """conversation_id passed to factory is forwarded into CreateMemoryInput."""

    @asynccontextmanager
    async def _factory():
        yield fake_svc

    tools = create_memory_tools(
        service_factory=_factory,
        conversation_id="conv-xyz",
        run_id="run-abc",
    )
    save_tool = next(t for t in tools if t.name == "memory_save")
    args = MemorySaveArgs(scope=MemoryScope.PERSONAL, type=MemoryType.PREFERENCE, content="test")
    await save_tool.execute("tc-4", args, signal=None, on_update=None)

    assert fake_svc.created[0].source_conversation_id == "conv-xyz"
    assert fake_svc.created[0].source_run_id == "run-abc"


# ---------------------------------------------------------------------------
# memory_search
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_memory_search_returns_items(
    service_factory: Any,
    fake_svc: _FakeMemoryService,
) -> None:
    """memory_search returns serialised items from repo.list."""
    fake_svc._search_results = [
        _fake_memory_item("mem-1", content="dark mode preference"),
        _fake_memory_item("mem-2", content="light theme"),
    ]
    tools = create_memory_tools(service_factory=service_factory)
    search_tool = next(t for t in tools if t.name == "memory_search")

    args = MemorySearchArgs(query="theme preference")
    result = await search_tool.execute("tc-s1", args, signal=None, on_update=None)

    payload = json.loads(result.content[0].text)
    assert "items" in payload
    assert len(payload["items"]) == 2
    assert payload["items"][0]["id"] == "mem-1"
    assert payload["items"][1]["content"] == "light theme"


@pytest.mark.asyncio
async def test_memory_search_empty_result(
    service_factory: Any,
    fake_svc: _FakeMemoryService,
) -> None:
    """memory_search returns empty items list when repo finds nothing."""
    fake_svc._search_results = []
    tools = create_memory_tools(service_factory=service_factory)
    search_tool = next(t for t in tools if t.name == "memory_search")

    args = MemorySearchArgs(query="nonexistent")
    result = await search_tool.execute("tc-s2", args, signal=None, on_update=None)

    payload = json.loads(result.content[0].text)
    assert payload["items"] == []


# ---------------------------------------------------------------------------
# memory_update
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_memory_update_returns_updated_status(
    service_factory: Any,
    fake_svc: _FakeMemoryService,
) -> None:
    """memory_update calls service.update and returns updated status."""
    tools = create_memory_tools(service_factory=service_factory)
    update_tool = next(t for t in tools if t.name == "memory_update")

    args = MemoryUpdateArgs(memory_id="mem-1", content="revised content")
    result = await update_tool.execute("tc-u1", args, signal=None, on_update=None)

    payload = json.loads(result.content[0].text)
    assert payload["status"] == "updated"
    assert "memory_id" in payload
    assert len(fake_svc.updated) == 1
    assert fake_svc.updated[0][0] == "mem-1"


@pytest.mark.asyncio
async def test_memory_update_returns_error_on_lookup_error(
    service_factory: Any,
    fake_svc: _FakeMemoryService,
) -> None:
    """memory_update wraps LookupError as error status payload."""
    fake_svc._raise_on_update = LookupError("memory item not found")
    tools = create_memory_tools(service_factory=service_factory)
    update_tool = next(t for t in tools if t.name == "memory_update")

    args = MemoryUpdateArgs(memory_id="nonexistent")
    result = await update_tool.execute("tc-u2", args, signal=None, on_update=None)

    payload = json.loads(result.content[0].text)
    assert payload["status"] == "error"
    assert "not found" in payload["error"].lower()


@pytest.mark.asyncio
async def test_memory_update_returns_rejected_on_screen_error(
    service_factory: Any,
    fake_svc: _FakeMemoryService,
) -> None:
    """memory_update wraps MemoryScreenError as rejected status payload."""
    fake_svc._raise_on_update = MemoryScreenError("content violates policy")
    tools = create_memory_tools(service_factory=service_factory)
    update_tool = next(t for t in tools if t.name == "memory_update")

    args = MemoryUpdateArgs(memory_id="mem-1", content="bad content")
    result = await update_tool.execute("tc-u3", args, signal=None, on_update=None)

    payload = json.loads(result.content[0].text)
    assert payload["status"] == "rejected"


@pytest.mark.asyncio
async def test_memory_update_archive_status(
    service_factory: Any,
    fake_svc: _FakeMemoryService,
) -> None:
    """memory_update passes status=archived to service."""
    tools = create_memory_tools(service_factory=service_factory)
    update_tool = next(t for t in tools if t.name == "memory_update")

    args = MemoryUpdateArgs(memory_id="mem-1", status=MemoryStatus.ARCHIVED)
    result = await update_tool.execute("tc-u4", args, signal=None, on_update=None)

    payload = json.loads(result.content[0].text)
    assert payload["status"] == "updated"
