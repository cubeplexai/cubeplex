"""Outbound send must use activity serviceUrl, not App.send defaults."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

import cubeplex.im.teams.app_manager as app_manager
from cubeplex.im.teams.connector import TeamsConnector


@pytest.fixture(autouse=True)
def _clear_routes() -> Any:
    app_manager._conversation_routes.clear()
    yield
    app_manager._conversation_routes.clear()


@pytest.mark.asyncio
async def test_send_to_chat_uses_activity_service_url_not_default_teams() -> None:
    sent: dict[str, Any] = {}

    class _Sender:
        async def send(self, activity: Any, ref: Any) -> SimpleNamespace:
            sent["activity"] = activity
            sent["ref"] = ref
            return SimpleNamespace(id="act-1")

    app = SimpleNamespace(
        id="11111111-2222-3333-4444-555555555555",
        _initialized=True,
        activity_sender=_Sender(),
        api=SimpleNamespace(service_url="https://smba.trafficmanager.net/teams"),
    )
    connector = TeamsConnector(
        bot_id=app.id,
        app=app,
        channel_id="conv-webchat",
        service_url="https://webchat.botframework.com/",
        bf_channel_id="webchat",
        bot_account_id="test-bot@webchat-channel-account-id",
    )
    out = await connector.send_to_chat("conv-webchat", None, "hello")
    assert out == "act-1"
    assert sent["ref"].service_url == "https://webchat.botframework.com"
    assert sent["ref"].channel_id == "webchat"
    assert sent["ref"].conversation.id == "conv-webchat"
    # from.id must be channel account id, not App ID — raw App ID → 403
    assert sent["ref"].bot.id == "test-bot@webchat-channel-account-id"


@pytest.mark.asyncio
async def test_send_to_chat_falls_back_to_remembered_route() -> None:
    sent: dict[str, Any] = {}

    class _Sender:
        async def send(self, activity: Any, ref: Any) -> SimpleNamespace:
            sent["ref"] = ref
            return SimpleNamespace(id="act-2")

    app = SimpleNamespace(
        id="bot-guid",
        _initialized=True,
        activity_sender=_Sender(),
        api=SimpleNamespace(service_url="https://smba.trafficmanager.net/teams"),
        send=AsyncMock(),
    )
    app_manager.remember_conversation_route(
        "conv-2",
        service_url="https://smba.trafficmanager.net/amer/",
        channel_id="msteams",
        bot_account_id="28:bot-guid",
    )
    connector = TeamsConnector(bot_id="bot-guid", app=app)
    await connector.send_to_chat("conv-2", None, "hi")
    assert sent["ref"].service_url == "https://smba.trafficmanager.net/amer"
    assert sent["ref"].channel_id == "msteams"
    assert sent["ref"].bot.id == "28:bot-guid"
    app.send.assert_not_called()


def test_webchat_does_not_support_message_edit() -> None:
    c = TeamsConnector(bf_channel_id="webchat")
    assert c.supports_message_edit is False


def test_msteams_supports_message_edit() -> None:
    c = TeamsConnector(bf_channel_id="msteams")
    assert c.supports_message_edit is True


@pytest.mark.asyncio
async def test_edit_message_skips_api_on_webchat() -> None:
    app = SimpleNamespace(
        id="bot",
        _initialized=True,
        activity_sender=SimpleNamespace(send=AsyncMock()),
    )
    c = TeamsConnector(
        bot_id="bot",
        app=app,
        channel_id="conv",
        bf_channel_id="webchat",
    )
    assert await c.edit_message("act-1", "hello") is False
    app.activity_sender.send.assert_not_called()
