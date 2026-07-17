"""Unit tests for DingTalk card action routing."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from cubeplex.im.dingtalk.interactions import handle_card_action


class TestHandleCardAction:
    @pytest.mark.anyio()
    async def test_routes_to_resume(self) -> None:
        callback = {
            "outTrackId": "cubeplex-run_001-abc123",
            "content": '{"cardActionId":"im:ask_user:run_001:q1234567:answer:yes"}',
        }
        with (
            patch(
                "cubeplex.im.dingtalk.interactions.resolve_full_question_id",
                new_callable=AsyncMock,
                return_value="q123456789abcdef",
            ),
            patch(
                "cubeplex.im.dingtalk.interactions.resume_paused_run",
                new_callable=AsyncMock,
            ) as mock_resume,
        ):
            await handle_card_action(callback=callback, run_manager=AsyncMock())
            mock_resume.assert_called_once_with(
                run_id="run_001",
                input_kind="ask_user",
                choice="yes",
                operator_open_id="",
                question_id="q123456789abcdef",
                answer_key="answer",
                run_manager=mock_resume.call_args.kwargs["run_manager"],
            )

    @pytest.mark.anyio()
    async def test_ignores_non_im_actions(self) -> None:
        callback = {
            "outTrackId": "cubeplex-run_001-abc123",
            "content": '{"cardActionId":"other:action"}',
        }
        with patch(
            "cubeplex.im.dingtalk.interactions.resume_paused_run",
            new_callable=AsyncMock,
        ) as mock_resume:
            await handle_card_action(callback=callback, run_manager=AsyncMock())
            mock_resume.assert_not_called()
