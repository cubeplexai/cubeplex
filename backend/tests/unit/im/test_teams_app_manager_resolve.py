"""Web Chat recipient.id is not the App ID — resolve via JWT aud."""

from __future__ import annotations

import base64
import json

import cubeplex.im.teams.app_manager as app_manager
from cubeplex.im.teams.app_manager import (
    TeamsAppEntry,
    bot_id_candidates,
    resolve_entry_for_activity,
)


def _b64url(data: dict) -> str:
    raw = json.dumps(data, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _fake_bearer(*, aud: str, appid: str | None = None) -> str:
    header = _b64url({"alg": "RS256", "typ": "JWT"})
    payload: dict = {"aud": aud, "iss": "https://api.botframework.com"}
    if appid is not None:
        payload["appid"] = appid
    # signature not validated at candidate-extract time
    return f"Bearer {header}.{_b64url(payload)}.sig"


def setup_function() -> None:
    app_manager._app_cache.clear()


def teardown_function() -> None:
    app_manager._app_cache.clear()


def test_bot_id_candidates_prefer_recipient_then_jwt_aud() -> None:
    app_id = "11111111-2222-3333-4444-555555555555"
    # Web Chat style channel account id (not a real secret / app id).
    webchat_recipient = "test-bot@webchat-channel-account-id"
    activity = {"recipient": {"id": webchat_recipient}}
    auth = _fake_bearer(aud=app_id)
    cands = bot_id_candidates(activity, auth)
    assert cands[0] == webchat_recipient
    assert app_id in cands


def test_bot_id_candidates_strip_28_prefix() -> None:
    app_id = "11111111-2222-3333-4444-555555555555"
    activity = {"recipient": {"id": f"28:{app_id}"}}
    cands = bot_id_candidates(activity, "")
    assert cands == [f"28:{app_id}", app_id]


def test_resolve_entry_falls_back_to_jwt_aud_for_webchat_recipient() -> None:
    app_id = "11111111-2222-3333-4444-555555555555"
    entry = TeamsAppEntry(
        app=object(),
        account_id="imac-test",
        bot_id=app_id,
        secrets={"app_id": app_id},
    )
    app_manager._app_cache[app_id] = entry

    activity = {
        "type": "message",
        "recipient": {
            "id": "test-bot@webchat-channel-account-id",
            "name": "test-bot",
        },
    }
    auth = _fake_bearer(aud=app_id)
    resolved = resolve_entry_for_activity(activity, auth)
    assert resolved is entry
    assert resolved.bot_id == app_id


def test_resolve_entry_none_when_no_cache_match() -> None:
    activity = {"recipient": {"id": "unknown@channel"}}
    auth = _fake_bearer(aud="00000000-0000-0000-0000-000000000000")
    assert resolve_entry_for_activity(activity, auth) is None


def test_conversation_route_cache() -> None:
    app_manager._conversation_routes.clear()
    app_manager.remember_conversation_route(
        "conv-webchat-1",
        service_url="https://webchat.botframework.com/",
        channel_id="webchat",
        bot_account_id="cubeplex@abc",
    )
    assert app_manager.get_conversation_route("conv-webchat-1") == (
        "https://webchat.botframework.com",
        "webchat",
        "cubeplex@abc",
    )
    app_manager._conversation_routes.clear()
