"""Unit: restart/delete paths handle sandbox state correctly (F5)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from cubebox.sandbox.lazy import LazySandbox
from cubebox.sandbox.manager import SandboxAttachment


def _make_lazy(catalog, sandbox, event_service=None):
    manager = MagicMock()
    manager.get_or_create = AsyncMock(
        return_value=SandboxAttachment(sandbox=sandbox, user_sandbox_id="sbx-test"),
    )
    manager.touch = AsyncMock()
    manager.renew_lease = AsyncMock()
    return LazySandbox(
        manager=manager,
        scope_type="user",
        scope_id="u1",
        user_id="u1",
        org_id="o1",
        workspace_id="w1",
        catalog=catalog,
        event_service=event_service,
    )


@pytest.mark.asyncio
async def test_sync_runs_once_per_run():
    catalog = MagicMock()
    catalog.list_enabled_for_workspace = AsyncMock(return_value=[])
    sandbox = MagicMock()
    sandbox.download = AsyncMock(side_effect=FileNotFoundError)
    sandbox.execute = AsyncMock(return_value=MagicMock(output="", exit_code=0))
    sandbox.upload = AsyncMock()
    lazy = _make_lazy(catalog, sandbox)
    await lazy.execute("first")
    await lazy.execute("second")
    # Sync should run once (hot path after first)
    assert catalog.list_enabled_for_workspace.await_count == 1


@pytest.mark.asyncio
async def test_sync_failure_retry():
    catalog = MagicMock()
    catalog.list_enabled_for_workspace = AsyncMock(
        side_effect=[
            RuntimeError("boom"),
            [],  # second succeeds
        ]
    )
    sandbox = MagicMock()
    sandbox.download = AsyncMock(side_effect=FileNotFoundError)
    sandbox.execute = AsyncMock(return_value=MagicMock(output="", exit_code=0))
    sandbox.upload = AsyncMock()
    lazy = _make_lazy(catalog, sandbox)
    await lazy.execute("first")
    await lazy.execute("second")
    # F4: failed sync does NOT set flag, so second call retries
    assert catalog.list_enabled_for_workspace.await_count == 2
