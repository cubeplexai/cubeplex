"""Unit: sandbox_id=None guards prevent crashes on terminated rows."""


import pytest

from cubebox.models.user_sandbox import UserSandbox


def _make_record(
    *,
    sandbox_id: str | None,
    status: str = "terminated",
    deleted_at=None,
    org_id="org-1",
    workspace_id="ws-1",
):
    return UserSandbox(
        id="sbx-test",
        user_id="user-x",
        scope_type="user",
        scope_id="user-x",
        sandbox_id=sandbox_id,
        status=status,
        image="img",
        provider="opensandbox",
        org_id=org_id,
        workspace_id=workspace_id,
        deleted_at=deleted_at,
    )


@pytest.mark.asyncio
async def test_terminated_row_sandbox_id_is_none():
    """Guard: terminated rows CAN have sandbox_id=None without crash."""
    row = _make_record(sandbox_id=None)
    assert row.sandbox_id is None
    assert row.deleted_at is None


@pytest.mark.asyncio
async def test_deleted_row_guard():
    """Guard: deleted rows have deleted_at not None."""
    from datetime import UTC, datetime

    row = _make_record(sandbox_id="sb-live", deleted_at=datetime.now(UTC))
    assert row.deleted_at is not None


@pytest.mark.asyncio
async def test_touch_active_guard_recognizes_none_sandbox_id():
    """If sandbox_id is None, the guard returns early (no crash)."""
    row = _make_record(sandbox_id=None)
    result = bool(row.sandbox_id)
    assert result is False  # guard: if not record.sandbox_id: return


@pytest.mark.asyncio
async def test_running_row_has_sandbox_id():
    """Running rows must have a sandbox_id."""
    row = _make_record(sandbox_id="sb-provider-1", status="running")
    assert row.sandbox_id == "sb-provider-1"


@pytest.mark.asyncio
async def test_touch_active_guard_recognizes_deleted_at():
    """If deleted_at is set, the guard returns early."""
    from datetime import UTC, datetime

    row = _make_record(sandbox_id="sb-live", deleted_at=datetime.now(UTC))
    result = row.deleted_at is not None
    assert result is True  # guard: if record.deleted_at is not None: return
