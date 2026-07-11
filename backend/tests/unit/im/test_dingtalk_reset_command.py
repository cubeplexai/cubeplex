"""DingTalk gateway intercepts /new before agent ingest."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cubebox.im.dingtalk.gateway import DingtalkGateway
from cubebox.im.types import DM_SCOPE_KEY


def _dm_raw(text: str = "/new") -> dict[str, Any]:
    return {
        "msgtype": "text",
        "text": {"content": text},
        "msgId": "msg_reset_1",
        "conversationId": "cid_dm_1",
        "conversationType": "1",
        "senderStaffId": "staff_1",
        "chatbotUserId": "bot_1",
    }


@pytest.mark.asyncio
async def test_new_command_resets_and_skips_ingest() -> None:
    account = SimpleNamespace(id="acc_1", external_account_id="ext_1", workspace_id="ws_1")
    session_maker = MagicMock()
    ingest = AsyncMock()

    gw = DingtalkGateway(
        account=account,
        app_key="app_key",
        app_secret="app_secret",
        ingest=ingest,
        session_maker=session_maker,
        run_manager=None,
        redis_key_prefix="test",
    )
    gw._access_token = "tok"

    reply = AsyncMock()
    with (
        patch(
            "cubebox.im.types.lookup_binding_mode",
            new=AsyncMock(return_value="isolated"),
        ),
        patch(
            "cubebox.im.reset_command.apply_reset_command",
            new=AsyncMock(return_value="flat"),
        ) as apply_reset,
        patch.object(gw, "_reply_connector", return_value=SimpleNamespace(reply_markdown=reply)),
    ):
        await gw._handle_inbound(_dm_raw("/new"), account, session_maker, ingest)

    apply_reset.assert_awaited_once_with(
        session_maker=session_maker,
        account_id="acc_1",
        channel_id="cid_dm_1",
        scope_key=DM_SCOPE_KEY,
    )
    reply.assert_awaited_once()
    text = reply.await_args.kwargs.get("text") or ""
    assert "新对话已开始" in text
    ingest.assert_not_awaited()


@pytest.mark.asyncio
async def test_normal_message_still_ingests() -> None:
    account = SimpleNamespace(id="acc_1", external_account_id="ext_1", workspace_id="ws_1")
    session_maker = MagicMock()
    ingest = AsyncMock(return_value=SimpleNamespace(outcome="enqueued"))

    gw = DingtalkGateway(
        account=account,
        app_key="app_key",
        app_secret="app_secret",
        ingest=ingest,
        session_maker=session_maker,
        run_manager=None,
        redis_key_prefix="test",
    )
    gw._access_token = "tok"

    with patch(
        "cubebox.im.types.lookup_binding_mode",
        new=AsyncMock(return_value="isolated"),
    ):
        await gw._handle_inbound(_dm_raw("hello"), account, session_maker, ingest)

    ingest.assert_awaited_once()
