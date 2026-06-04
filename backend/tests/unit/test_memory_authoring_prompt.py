import pytest

from cubebox.prompts.memory import MEMORY_AUTHORING_BLOCK


def test_authoring_block_covers_every_type_trigger():
    block = MEMORY_AUTHORING_BLOCK
    assert "memory_save" in block
    for t in ("preference", "correction", "procedure", "project_fact", "decision", "org_policy"):
        assert t in block
    assert "personal" in block.lower()
    assert "explicitly" in block.lower()


@pytest.mark.asyncio
async def test_transform_system_prompt_injects_authoring_without_pinned(monkeypatch):
    from contextlib import asynccontextmanager

    from cubebox.middleware.memory import MemoryMiddleware

    class _Repo:
        pass

    @asynccontextmanager
    async def _factory():
        yield _Repo()

    async def _empty(repo):
        return ""

    monkeypatch.setattr("cubebox.middleware.memory._render_pinned", _empty)

    mw = MemoryMiddleware(repo_factory=_factory)
    out = await mw.transform_system_prompt("BASE", ctx=object())
    assert "BASE" in out
    assert "memory_save" in out  # authoring block injected even with no pinned memory
