"""Unit tests for SandboxManager.get_or_create (Task 6).

Drives the manager against a fake ``opensandbox.Sandbox`` and the real (sqlite)
repo so we can assert reserve-before-create ordering, the workspace-scoped PVC
claim name, and the same-user-across-workspaces distinct-claims invariant.
"""

import opensandbox
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from cubebox.sandbox.manager import SandboxManager


class _FakeRaw:
    _counter = 0

    def __init__(self) -> None:
        _FakeRaw._counter += 1
        self.id = f"prov-{_FakeRaw._counter}"

    async def is_healthy(self) -> bool:
        return True

    async def close(self) -> None:
        return None


@pytest.fixture
async def session_factory(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def fake_create(image, **kwargs):  # noqa: ANN001
        fake_create.calls += 1
        fake_create.last_volumes = kwargs.get("volumes")
        fake_create.last_image = image
        return _FakeRaw()

    fake_create.calls = 0
    fake_create.last_volumes = None
    fake_create.last_image = None

    async def fake_connect(sandbox_id, **kwargs):  # noqa: ANN001
        return _FakeRaw()

    monkeypatch.setattr(opensandbox.Sandbox, "create", staticmethod(fake_create))
    monkeypatch.setattr(opensandbox.Sandbox, "connect", staticmethod(fake_connect))
    yield factory, fake_create
    await engine.dispose()


async def test_volume_claim_name_carries_workspace(session_factory, monkeypatch):
    factory, fake_create = session_factory
    mgr = SandboxManager(factory)
    monkeypatch.setattr(mgr, "_volume_enabled", True)
    await mgr.get_or_create("user-1", org_id="org-1", workspace_id="ws-A")
    vols = fake_create.last_volumes
    assert vols is not None
    claim = vols[0].pvc.claim_name
    assert "ws-a" in claim and "user-1" in claim


async def test_same_user_two_workspaces_get_distinct_claims(session_factory, monkeypatch):
    factory, fake_create = session_factory
    mgr = SandboxManager(factory)
    monkeypatch.setattr(mgr, "_volume_enabled", True)
    await mgr.get_or_create("user-1", org_id="org-1", workspace_id="ws-A")
    claim_a = fake_create.last_volumes[0].pvc.claim_name
    await mgr.get_or_create("user-1", org_id="org-1", workspace_id="ws-B")
    claim_b = fake_create.last_volumes[0].pvc.claim_name
    assert claim_a != claim_b


async def test_create_reserves_before_provider_create(session_factory):
    factory, fake_create = session_factory
    mgr = SandboxManager(factory)
    await mgr.get_or_create("user-1", org_id="org-1", workspace_id="ws-A")
    assert fake_create.calls == 1
