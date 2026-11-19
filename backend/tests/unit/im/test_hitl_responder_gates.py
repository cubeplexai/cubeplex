"""Unit tests for fail-closed awaiting_responder gates across IM connectors."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest


class _FakeRedis:
    def __init__(self, initial: dict[str, str] | None = None) -> None:
        self.store: dict[str, str] = dict(initial or {})

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, *, ex: int | None = None, nx: bool = False) -> bool:
        del ex
        if nx and key in self.store:
            return False
        self.store[key] = value
        return True


@pytest.mark.asyncio
async def test_slack_block_action_enforces_awaiting_responder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cubeplex import cache
    from cubeplex.im.slack.interactions import handle_block_action

    fake_redis = _FakeRedis({"cb:run:run_1:awaiting_responder": "U_ACTOR"})
    monkeypatch.setattr(cache, "get_redis", lambda: fake_redis)

    action = {"action_id": "im:ask_user:run_1:q1234567:ans:yes"}
    with (
        patch(
            "cubeplex.im.resume.resolve_full_question_id",
            new_callable=AsyncMock,
            return_value="q123456789",
        ),
        patch(
            "cubeplex.im.resume.resume_paused_run",
            new_callable=AsyncMock,
        ) as mock_resume,
    ):
        await handle_block_action(
            action=action,
            body={"user": {"id": "U_OTHER"}},
            run_manager=AsyncMock(),
            redis_key_prefix="cb",
        )
        mock_resume.assert_not_called()

        await handle_block_action(
            action=action,
            body={"user": {"id": "U_ACTOR"}},
            run_manager=AsyncMock(),
            redis_key_prefix="cb",
        )
        mock_resume.assert_awaited_once()
        assert mock_resume.await_args.kwargs["operator_open_id"] == "U_ACTOR"


@pytest.mark.asyncio
async def test_discord_component_interaction_enforces_awaiting_responder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cubeplex import cache
    from cubeplex.im.discord.interactions import handle_component_interaction

    fake_redis = _FakeRedis({"cb:run:run_1:awaiting_responder": "12345"})
    monkeypatch.setattr(cache, "get_redis", lambda: fake_redis)

    def _make_interaction(user_id: int) -> Any:
        return SimpleNamespace(
            data={"custom_id": "im:sandbox_confirm:run_1:q1234567:confirm:approve"},
            user=SimpleNamespace(id=user_id),
            response=SimpleNamespace(send_message=AsyncMock()),
        )

    with (
        patch(
            "cubeplex.im.resume.resolve_full_question_id",
            new_callable=AsyncMock,
            return_value="q123456789",
        ),
        patch(
            "cubeplex.im.resume.resume_paused_run",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_resume,
    ):
        wrong = _make_interaction(99999)
        await handle_component_interaction(
            wrong,
            run_manager=AsyncMock(),
            redis_key_prefix="cb",
        )
        mock_resume.assert_not_called()
        wrong.response.send_message.assert_awaited_once_with("这不是发给你的。", ephemeral=True)

        right = _make_interaction(12345)
        await handle_component_interaction(
            right,
            run_manager=AsyncMock(),
            redis_key_prefix="cb",
        )
        mock_resume.assert_awaited_once()
        assert mock_resume.await_args.kwargs["operator_open_id"] == "12345"


@pytest.mark.asyncio
async def test_teams_card_action_non_actor_does_not_lock_out_real_actor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: a non-actor clicking a Teams card in a shared chat must be
    rejected BEFORE replay_key is set, and missing bindings must fail closed."""
    from cubeplex import cache
    from cubeplex.im.teams.interactions import handle_card_action

    fake_redis = _FakeRedis({"cb:run:run_1:awaiting_responder": "aad_actor"})
    monkeypatch.setattr(cache, "get_redis", lambda: fake_redis)

    data = {"action": "im:ask_user:run_1:q1234567:choice:yes"}
    with (
        patch(
            "cubeplex.im.resume.resolve_full_question_id",
            new_callable=AsyncMock,
            return_value="q123456789",
        ),
        patch(
            "cubeplex.im.resume.resume_paused_run",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_resume,
    ):
        # 1. Non-actor clicks first → rejected without burning replay_key
        ok_other = await handle_card_action(
            data=data,
            operator_aad_id="aad_bystander",
            run_manager=AsyncMock(),
            redis_key_prefix="cb",
        )
        assert ok_other is False
        assert "cb:teams:invoke:run_1:q1234567:choice" not in fake_redis.store
        mock_resume.assert_not_called()

        # 2. Empty operator id → fail closed
        ok_empty = await handle_card_action(
            data=data,
            operator_aad_id="",
            run_manager=AsyncMock(),
            redis_key_prefix="cb",
        )
        assert ok_empty is False
        mock_resume.assert_not_called()

        # 3. Real actor clicks → succeeds and sets replay_key
        ok_actor = await handle_card_action(
            data=data,
            operator_aad_id="aad_actor",
            run_manager=AsyncMock(),
            redis_key_prefix="cb",
        )
        assert ok_actor is True
        mock_resume.assert_awaited_once()

        # 4. Missing binding on another run → fail closed
        ok_missing = await handle_card_action(
            data={"action": "im:ask_user:run_expired:q1234567:choice:yes"},
            operator_aad_id="aad_actor",
            run_manager=AsyncMock(),
            redis_key_prefix="cb",
        )
        assert ok_missing is False
