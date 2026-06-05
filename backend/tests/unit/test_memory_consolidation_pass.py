import json

import pytest

from cubebox.models.memory import MemoryScope, MemorySourceType, MemoryType
from cubebox.services import memory_consolidation as mc


class _Item:
    def __init__(self, scope):
        self.scope = scope


class _FakeRepo:
    def __init__(self, items):
        self._items = items  # id -> _Item

    async def get(self, memory_id):
        return self._items.get(memory_id)


class _FakeService:
    def __init__(self, items=None):
        self.created = []
        self.updated = []
        self.archived = []
        self.repo = _FakeRepo(items or {})

    async def create(self, inp):
        self.created.append(inp)

    async def update(self, memory_id, *, content=None, **kw):
        self.updated.append((memory_id, content))

    async def archive(self, memory_id):
        self.archived.append(memory_id)


def test_parse_ops_rejects_malformed():
    assert mc.parse_ops("not json", max_ops=10) is None
    assert mc.parse_ops('{"ops": "x"}', max_ops=10) is None
    big = json.dumps({"ops": [{"action": "archive", "id": f"m{i}"} for i in range(11)]})
    assert mc.parse_ops(big, max_ops=10) is None


def test_parse_ops_filters_invalid_ops_keeps_valid():
    raw = json.dumps(
        {
            "ops": [
                {"action": "extract", "type": "preference", "content": "likes dark mode"},
                {"action": "extract", "type": "BOGUS", "content": "x"},
                {"action": "merge", "id": "m1", "content": "merged"},
                {"action": "merge"},
                {"action": "archive", "id": "m2"},
                {"action": "frobnicate", "id": "m3"},
            ]
        }
    )
    ops = mc.parse_ops(raw, max_ops=10)
    assert ops is not None
    assert [o["action"] for o in ops] == ["extract", "merge", "archive"]


@pytest.mark.asyncio
async def test_apply_ops_forces_personal_scope_and_source():
    svc = _FakeService(items={"m1": _Item(MemoryScope.PERSONAL), "m2": _Item(MemoryScope.PERSONAL)})
    ops = [
        {"action": "extract", "type": "preference", "content": "likes dark mode"},
        {"action": "merge", "id": "m1", "content": "merged"},
        {"action": "archive", "id": "m2"},
    ]
    await mc.apply_ops(svc, ops, conversation_id="conv1", run_id="run1")
    assert len(svc.created) == 1
    inp = svc.created[0]
    assert inp.scope == MemoryScope.PERSONAL
    assert inp.type == MemoryType.PREFERENCE
    assert inp.source_type == MemorySourceType.CONSOLIDATION
    assert inp.source_conversation_id == "conv1"
    assert svc.updated == [("m1", "merged")]
    assert svc.archived == ["m2"]


@pytest.mark.asyncio
async def test_apply_ops_skips_non_personal_or_missing_targets():
    svc = _FakeService(items={"m-ws": _Item(MemoryScope.WORKSPACE)})
    ops = [
        {"action": "merge", "id": "m-ws", "content": "x"},
        {"action": "archive", "id": "m-gone"},
    ]
    await mc.apply_ops(svc, ops, conversation_id="conv1", run_id=None)
    assert svc.updated == []
    assert svc.archived == []


@pytest.mark.asyncio
async def test_run_consolidation_uses_tracer_oneshot_when_provided(monkeypatch):
    """run_consolidation must route the LLM call through ``tracer.oneshot()`` when
    a tracer is provided, so the call is recorded with conversation_id/user_id
    metadata and the oneshot_operation label."""
    import contextlib
    from unittest.mock import AsyncMock, MagicMock

    # Skip checkpointer / DB paths by patching the no-history short-circuit.
    @contextlib.asynccontextmanager
    async def _fake_init_checkpointer():
        cp = MagicMock()
        cp.load = AsyncMock(return_value=MagicMock(messages=[]))
        yield cp

    monkeypatch.setattr("cubebox.agents.checkpointer.init_checkpointer", _fake_init_checkpointer)

    # Lock helpers — return a token so we pass acquire_lock guard.
    monkeypatch.setattr(mc, "acquire_lock", AsyncMock(return_value="tok"))
    monkeypatch.setattr(mc, "release_lock", AsyncMock())
    monkeypatch.setattr(mc, "mark_consolidated", AsyncMock())
    monkeypatch.setattr(mc, "_counter", AsyncMock(return_value=0))

    # Fake tracer.oneshot() that records being called with the right metadata.
    captured: dict[str, object] = {}

    @contextlib.asynccontextmanager
    async def _fake_oneshot(*, model, operation, metadata, **_kw):
        captured["operation"] = operation
        captured["metadata"] = dict(metadata)
        session = MagicMock()
        session.generate = AsyncMock(return_value='{"ops": []}')
        yield session

    fake_tracer = MagicMock()
    fake_tracer.oneshot = _fake_oneshot

    # data.messages is empty → run_consolidation marks consolidated and
    # returns before hitting the LLM. Force a non-empty path by giving
    # the checkpointer one fake message.
    @contextlib.asynccontextmanager
    async def _fake_init_checkpointer_nonempty():
        cp = MagicMock()
        data = MagicMock()
        data.messages = [MagicMock(role="user", content=[])]
        cp.load = AsyncMock(return_value=data)
        yield cp

    monkeypatch.setattr(
        "cubebox.agents.checkpointer.init_checkpointer",
        _fake_init_checkpointer_nonempty,
    )

    # Stub MemoryRepository.list so the "existing items" SQL never runs.
    class _StubRepo:
        def __init__(self, *_a, **_kw):
            pass

        async def list(self, **_kw):
            return []

    monkeypatch.setattr("cubebox.repositories.memory.MemoryRepository", _StubRepo)

    # Session maker that just yields a MagicMock async-context.
    @contextlib.asynccontextmanager
    async def _fake_session_maker():
        yield MagicMock()

    fake_bound_model = MagicMock()

    await mc.run_consolidation(
        redis=MagicMock(),
        prefix="test",
        conversation_id="conv-xyz",
        user_id="usr-abc",
        org_id="org-1",
        workspace_id="ws-2",
        model=fake_bound_model,
        tracer=fake_tracer,
        session_maker=_fake_session_maker,
    )

    assert captured.get("operation") == "consolidate_memory"
    meta = captured.get("metadata") or {}
    assert meta["conversation_id"] == "conv-xyz"
    assert meta["user_id"] == "usr-abc"
    assert meta["workspace_id"] == "ws-2"
    assert meta["org_id"] == "org-1"


