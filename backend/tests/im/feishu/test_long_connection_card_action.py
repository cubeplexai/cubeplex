"""Tests for the long-connection card.action.trigger SDK handler glue.

These tests directly exercise the `_lc_handle_card_action` async helper
(the function the SDK handler invokes) — they don't spin up a real
WebSocket, just verify the SDK→handler envelope conversion and the
toast → CallBackToast mapping.
"""

from __future__ import annotations

from typing import Any

import pytest


@pytest.mark.asyncio
async def test_lc_handler_builds_envelope_and_calls_ingress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cubebox.im.feishu import long_connection as lc

    seen: list[dict[str, Any]] = []

    async def fake_handler(
        envelope: dict[str, Any], *, run_manager: Any = None, redis_key_prefix: str = ""
    ) -> tuple[bool, str | None]:
        seen.append(envelope)
        return True, None

    monkeypatch.setattr(lc, "_handle_card_action", fake_handler)

    # Build a fake SDK event payload (duck-typed, only the fields the handler reads).
    class _Op:
        open_id = "ou_user_1"

    class _Action:
        value = {
            "action": "ask_user",
            "run_id": "run_1",
            "choice": "yes",
            "question_id": "q_1",
        }

    class _Data:
        operator = _Op()
        token = "tok_xyz"
        action = _Action()

    class _Event:
        event = _Data()

    response = await lc._lc_handle_card_action(
        _Event(), run_manager=None, redis_key_prefix="cubebox-dev"
    )
    # No toast → response.toast is None or a CallBackToast with empty content
    assert response is not None
    assert seen == [
        {
            "header": {"event_type": "card.action.trigger", "token": "tok_xyz"},
            "event": {
                "operator": {"open_id": "ou_user_1"},
                "action": {
                    "value": {
                        "action": "ask_user",
                        "run_id": "run_1",
                        "choice": "yes",
                        "question_id": "q_1",
                    }
                },
            },
        }
    ]
    # The shape that the SDK wants back; no toast in this case.
    assert response.toast is None


@pytest.mark.asyncio
async def test_lc_handler_carries_toast_when_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cubebox.im.feishu import long_connection as lc

    async def fake_handler(
        envelope: dict[str, Any], *, run_manager: Any = None, redis_key_prefix: str = ""
    ) -> tuple[bool, str | None]:
        return True, "这不是发给你的"

    monkeypatch.setattr(lc, "_handle_card_action", fake_handler)

    class _Op:
        open_id = "ou_user_1"

    class _Action:
        value = {"action": "ask_user", "run_id": "run_1", "choice": "yes"}

    class _Data:
        operator = _Op()
        token = "tok_t"
        action = _Action()

    class _Event:
        event = _Data()

    response = await lc._lc_handle_card_action(
        _Event(), run_manager=None, redis_key_prefix="cubebox-dev"
    )
    assert response is not None
    assert response.toast is not None
    assert response.toast.content == "这不是发给你的"
    assert response.toast.type == "info"


@pytest.mark.asyncio
async def test_lc_handler_tolerates_missing_event_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Defensive: a malformed SDK event shouldn't crash the dispatcher."""
    from cubebox.im.feishu import long_connection as lc

    called: list[Any] = []

    async def fake_handler(
        envelope: dict[str, Any], *, run_manager: Any = None, redis_key_prefix: str = ""
    ) -> tuple[bool, str | None]:
        called.append(envelope)
        return True, "未知操作"

    monkeypatch.setattr(lc, "_handle_card_action", fake_handler)

    class _Event:
        event = None  # SDK passes None when the payload is missing

    response = await lc._lc_handle_card_action(
        _Event(), run_manager=None, redis_key_prefix="cubebox-dev"
    )
    # The handler is still invoked with an empty envelope; downstream
    # parse_action_payload raises InvalidAction → toast "未知操作".
    assert response is not None
    assert called[0]["event"] == {}
