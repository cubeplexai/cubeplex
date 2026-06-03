"""Unit tests for the scheduled_tasks agent capability input models and handlers."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from cubebox.agents.actions.capabilities.scheduled_tasks import (
    CreateInput,
    _handle_create,
)
from cubebox.agents.actions.context import ScopeContext
from cubebox.agents.actions.types import ActionInvalidInput
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


@pytest.mark.asyncio
async def test_handle_create_cron_flattens_to_service_dict() -> None:
    captured: dict = {}

    async def fake_create(ctx, session, data):  # type: ignore[no-untyped-def]
        captured.update(data)
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

    from cubebox.agents.actions.capabilities import scheduled_tasks as cap

    inp = CreateInput(
        name="morning-reply",
        prompt="reply to bigv",
        schedule={"kind": "cron", "cron_expr": "0 9 * * *", "timezone": "Asia/Shanghai"},
    )
    with patch.object(cap._svc, "create", new=fake_create):
        await _handle_create(_ctx(), AsyncMock(), inp)

    assert captured["schedule_kind"] == "cron"
    assert captured["cron_expr"] == "0 9 * * *"
    assert captured["interval_seconds"] is None
    assert captured["run_at"] is None
    assert captured["timezone"] == "Asia/Shanghai"
    assert captured["target_mode"] == "new_each_run"
    assert captured["target_conversation_id"] is None


@pytest.mark.asyncio
async def test_handle_create_target_current_conversation_uses_ctx_id() -> None:
    captured: dict = {}

    async def fake_create(ctx, session, data):  # type: ignore[no-untyped-def]
        captured.update(data)
        task = AsyncMock()
        task.id = "stask-1"
        task.name = data["name"]
        task.status = "active"
        task.schedule_kind = data["schedule_kind"]
        task.cron_expr = data.get("cron_expr")
        task.interval_seconds = data.get("interval_seconds")
        task.timezone = "UTC"
        task.prompt = data["prompt"]
        task.target_mode = data["target_mode"]
        task.next_fire_at = None
        task.last_fired_at = None
        return task

    from cubebox.agents.actions.capabilities import scheduled_tasks as cap

    inp = CreateInput(
        name="x",
        prompt="y",
        schedule={"kind": "cron", "cron_expr": "0 9 * * *"},
        target="current_conversation",
    )
    with patch.object(cap._svc, "create", new=fake_create):
        await _handle_create(_ctx("conv-abc"), AsyncMock(), inp)

    assert captured["target_mode"] == "fixed"
    assert captured["target_conversation_id"] == "conv-abc"


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
