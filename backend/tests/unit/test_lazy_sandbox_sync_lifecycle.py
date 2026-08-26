"""Unit: LazySandbox sync flag / lock / recreate-reset invariants.

If F3/F4/F5 regress, these tests catch it.

F3: two concurrent execute calls must not both run _sync_skills → independent
    _sync_lock serialises them; double-check pattern lets the second skip.
F4: a sync failure must NOT set the flag; the next execute must retry.
F5: sandbox recreate (execute / upload failure path) must advance the sync
    generation so the new sandbox gets synced.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from cubeplex.sandbox.lazy import LazySandbox
from cubeplex.sandbox.manager import SandboxAttachment


def _make_lazy(catalog: object, sandbox: object, event_service: object = None) -> LazySandbox:
    """Construct a LazySandbox whose _ensure already returns the given sandbox."""
    manager = MagicMock()
    manager.get_or_create = AsyncMock(
        return_value=SandboxAttachment(sandbox=sandbox, user_sandbox_id="uss-test"),  # type: ignore[arg-type]
    )
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
        event_service=event_service,  # type: ignore[arg-type]
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
async def test_refresh_skills_resyncs_an_initialized_sandbox() -> None:
    """A skill installed mid-run must reach a sandbox already synced this run."""
    installed_skill = SimpleNamespace(
        name="org:slides",
        version="1.0.0",
        skill_version_id="skv-slides",
        content_hash="sha256:slides",
        storage_prefix="skills/slides/1.0.0/",
    )
    catalog = MagicMock()
    catalog.list_enabled_for_workspace = AsyncMock(
        side_effect=[[], [installed_skill]],
    )
    catalog.list_files_for_sandbox_sync = AsyncMock(
        return_value=[("SKILL.md", b"# Slides")],
    )
    sandbox = _make_sandbox()
    lazy = _make_lazy(catalog, sandbox)

    await lazy.execute("true")
    refresh_skills = getattr(lazy, "refresh_skills", None)

    assert refresh_skills is not None, "LazySandbox must expose a mid-run skill refresh"
    await refresh_skills()

    assert catalog.list_enabled_for_workspace.await_count == 2
    catalog.list_files_for_sandbox_sync.assert_awaited_once_with(
        "skv-slides",
        storage_prefix="skills/slides/1.0.0/",
    )


@pytest.mark.asyncio
async def test_refresh_skills_keeps_an_uninitialized_sandbox_lazy() -> None:
    catalog = MagicMock()
    catalog.list_enabled_for_workspace = AsyncMock(return_value=[])
    sandbox = _make_sandbox()
    lazy = _make_lazy(catalog, sandbox)
    refresh_skills = getattr(lazy, "refresh_skills", None)

    assert refresh_skills is not None, "LazySandbox must expose a mid-run skill refresh"
    await refresh_skills()

    assert lazy.initialized is False
    lazy._manager.get_or_create.assert_not_awaited()
    catalog.list_enabled_for_workspace.assert_not_awaited()


@pytest.mark.asyncio
async def test_refresh_does_not_mark_a_replacement_sandbox_synced() -> None:
    """An old-sandbox refresh must not suppress sync on a concurrent replacement."""
    refresh_download_started = asyncio.Event()
    allow_refresh_download = asyncio.Event()
    command_started = asyncio.Event()
    allow_command_failure = asyncio.Event()

    old_sandbox = _make_sandbox()
    old_download_count = 0

    async def old_download(_paths: list[str]) -> list[tuple[str, bytes]]:
        nonlocal old_download_count
        old_download_count += 1
        if old_download_count == 2:
            refresh_download_started.set()
            await allow_refresh_download.wait()
        return []

    old_execute_count = 0

    async def old_execute(*_args: object, **_kwargs: object) -> MagicMock:
        nonlocal old_execute_count
        old_execute_count += 1
        if old_execute_count == 2:
            command_started.set()
            await allow_command_failure.wait()
            raise RuntimeError("old sandbox died")
        return MagicMock(output="", exit_code=0)

    old_sandbox.download = AsyncMock(side_effect=old_download)
    old_sandbox.execute = AsyncMock(side_effect=old_execute)
    new_sandbox = _make_sandbox()

    catalog = MagicMock()
    catalog.list_enabled_for_workspace = AsyncMock(return_value=[])
    lazy = _make_lazy(catalog, old_sandbox)
    lazy._manager.get_or_create = AsyncMock(
        side_effect=[
            SandboxAttachment(sandbox=old_sandbox, user_sandbox_id="uss-old"),
            SandboxAttachment(sandbox=new_sandbox, user_sandbox_id="uss-new"),
        ]
    )

    await lazy.execute("initial")
    command_task = asyncio.create_task(lazy.execute("fails"))
    await command_started.wait()

    refresh_task = asyncio.create_task(lazy.refresh_skills())
    await refresh_download_started.wait()
    allow_command_failure.set()
    for _ in range(100):
        if lazy._manager.get_or_create.await_count == 2:
            break
        await asyncio.sleep(0)
    assert lazy._manager.get_or_create.await_count == 2

    allow_refresh_download.set()
    await asyncio.gather(command_task, refresh_task)

    assert catalog.list_enabled_for_workspace.await_count == 3
    new_sandbox.download.assert_awaited_once()


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
async def test_execute_keepalives_touch_and_lease_while_command_runs() -> None:
    """A long execute must refresh last_activity and the in-use lease."""
    catalog = MagicMock()
    catalog.list_enabled_for_workspace = AsyncMock(return_value=[])
    sandbox = _make_sandbox()

    async def slow_execute(
        command: str,
        *,
        timeout: int | None = None,
        envs: object = None,
        as_root: bool = False,
    ) -> object:
        del command, timeout, envs, as_root
        await asyncio.sleep(0.12)
        return MagicMock(output="", exit_code=0)

    sandbox.id = "sbx-keep"
    sandbox.execute = slow_execute
    lazy = _make_lazy(catalog, sandbox)
    lazy._keepalive_interval_seconds = 0.04

    await lazy.execute("pip install foo", timeout=300)

    # start (_ensure_with_retry) + at least one mid-command beat
    assert lazy._manager.touch.await_count >= 2
    assert lazy._manager.renew_lease.await_count >= 2
    # Lease window stays the construction default (None → manager 300s),
    # not the command timeout.
    for call in lazy._manager.renew_lease.await_args_list:
        assert call.kwargs["lease_seconds"] is None


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


@pytest.mark.asyncio
async def test_no_manifest_write_when_collect_files_empty() -> None:
    """If to_push is non-empty but list_files_for_sandbox_sync returns [],
    _sync_skills must NOT write the manifest — leaving it stale so the next
    sync retries the push (Finding 3 guard).
    """
    from cubeplex.sandbox.lazy import _sync_skills
    from cubeplex.skills.sync_manifest import MANIFEST_PATH

    # Build a minimal ResolvedSkill-like stub that satisfies ResolvedLike.
    skill_stub = MagicMock()
    skill_stub.name = "probe"
    skill_stub.version = "1.0.0"
    skill_stub.skill_version_id = "sv-1"
    skill_stub.storage_prefix = "skills/probe/1.0.0/"
    skill_stub.content_hash = "abc123"

    catalog = MagicMock()
    # Catalog reports one enabled skill
    catalog.list_enabled_for_workspace = AsyncMock(return_value=[skill_stub])
    # But fetching files returns nothing (storage inconsistency)
    catalog.list_files_for_sandbox_sync = AsyncMock(return_value=[])

    sandbox = _make_sandbox()
    # No manifest on disk → cold path
    sandbox.download = AsyncMock(side_effect=FileNotFoundError(MANIFEST_PATH))

    await _sync_skills(
        catalog=catalog,
        workspace_id="w1",
        org_id="o1",
        sandbox=sandbox,
    )

    # upload must NOT have been called with the manifest path
    manifest_uploads = [
        c for c in sandbox.upload.call_args_list if any(MANIFEST_PATH in p for p, _ in c.args[0])
    ]
    assert manifest_uploads == [], (
        "manifest must not be written when to_push > 0 but no files were collected"
    )


@pytest.mark.asyncio
async def test_event_service_called_on_success() -> None:
    catalog = MagicMock()
    # Returning enabled list with one skill triggers a "success" sync
    fake_skill = SimpleNamespace(
        name="probe",
        version="1.0.0",
        skill_version_id="skv_a",
        content_hash="sha256:abc",
        storage_prefix="x/",
    )
    catalog.list_enabled_for_workspace = AsyncMock(return_value=[fake_skill])
    catalog.list_files_for_sandbox_sync = AsyncMock(return_value=[("SKILL.md", b"hi")])
    sandbox = MagicMock()
    sandbox.download = AsyncMock(side_effect=FileNotFoundError)  # cold
    sandbox.upload = AsyncMock()
    sandbox.execute = AsyncMock(return_value=MagicMock(output="", exit_code=0))
    event_service = MagicMock()
    event_service.record = AsyncMock()

    lazy = _make_lazy(catalog, sandbox, event_service=event_service)
    await lazy.execute("true")

    assert event_service.record.await_count == 1
    call = event_service.record.await_args
    assert call.kwargs["result"].status == "success"
    assert call.kwargs["user_sandbox_id"] == "uss-test"


@pytest.mark.asyncio
async def test_event_service_not_called_on_noop() -> None:
    catalog = MagicMock()
    catalog.list_enabled_for_workspace = AsyncMock(return_value=[])  # empty desired
    sandbox = MagicMock()
    sandbox.download = AsyncMock(side_effect=FileNotFoundError)  # empty manifest
    sandbox.upload = AsyncMock()
    sandbox.execute = AsyncMock(return_value=MagicMock(output="", exit_code=0))
    event_service = MagicMock()
    event_service.record = AsyncMock()

    lazy = _make_lazy(catalog, sandbox, event_service=event_service)
    await lazy.execute("true")

    # Empty manifest + empty desired = noop → no event
    assert event_service.record.await_count == 0


@pytest.mark.asyncio
async def test_event_service_called_on_failed_but_flag_not_set() -> None:
    catalog = MagicMock()
    catalog.list_enabled_for_workspace = AsyncMock(side_effect=RuntimeError("boom"))
    sandbox = MagicMock()
    sandbox.download = AsyncMock(side_effect=FileNotFoundError)
    sandbox.upload = AsyncMock()
    sandbox.execute = AsyncMock(return_value=MagicMock(output="", exit_code=0))
    event_service = MagicMock()
    event_service.record = AsyncMock()

    lazy = _make_lazy(catalog, sandbox, event_service=event_service)
    await lazy.execute("first")
    await lazy.execute("second")

    # Both attempts call event service (F4: failed doesn't set flag)
    assert event_service.record.await_count == 2
    for call in event_service.record.await_args_list:
        assert call.kwargs["result"].status == "failed"


@pytest.mark.asyncio
async def test_event_service_swallow_exception() -> None:
    catalog = MagicMock()
    fake_skill = SimpleNamespace(
        name="probe",
        version="1.0.0",
        skill_version_id="skv_a",
        content_hash="sha256:abc",
        storage_prefix="x/",
    )
    catalog.list_enabled_for_workspace = AsyncMock(return_value=[fake_skill])
    catalog.list_files_for_sandbox_sync = AsyncMock(return_value=[("SKILL.md", b"hi")])
    sandbox = MagicMock()
    sandbox.download = AsyncMock(side_effect=FileNotFoundError)
    sandbox.upload = AsyncMock()
    sandbox.execute = AsyncMock(return_value=MagicMock(output="", exit_code=0))
    event_service = MagicMock()
    event_service.record = AsyncMock(side_effect=RuntimeError("db down"))

    lazy = _make_lazy(catalog, sandbox, event_service=event_service)
    result = await lazy.execute("true")
    # Execute completes successfully even though event write blew up
    assert result.output == ""
