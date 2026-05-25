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
