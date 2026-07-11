"""Unit tests for the scheduled_tasks agent capability input models and handlers."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from cubebox.agents.actions.capabilities.scheduled_tasks import (
    SCHEDULED_TASKS_CAPABILITY,
    CreateInput,
    UpdateInput,
    _handle_create,
    _handle_update,
)
from cubebox.agents.actions.context import ScopeContext
from cubebox.agents.actions.types import ActionInvalidInput, AgentOperation
from cubebox.models.membership import Role


def _ctx(conversation_id: str | None = "conv-test") -> ScopeContext:
    return ScopeContext(
        org_id="org-test",
        workspace_id="ws-test",
        user_id="usr-test",
        role=Role.MEMBER,
        conversation_id=conversation_id,
    )


# ---------------------------------------------------------------------------
# CreateInput.schedule — nested discriminated union
# ---------------------------------------------------------------------------


def test_create_cron_schedule_parses() -> None:
    inp = CreateInput(
        name="morning-reply",
        prompt="reply to bigv",
        schedule={"kind": "cron", "cron_expr": "0 9 * * *"},
    )
    assert inp.schedule.kind == "cron"
    assert inp.schedule.cron_expr == "0 9 * * *"
    assert inp.schedule.timezone == "UTC"


def test_create_interval_schedule_parses() -> None:
    inp = CreateInput(
        name="poll",
        prompt="poll feed",
        schedule={"kind": "interval", "interval_seconds": 1800},
    )
    assert inp.schedule.kind == "interval"
    assert inp.schedule.interval_seconds == 1800


def test_create_once_schedule_parses() -> None:
    inp = CreateInput(
        name="remind",
        prompt="remind",
        schedule={"kind": "once", "run_at": "2026-06-10T15:00:00+00:00"},
    )
    assert inp.schedule.kind == "once"
    assert inp.schedule.run_at == datetime(2026, 6, 10, 15, 0, tzinfo=UTC)


def test_create_cron_without_cron_expr_rejected_at_parse() -> None:
    with pytest.raises(ValidationError) as exc_info:
        CreateInput(
            name="x",
            prompt="y",
            schedule={"kind": "cron"},
        )
    # The pydantic error must name the missing field at the schedule level.
    assert "cron_expr" in str(exc_info.value)


def test_create_interval_without_seconds_rejected_at_parse() -> None:
    with pytest.raises(ValidationError) as exc_info:
        CreateInput(
            name="x",
            prompt="y",
            schedule={"kind": "interval"},
        )
    assert "interval_seconds" in str(exc_info.value)


def test_create_once_without_run_at_rejected_at_parse() -> None:
    with pytest.raises(ValidationError) as exc_info:
        CreateInput(
            name="x",
            prompt="y",
            schedule={"kind": "once"},
        )
    assert "run_at" in str(exc_info.value)


def test_create_unknown_kind_rejected() -> None:
    with pytest.raises(ValidationError):
        CreateInput(
            name="x",
            prompt="y",
            schedule={"kind": "yearly", "cron_expr": "0 0 1 1 *"},
        )


# ---------------------------------------------------------------------------
# _handle_create — flattens nested union into the service dict
# ---------------------------------------------------------------------------


def _fake_task_from_data(data: dict) -> AsyncMock:
    task = AsyncMock()
    task.id = "stask-1"
    task.name = data["name"]
    task.status = "active"
    task.schedule_kind = data["schedule_kind"]
    task.cron_expr = data.get("cron_expr")
    task.interval_seconds = data.get("interval_seconds")
    task.timezone = data.get("timezone", "UTC")
    task.prompt = data["prompt"]
    task.target_mode = data["target_mode"]
    task.next_fire_at = datetime(2026, 6, 4, 9, 0, tzinfo=UTC)
    task.last_fired_at = None
    return task


def _session() -> AsyncMock:
    """Session stub: no Conversation row unless a test overrides ``get``."""
    session = AsyncMock()
    session.get = AsyncMock(return_value=None)
    return session


@pytest.mark.asyncio
async def test_handle_create_cron_flattens_to_service_dict() -> None:
    captured: dict = {}

    async def fake_create(ctx, session, data):  # type: ignore[no-untyped-def]
        captured.update(data)
        return _fake_task_from_data(data)

    from cubebox.agents.actions.capabilities import scheduled_tasks as cap

    inp = CreateInput(
        name="morning-reply",
        prompt="reply to bigv",
        schedule={"kind": "cron", "cron_expr": "0 9 * * *", "timezone": "Asia/Shanghai"},
    )
    with (
        patch.object(cap._svc, "create", new=fake_create),
        patch(
            "cubebox.services.schedule_destination.resolve_im_destination_for_conversation",
            new=AsyncMock(return_value=None),
        ),
    ):
        await _handle_create(_ctx(), _session(), inp)

    assert captured["schedule_kind"] == "cron"
    assert captured["cron_expr"] == "0 9 * * *"
    assert captured["interval_seconds"] is None
    assert captured["run_at"] is None
    assert captured["timezone"] == "Asia/Shanghai"
    assert captured["target_mode"] == "new_each_run"
    assert captured["target_conversation_id"] is None


@pytest.mark.asyncio
async def test_handle_create_target_current_conversation_uses_ctx_id() -> None:
    """Non-IM conversation: current_conversation → fixed + ctx conversation id."""
    captured: dict = {}

    async def fake_create(ctx, session, data):  # type: ignore[no-untyped-def]
        captured.update(data)
        return _fake_task_from_data(data)

    from cubebox.agents.actions.capabilities import scheduled_tasks as cap

    inp = CreateInput(
        name="x",
        prompt="y",
        schedule={"kind": "cron", "cron_expr": "0 9 * * *"},
        target="current_conversation",
    )
    with (
        patch.object(cap._svc, "create", new=fake_create),
        patch(
            "cubebox.services.schedule_destination.resolve_im_destination_for_conversation",
            new=AsyncMock(return_value=None),
        ),
    ):
        await _handle_create(_ctx("conv-abc"), _session(), inp)

    assert captured["target_mode"] == "fixed"
    assert captured["target_conversation_id"] == "conv-abc"
    assert captured.get("im_account_id") is None


@pytest.mark.asyncio
async def test_handle_create_current_conversation_on_im_upgrades_to_im_channel() -> None:
    """IM-bound conversation: current_conversation → im_channel (not fixed)."""
    from cubebox.services.schedule_destination import ImLinkSnapshot

    captured: dict = {}

    async def fake_create(ctx, session, data):  # type: ignore[no-untyped-def]
        captured.update(data)
        return _fake_task_from_data(data)

    from cubebox.agents.actions.capabilities import scheduled_tasks as cap

    im = ImLinkSnapshot(
        im_account_id="imac-1",
        im_channel_id="oc-feishu",
        im_scope_key="dm",
        im_scope_kind="dm",
    )
    inp = CreateInput(
        name="x",
        prompt="y",
        schedule={"kind": "cron", "cron_expr": "0 9 * * *"},
        target="current_conversation",
    )
    with (
        patch.object(cap._svc, "create", new=fake_create),
        patch(
            "cubebox.services.schedule_destination.resolve_im_destination_for_conversation",
            new=AsyncMock(return_value=im),
        ),
    ):
        await _handle_create(_ctx("conv-im"), _session(), inp)

    assert captured["target_mode"] == "im_channel"
    assert captured["target_conversation_id"] is None
    assert captured["im_account_id"] == "imac-1"
    assert captured["im_channel_id"] == "oc-feishu"
    assert captured["im_scope_key"] == "dm"
    assert captured["im_scope_kind"] == "dm"


@pytest.mark.asyncio
async def test_handle_create_target_current_conversation_without_ctx_raises() -> None:
    inp = CreateInput(
        name="x",
        prompt="y",
        schedule={"kind": "cron", "cron_expr": "0 9 * * *"},
        target="current_conversation",
    )
    with pytest.raises(ActionInvalidInput, match="current_conversation"):
        await _handle_create(_ctx(conversation_id=None), AsyncMock(), inp)


# ---------------------------------------------------------------------------
# UpdateInput.schedule — same nested union, optional
# ---------------------------------------------------------------------------


def test_update_without_schedule_parses() -> None:
    inp = UpdateInput(task_id="stask-1", name="renamed")
    assert inp.schedule is None
    assert inp.name == "renamed"


def test_update_with_schedule_parses() -> None:
    inp = UpdateInput(
        task_id="stask-1",
        schedule={"kind": "interval", "interval_seconds": 600},
    )
    assert inp.schedule is not None
    assert inp.schedule.kind == "interval"
    assert inp.schedule.interval_seconds == 600


def test_update_cron_without_cron_expr_rejected() -> None:
    with pytest.raises(ValidationError) as exc_info:
        UpdateInput(task_id="stask-1", schedule={"kind": "cron"})
    assert "cron_expr" in str(exc_info.value)


@pytest.mark.asyncio
async def test_handle_update_flattens_schedule() -> None:
    captured: dict = {}

    async def fake_update(ctx, session, task_id, data):  # type: ignore[no-untyped-def]
        captured["task_id"] = task_id
        captured.update(data)
        task = AsyncMock()
        task.id = task_id
        task.name = data.get("name") or "n"
        task.status = "active"
        task.schedule_kind = data.get("schedule_kind") or "interval"
        task.cron_expr = data.get("cron_expr")
        task.interval_seconds = data.get("interval_seconds") or 600
        task.timezone = "UTC"
        task.prompt = "p"
        task.target_mode = "new_each_run"
        task.next_fire_at = None
        task.last_fired_at = None
        return task

    from cubebox.agents.actions.capabilities import scheduled_tasks as cap

    inp = UpdateInput(
        task_id="stask-1",
        schedule={"kind": "interval", "interval_seconds": 600},
    )
    with patch.object(cap._svc, "update", new=fake_update):
        await _handle_update(_ctx(), AsyncMock(), inp)

    assert captured["task_id"] == "stask-1"
    assert captured["schedule_kind"] == "interval"
    assert captured["interval_seconds"] == 600
    # Untouched fields must NOT be in the data dict (so the service's
    # "None means skip" loop leaves them alone).
    assert "name" not in captured
    assert "prompt" not in captured


# ---------------------------------------------------------------------------
# Description content — each operation carries a copyable example payload
# ---------------------------------------------------------------------------


def _op(name: str) -> AgentOperation:
    for op in SCHEDULED_TASKS_CAPABILITY.operations:
        if op.name == name:
            return op
    raise AssertionError(f"op {name!r} not registered")


def test_each_operation_description_contains_example_payload() -> None:
    # Every op (except list, which is no-arg) should show a JSON-shaped
    # example payload the model can copy as direct tool input.
    for op_name in (
        "create",
        "update",
        "retarget",
        "pause",
        "resume",
        "delete",
        "get",
        "list_runs",
    ):
        desc = _op(op_name).description
        assert "Example" in desc or "example" in desc, f"{op_name} description has no example"
        # The umbrella "operation" discriminator is gone — examples are the
        # bare input payload now.
        assert '"operation"' not in desc, (
            f"{op_name} description still mentions the old operation discriminator"
        )


def test_retarget_operation_registered() -> None:
    assert _op("retarget").name == "retarget"
    assert "im_channel" in _op("retarget").description


def test_create_description_documents_all_three_schedule_kinds() -> None:
    desc = _op("create").description
    for keyword in ("cron", "interval", "once"):
        assert keyword in desc, f"create description omits schedule kind {keyword!r}"


def test_create_description_documents_target_sentinel() -> None:
    desc = _op("create").description
    assert "current_conversation" in desc
    assert (
        "do not need" in desc.lower()
        or "no id" in desc.lower()
        or "no conversation id" in desc.lower()
    ), "create description must tell the model it doesn't need to pass a conversation ID"


def test_capability_description_is_short() -> None:
    # The capability description is the DeferredToolGroup catalog blurb —
    # one-line summary, no per-op detail.
    cap_desc = SCHEDULED_TASKS_CAPABILITY.description
    assert len(cap_desc) < 400
    assert "scheduled" in cap_desc.lower()


def test_list_description_states_no_arguments() -> None:
    desc = _op("list").description
    assert "no arguments" in desc.lower() or "no parameters" in desc.lower()
