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

    async def check_ready(self, timeout, polling_interval) -> None:  # noqa: ANN001
        return None

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


async def test_volume_claim_name_carries_workspace(
    session_factory, mock_encryption_backend, monkeypatch
):
    factory, fake_create = session_factory
    mgr = SandboxManager(factory, mock_encryption_backend)
    monkeypatch.setattr(mgr, "_volume_enabled", True)
    await mgr.get_or_create(
        scope_type="user", scope_id="user-1", user_id="user-1", org_id="org-1", workspace_id="ws-A"
    )
    vols = fake_create.last_volumes
    assert vols is not None
    claim = vols[0].pvc.claim_name
    assert "ws-a" in claim and "user-1" in claim


async def test_same_user_two_workspaces_get_distinct_claims(
    session_factory, mock_encryption_backend, monkeypatch
):
    factory, fake_create = session_factory
    mgr = SandboxManager(factory, mock_encryption_backend)
    monkeypatch.setattr(mgr, "_volume_enabled", True)
    await mgr.get_or_create(
        scope_type="user", scope_id="user-1", user_id="user-1", org_id="org-1", workspace_id="ws-A"
    )
    claim_a = fake_create.last_volumes[0].pvc.claim_name
    await mgr.get_or_create(
        scope_type="user", scope_id="user-1", user_id="user-1", org_id="org-1", workspace_id="ws-B"
    )
    claim_b = fake_create.last_volumes[0].pvc.claim_name
    assert claim_a != claim_b


async def test_create_reserves_before_provider_create(session_factory, mock_encryption_backend):
    factory, fake_create = session_factory
    mgr = SandboxManager(factory, mock_encryption_backend)
    await mgr.get_or_create(
        scope_type="user", scope_id="user-1", user_id="user-1", org_id="org-1", workspace_id="ws-A"
    )
    assert fake_create.calls == 1


async def test_pre_create_setup_failure_releases_reservation(
    session_factory, mock_encryption_backend, monkeypatch
):
    """If anything between `repo.reserve()` and `Sandbox.create()` raises
    (e.g. env injection on a malformed vault row), the provisioning row must
    be released — otherwise the partial unique index pins this user/workspace
    until TTL cleanup notices and the next request gets stuck polling a
    phantom winner. Regression test for codex P1 r3317495775."""
    import sqlalchemy as sa

    from cubebox.sandbox_env.injector import SandboxEnvInjector

    factory, _fake_create = session_factory
    mgr = SandboxManager(factory, mock_encryption_backend)
    mgr._exchange_host = "egress-exchange.internal"

    def boom(self, *args, **kwargs):  # noqa: ANN001
        raise RuntimeError("malformed vault row")

    monkeypatch.setattr(SandboxEnvInjector, "build", boom)

    with pytest.raises(RuntimeError, match="malformed vault row"):
        await mgr.get_or_create(
            scope_type="user",
            scope_id="user-1",
            user_id="user-1",
            org_id="org-1",
            workspace_id="ws-A",
        )

    # The provisioning row must be gone, or the next reserve() would raise
    # IntegrityError on the partial unique index.
    async with factory() as s:
        rows = (
            await s.execute(
                sa.text(
                    "SELECT id, status FROM user_sandboxes WHERE user_id=:u AND workspace_id=:w"
                ),
                {"u": "user-1", "w": "ws-A"},
            )
        ).all()
    assert rows == [], f"Expected reservation released after pre-create failure, got: {rows}"


async def test_reconnect_failure_after_create_keeps_running_row(
    session_factory, mock_encryption_backend, monkeypatch
):
    """After `promote_to_running` commits, the row references a real provider
    sandbox. If the post-create reconnect fails transiently, the row MUST stay
    `running` so the reaper can clean it up or the next reuse can re-test it.
    Deleting the row here would orphan the provider sandbox. Regression test
    for codex P1 r3317495778."""
    import sqlalchemy as sa

    factory, fake_create = session_factory
    mgr = SandboxManager(factory, mock_encryption_backend)

    call_count = {"connect": 0}

    async def fake_connect(sandbox_id, **kwargs):  # noqa: ANN001
        call_count["connect"] += 1
        # The create-time post-reserve reconnect uses skip_health_check=True.
        # Fail on that specific call so the provider sandbox is committed
        # but the rebind fails.
        if kwargs.get("skip_health_check"):
            raise RuntimeError("transient reconnect failure")
        return _FakeRaw()

    monkeypatch.setattr(opensandbox.Sandbox, "connect", staticmethod(fake_connect))

    from cubebox.sandbox.base import SandboxError

    with pytest.raises(SandboxError, match="reconnect failed"):
        await mgr.get_or_create(
            scope_type="user",
            scope_id="user-1",
            user_id="user-1",
            org_id="org-1",
            workspace_id="ws-A",
        )

    # Row must remain `running` — not deleted — so the reaper / next reuse
    # owns its lifecycle.
    async with factory() as s:
        rows = (
            await s.execute(
                sa.text(
                    "SELECT status, sandbox_id FROM user_sandboxes "
                    "WHERE user_id=:u AND workspace_id=:w"
                ),
                {"u": "user-1", "w": "ws-A"},
            )
        ).all()
    assert len(rows) == 1, f"Expected the running row to remain, got: {rows}"
    assert rows[0][0] == "running"
    assert not rows[0][1].startswith("pending-"), (
        f"Row still has a placeholder sandbox_id — promote_to_running didn't "
        f"commit before the reconnect failure: {rows[0]}"
    )
