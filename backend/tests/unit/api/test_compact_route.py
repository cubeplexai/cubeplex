"""Unit tests for POST .../conversations/{id}/compact handler."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from cubeplex.api.routes.v1.conversations import compact_conversation
from cubeplex.services.conversation_compact import ForceCompactResult


def _ctx() -> SimpleNamespace:
    return SimpleNamespace(
        org_id="org-1",
        workspace_id="ws-1",
        user=SimpleNamespace(id="user-1"),
    )


def _rds() -> SimpleNamespace:
    client = AsyncMock()
    client.set = AsyncMock(return_value=True)
    client.eval = AsyncMock(return_value=1)
    client.delete = AsyncMock()
    return SimpleNamespace(client=client, key_prefix="pfx")


def _session() -> MagicMock:
    return MagicMock()


@pytest.mark.asyncio
async def test_compact_route_404_when_missing() -> None:
    repo = AsyncMock()
    repo.get_by_id = AsyncMock(return_value=None)
    with patch(
        "cubeplex.api.routes.v1.conversations.ConversationRepository",
        return_value=repo,
    ):
        with pytest.raises(HTTPException) as ei:
            await compact_conversation(
                conversation_id="missing",
                session=_session(),
                ctx=_ctx(),  # type: ignore[arg-type]
                rds=_rds(),  # type: ignore[arg-type]
            )
    assert ei.value.status_code == 404


@pytest.mark.asyncio
async def test_compact_route_409_lock_not_acquired() -> None:
    repo = AsyncMock()
    repo.get_by_id = AsyncMock(return_value=SimpleNamespace(id="c1"))
    rds = _rds()
    rds.client.set = AsyncMock(return_value=False)
    with patch(
        "cubeplex.api.routes.v1.conversations.ConversationRepository",
        return_value=repo,
    ):
        with pytest.raises(HTTPException) as ei:
            await compact_conversation(
                conversation_id="c1",
                session=_session(),
                ctx=_ctx(),  # type: ignore[arg-type]
                rds=rds,  # type: ignore[arg-type]
            )
    assert ei.value.status_code == 409
    assert "already in progress" in str(ei.value.detail).lower()


@pytest.mark.asyncio
async def test_compact_route_409_when_active_run() -> None:
    repo = AsyncMock()
    repo.get_by_id = AsyncMock(return_value=SimpleNamespace(id="c1"))
    with (
        patch(
            "cubeplex.api.routes.v1.conversations.ConversationRepository",
            return_value=repo,
        ),
        patch(
            "cubeplex.api.routes.v1.conversations.get_active_run",
            new=AsyncMock(return_value=object()),
        ),
    ):
        with pytest.raises(HTTPException) as ei:
            await compact_conversation(
                conversation_id="c1",
                session=_session(),
                ctx=_ctx(),  # type: ignore[arg-type]
                rds=_rds(),  # type: ignore[arg-type]
            )
    assert ei.value.status_code == 409


@pytest.mark.asyncio
async def test_compact_route_success_with_marker() -> None:
    repo = AsyncMock()
    repo.get_by_id = AsyncMock(return_value=SimpleNamespace(id="c1"))
    marker = {"role": "user", "metadata": {"synthetic_source": "compaction"}}

    async def _force(conversation_id: str, **kwargs: Any) -> ForceCompactResult:
        return ForceCompactResult(
            ok=True,
            compacted=True,
            boundary=4,
            marker=marker,
        )

    with (
        patch(
            "cubeplex.api.routes.v1.conversations.ConversationRepository",
            return_value=repo,
        ),
        patch(
            "cubeplex.api.routes.v1.conversations.get_active_run",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "cubeplex.services.conversation_compact.force_compact_conversation",
            new=_force,
        ),
    ):
        body = await compact_conversation(
            conversation_id="c1",
            session=_session(),
            ctx=_ctx(),  # type: ignore[arg-type]
            rds=_rds(),  # type: ignore[arg-type]
        )
    assert body["compacted"] is True
    assert body["boundary"] == 4
    assert body["marker"] == marker


@pytest.mark.asyncio
async def test_compact_route_history_changed_409() -> None:
    repo = AsyncMock()
    repo.get_by_id = AsyncMock(return_value=SimpleNamespace(id="c1"))

    async def _force(conversation_id: str, **kwargs: Any) -> ForceCompactResult:
        return ForceCompactResult(ok=True, compacted=False, reason="history_changed")

    with (
        patch(
            "cubeplex.api.routes.v1.conversations.ConversationRepository",
            return_value=repo,
        ),
        patch(
            "cubeplex.api.routes.v1.conversations.get_active_run",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "cubeplex.services.conversation_compact.force_compact_conversation",
            new=_force,
        ),
    ):
        with pytest.raises(HTTPException) as ei:
            await compact_conversation(
                conversation_id="c1",
                session=_session(),
                ctx=_ctx(),  # type: ignore[arg-type]
                rds=_rds(),  # type: ignore[arg-type]
            )
    assert ei.value.status_code == 409


@pytest.mark.asyncio
async def test_compact_route_busy_result_409() -> None:
    repo = AsyncMock()
    repo.get_by_id = AsyncMock(return_value=SimpleNamespace(id="c1"))

    async def _force(conversation_id: str, **kwargs: Any) -> ForceCompactResult:
        return ForceCompactResult(ok=False, compacted=False, reason="busy")

    with (
        patch(
            "cubeplex.api.routes.v1.conversations.ConversationRepository",
            return_value=repo,
        ),
        patch(
            "cubeplex.api.routes.v1.conversations.get_active_run",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "cubeplex.services.conversation_compact.force_compact_conversation",
            new=_force,
        ),
    ):
        with pytest.raises(HTTPException) as ei:
            await compact_conversation(
                conversation_id="c1",
                session=_session(),
                ctx=_ctx(),  # type: ignore[arg-type]
                rds=_rds(),  # type: ignore[arg-type]
            )
    assert ei.value.status_code == 409
