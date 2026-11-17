"""Test Feishu /link command interception."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from cubeplex.im.feishu.link_command import handle_link_command, parse_link_command
from cubeplex.im.link import verify_link_token


class TestParseLinkCommand:
    def test_link_with_email(self) -> None:
        result = parse_link_command("/link chris@example.com")
        assert result == "chris@example.com"

    def test_link_chinese(self) -> None:
        result = parse_link_command("绑定 test@corp.cn")
        assert result == "test@corp.cn"

    def test_link_extra_whitespace(self) -> None:
        result = parse_link_command("  /link   user@host.com  ")
        assert result == "user@host.com"

    def test_not_a_link_command(self) -> None:
        assert parse_link_command("hello world") is None
        assert parse_link_command("/new") is None
        assert parse_link_command("/link") is None  # no email
        assert parse_link_command("绑定") is None

    def test_invalid_email_rejected(self) -> None:
        assert parse_link_command("/link notanemail") is None

    def test_feishu_mailto_autolink_unwrapped(self) -> None:
        # Feishu client auto-renders bare emails as markdown autolinks.
        result = parse_link_command("/link [gxf.beta@gmail.com](mailto:gxf.beta@gmail.com)")
        assert result == "gxf.beta@gmail.com"

    def test_feishu_edited_mailto_autolink_prefers_visible_email(self) -> None:
        # Editing an auto-linked email in Feishu updates the visible text while
        # keeping the original mailto href.
        result = parse_link_command("/link [daibolin@sensedeal.ai](mailto:daibolin@sensedal.ai)")
        assert result == "daibolin@sensedeal.ai"

    def test_feishu_non_email_label_falls_back_to_mailto_href(self) -> None:
        result = parse_link_command("/link [点击绑定](mailto:chris@example.com)")
        assert result == "chris@example.com"

    def test_angle_bracketed_email_unwrapped(self) -> None:
        result = parse_link_command("/link <chris@example.com>")
        assert result == "chris@example.com"


class TestHandleLinkCommand:
    @pytest.mark.anyio
    async def test_reply_echoes_target_email_and_signs_matching_token(self) -> None:
        connector = SimpleNamespace(send_to_chat=AsyncMock())
        event = SimpleNamespace(
            sender_ref="on_sender_1",
            sender_open_id="ou_sender_1",
            channel_id="oc_chat_1",
            reply_to_id="om_msg_1",
        )
        account = SimpleNamespace(id="imac-1", workspace_id="ws-1")

        def _cfg_get(key: str, default: str = "") -> str:
            if key == "auth.jwt_secret":
                return "test-secret"
            if key == "frontend_base_url":
                return "https://cubeplex.example.com"
            return default

        with patch("cubeplex.config.config") as mock_cfg:
            mock_cfg.get.side_effect = _cfg_get
            await handle_link_command(
                email="Daibolin@Sensedeal.ai",
                event=event,
                account=account,  # type: ignore[arg-type]
                connector=connector,
            )

        connector.send_to_chat.assert_awaited_once()
        chat_id, reply_to_id, text = connector.send_to_chat.await_args.args
        assert chat_id == "oc_chat_1"
        assert reply_to_id == "om_msg_1"
        assert "daibolin@sensedeal.ai" in text
        assert "https://cubeplex.example.com/im-link?token=" in text
        token = text.split("token=", 1)[1].strip()
        claims = verify_link_token(token, secret="test-secret")
        assert claims.email == "daibolin@sensedeal.ai"