@pytest.mark.asyncio
async def test_run_consolidation_fallback_uses_provider_generate(monkeypatch):
    """Without a tracer, consolidation uses cubepi Provider.generate directly."""
    import contextlib
    from unittest.mock import AsyncMock, MagicMock

    from cubepi.providers.base import AssistantMessage, TextContent

    @contextlib.asynccontextmanager
    async def _fake_init_checkpointer_nonempty():
        cp = MagicMock()
        data = MagicMock()
        data.messages = [MagicMock(role="user", content=[])]
        cp.load = AsyncMock(return_value=data)
        yield cp

    monkeypatch.setattr(
        "cubebox.agents.checkpointer.init_checkpointer",
        _fake_init_checkpointer_nonempty,
    )
    monkeypatch.setattr(mc, "acquire_lock", AsyncMock(return_value="tok"))
    monkeypatch.setattr(mc, "release_lock", AsyncMock())
    monkeypatch.setattr(mc, "mark_consolidated", AsyncMock())
    monkeypatch.setattr(mc, "_counter", AsyncMock(return_value=0))

    class _StubRepo:
        def __init__(self, *_a, **_kw):
            pass

        async def list(self, **_kw):
            return []

    monkeypatch.setattr("cubebox.repositories.memory.MemoryRepository", _StubRepo)

    @contextlib.asynccontextmanager
    async def _fake_session_maker():
        yield MagicMock()

    fake_spec = MagicMock()
    fake_provider = MagicMock()
    fake_provider.generate = AsyncMock(
        return_value=AssistantMessage(content=[TextContent(text='{"ops": []}')])
    )
    bound_model = MagicMock()
    bound_model.provider = fake_provider
    bound_model.spec = fake_spec

    await mc.run_consolidation(
        redis=MagicMock(),
        prefix="test",
        conversation_id="conv-xyz",
        user_id="usr-abc",
        org_id="org-1",
        workspace_id="ws-2",
        model=bound_model,
        tracer=None,
        session_maker=_fake_session_maker,
    )

    fake_provider.generate.assert_awaited_once()
    call = fake_provider.generate.await_args.kwargs
    assert call["model"] is fake_spec
    assert call["system_prompt"] == mc.CONSOLIDATION_SYSTEM
    assert call["max_output_tokens"] == mc.EXTRACT_MODEL_MAX_TOKENS


@pytest.mark.asyncio
async def test_run_consolidation_fallback_treats_provider_error_as_failed_pass(
    monkeypatch,
) -> None:
    import contextlib
    from unittest.mock import AsyncMock, MagicMock

    from cubepi.providers.base import AssistantMessage

    @contextlib.asynccontextmanager
    async def _fake_init_checkpointer_nonempty():
        cp = MagicMock()
        data = MagicMock()
        data.messages = [MagicMock(role="user", content=[])]
        cp.load = AsyncMock(return_value=data)
        yield cp

    monkeypatch.setattr(
        "cubebox.agents.checkpointer.init_checkpointer",
        _fake_init_checkpointer_nonempty,
    )
    monkeypatch.setattr(mc, "acquire_lock", AsyncMock(return_value="tok"))
    release_lock = AsyncMock()
    mark_consolidated = AsyncMock()
    monkeypatch.setattr(mc, "release_lock", release_lock)
    monkeypatch.setattr(mc, "mark_consolidated", mark_consolidated)
    monkeypatch.setattr(mc, "_counter", AsyncMock(return_value=0))

    class _StubRepo:
        def __init__(self, *_a, **_kw):
            pass

        async def list(self, **_kw):
            return []

    monkeypatch.setattr("cubebox.repositories.memory.MemoryRepository", _StubRepo)

    @contextlib.asynccontextmanager
    async def _fake_session_maker():
        yield MagicMock()

    fake_provider = MagicMock()
    fake_provider.generate = AsyncMock(
        return_value=AssistantMessage(content=[], error_message="provider failed")
    )
    bound_model = MagicMock()
    bound_model.provider = fake_provider
    bound_model.spec = MagicMock()

    await mc.run_consolidation(
        redis=MagicMock(),
        prefix="test",
        conversation_id="conv-xyz",
        user_id="usr-abc",
        org_id="org-1",
        workspace_id="ws-2",
        model=bound_model,
        tracer=None,
        session_maker=_fake_session_maker,
    )

    fake_provider.generate.assert_awaited_once()
    mark_consolidated.assert_not_awaited()
    release_lock.assert_awaited_once()
