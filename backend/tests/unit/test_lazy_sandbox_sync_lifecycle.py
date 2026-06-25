"""Unit: LazySandbox sync flag / lock / recreate-reset invariants.

If F3/F4/F5 regress, these tests catch it.

F3: two concurrent execute calls must not both run _sync_skills → independent
    _sync_lock serialises them; double-check pattern lets the second skip.
F4: a sync failure must NOT set the flag; the next execute must retry.
F5: sandbox recreate (execute / upload failure path) must reset
    _synced_for_this_run so the new sandbox gets synced.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from cubebox.sandbox.lazy import LazySandbox


def _make_lazy(catalog: object, sandbox: object) -> LazySandbox:
    """Construct a LazySandbox whose _ensure already returns the given sandbox."""
    manager = MagicMock()
    manager.get_or_create = AsyncMock(return_value=sandbox)
    manager.touch = AsyncMock()
    manager.renew_lease = AsyncMock()
    return LazySandbox(
        manager=manager,  # type: ignore[arg-type]
        scope_type="user",
        scope_id="u1",
        user_id="u1",
        org_id="o1",
        workspace_id="w1",
        catalog=catalog,  # type: ignore[arg-type]
    )


def _make_sandbox() -> MagicMock:
    """Fake sandbox: download returns [] (no manifest → cold path treated as empty)."""
    sandbox = MagicMock()
    sandbox.download = AsyncMock(return_value=[])  # no manifest file
    sandbox.upload = AsyncMock()
    sandbox.execute = AsyncMock(return_value=MagicMock(output="", exit_code=0))
    return sandbox


@pytest.mark.asyncio
async def test_sync_runs_once_per_run() -> None:
    catalog = MagicMock()
    catalog.list_enabled_for_workspace = AsyncMock(return_value=[])
    sandbox = _make_sandbox()
    lazy = _make_lazy(catalog, sandbox)

    await lazy.execute("true")
    await lazy.execute("true")

    # list_enabled_for_workspace called exactly once (flag set after first success)
    assert catalog.list_enabled_for_workspace.await_count == 1


@pytest.mark.asyncio
async def test_sync_failure_does_not_set_flag_so_next_call_retries() -> None:
    catalog = MagicMock()
    catalog.list_enabled_for_workspace = AsyncMock(
        side_effect=[
            RuntimeError("first sync boom"),
            [],  # second attempt succeeds
        ]
    )
    sandbox = _make_sandbox()
    lazy = _make_lazy(catalog, sandbox)

    await lazy.execute("true")  # sync fails, flag NOT set
    await lazy.execute("true")  # second call retries sync, succeeds

    assert catalog.list_enabled_for_workspace.await_count == 2


@pytest.mark.asyncio
async def test_concurrent_first_calls_only_sync_once() -> None:
    catalog = MagicMock()
    catalog.list_enabled_for_workspace = AsyncMock(return_value=[])
    sandbox = _make_sandbox()
    lazy = _make_lazy(catalog, sandbox)

    await asyncio.gather(lazy.execute("a"), lazy.execute("b"), lazy.execute("c"))

    # Double-check pattern inside _sync_lock ensures only one sync runs
    assert catalog.list_enabled_for_workspace.await_count == 1


@pytest.mark.asyncio
async def test_sandbox_recreate_resets_sync_flag() -> None:
    catalog = MagicMock()
    catalog.list_enabled_for_workspace = AsyncMock(return_value=[])
    sandbox = _make_sandbox()

    # First execute succeeds; second execute raises (simulates dead sandbox);
    # recreate then succeeds.
    call_count = {"n": 0}

    async def flaky_exec(*a: object, **kw: object) -> MagicMock:
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise RuntimeError("dead sandbox")
        return MagicMock(output="", exit_code=0)

    sandbox.execute = AsyncMock(side_effect=flaky_exec)
    lazy = _make_lazy(catalog, sandbox)

    await lazy.execute("first")  # sync runs (count=1)
    await lazy.execute("recreate-path")  # 2nd execute fails, recreate, sync again (count=2)

    assert catalog.list_enabled_for_workspace.await_count == 2
