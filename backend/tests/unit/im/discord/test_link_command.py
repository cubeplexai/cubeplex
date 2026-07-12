# backend/tests/unit/im/discord/test_link_command.py
"""Test Discord /link slash command handler."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cubeplex.im.discord.commands import _initiate_link


@pytest.mark.anyio
async def test_link_generates_token_and_replies_ephemeral() -> None:
    interaction = MagicMock()
    interaction.user.id = 123456
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()

    bot = MagicMock()
    bot._cubeplex_account_id = "imca_abc"
    bot._cubeplex_workspace_id = "ws_xyz"

    with (
        patch("cubeplex.im.link.get_jwt_secret", return_value="test-secret"),
        patch(
            "cubeplex.im.link.get_frontend_base_url",
            return_value="http://localhost:3000",
        ),
    ):
        await _initiate_link(interaction, bot, email="chris@example.com")

    interaction.response.defer.assert_awaited_once_with(ephemeral=True)
    interaction.followup.send.assert_awaited_once()
    call_kwargs = interaction.followup.send.call_args
    msg: str = call_kwargs.args[0] if call_kwargs.args else call_kwargs.kwargs.get("content", "")
    assert "http://localhost:3000/im-link?token=" in msg
    assert call_kwargs.kwargs.get("ephemeral") is True


@pytest.mark.anyio
async def test_link_missing_account_id_replies_error() -> None:
    interaction = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()

    bot = MagicMock()
    bot._cubeplex_account_id = None
    bot._cubeplex_workspace_id = None

    await _initiate_link(interaction, bot, email="a@b.com")

    interaction.response.defer.assert_awaited_once_with(ephemeral=True)
    interaction.followup.send.assert_awaited_once()
    call_kwargs = interaction.followup.send.call_args
    assert call_kwargs.kwargs.get("ephemeral") is True
    msg = call_kwargs.args[0] if call_kwargs.args else ""
    assert "内部错误" in msg
