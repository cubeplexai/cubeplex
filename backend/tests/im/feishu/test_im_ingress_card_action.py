"""End-to-end-ish test for the card.action branch of the webhook ingress."""

from __future__ import annotations

from typing import Any

import pytest


class _FakeRunManager:
    async def resume_run_with_answer(self, **_: Any) -> str:
        return "new_run"


@pytest.mark.asyncio
async def test_card_action_dispatch_calls_resume(monkeypatch: pytest.MonkeyPatch) -> None:
    from cubebox.api.routes.v1 import im_ingress

    resume_calls: list[dict[str, Any]] = []

    async def fake_resume(**kwargs: Any) -> bool:
        resume_calls.append(kwargs)
        return True

    monkeypatch.setattr(im_ingress, "resume_paused_run", fake_resume)

    redis_state: dict[str, str] = {
        "cubebox-dev:run:run_1:awaiting_responder": "ou_user_1",
    }

    async def fake_get(key: str) -> str | None:
        return redis_state.get(key)

    async def fake_setnx(key: str, value: str, ex: int) -> bool:
        if key in redis_state:
            return False
        redis_state[key] = value
        return True

    monkeypatch.setattr(im_ingress, "_redis_get", fake_get)
    monkeypatch.setattr(im_ingress, "_redis_setnx", fake_setnx)

    event = {
        "header": {"event_type": "card.action.trigger", "token": "tok_abc"},
        "event": {
            "operator": {"open_id": "ou_user_1"},
            "action": {
                "value": {
                    "action": "ask_user",
                    "run_id": "run_1",
                    "choice": "yes",
                    "question_id": "q_1",
                    "answer_key": "approve_deploy",
                }
            },
        },
    }
    rm = _FakeRunManager()
    handled, toast = await im_ingress._handle_card_action(
        event, run_manager=rm, redis_key_prefix="cubebox-dev"
    )
    assert handled is True
    assert toast is None
    assert resume_calls == [
        {
            "run_id": "run_1",
            "input_kind": "ask_user",
            "choice": "yes",
            "operator_open_id": "ou_user_1",
            "question_id": "q_1",
            "answer_key": "approve_deploy",
            "run_manager": rm,
        }
    ]


@pytest.mark.asyncio
async def test_card_action_token_replay_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cubebox.api.routes.v1 import im_ingress

    resume_calls: list[Any] = []

    async def fake_resume(**kwargs: Any) -> bool:
        resume_calls.append(kwargs)
        return True

    monkeypatch.setattr(im_ingress, "resume_paused_run", fake_resume)

    redis_state: dict[str, str] = {"cubebox-dev:run:run_1:awaiting_responder": "ou_user_1"}

    async def fake_get(k: str) -> str | None:
        return redis_state.get(k)

    async def fake_setnx(k: str, v: str, ex: int) -> bool:
        if k in redis_state:
            return False
        redis_state[k] = v
        return True

    monkeypatch.setattr(im_ingress, "_redis_get", fake_get)
    monkeypatch.setattr(im_ingress, "_redis_setnx", fake_setnx)

    event = {
        "header": {"event_type": "card.action.trigger", "token": "tok_dup"},
        "event": {
            "operator": {"open_id": "ou_user_1"},
            "action": {"value": {"action": "ask_user", "run_id": "run_1", "choice": "yes"}},
        },
    }
    rm = _FakeRunManager()
    await im_ingress._handle_card_action(event, run_manager=rm, redis_key_prefix="cubebox-dev")
    await im_ingress._handle_card_action(event, run_manager=rm, redis_key_prefix="cubebox-dev")
    assert len(resume_calls) == 1  # second call no-op'd by token replay guard


@pytest.mark.asyncio
async def test_card_action_responder_mismatch_returns_toast(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cubebox.api.routes.v1 import im_ingress

    async def fake_resume(**_: Any) -> bool:
        raise AssertionError("must not call resume on mismatch")

    monkeypatch.setattr(im_ingress, "resume_paused_run", fake_resume)

    redis_state = {"cubebox-dev:run:run_1:awaiting_responder": "ou_user_1"}

    async def fake_get(k: str) -> str | None:
        return redis_state.get(k)

    async def fake_setnx(*_: Any, **__: Any) -> bool:
        return True

    monkeypatch.setattr(im_ingress, "_redis_get", fake_get)
    monkeypatch.setattr(im_ingress, "_redis_setnx", fake_setnx)

    event = {
        "header": {"event_type": "card.action.trigger", "token": "tok_3"},
        "event": {
            "operator": {"open_id": "ou_someone_else"},
            "action": {"value": {"action": "ask_user", "run_id": "run_1", "choice": "yes"}},
        },
    }
    handled, toast = await im_ingress._handle_card_action(
        event, run_manager=_FakeRunManager(), redis_key_prefix="cubebox-dev"
    )
    assert handled is True
    assert toast == "这不是发给你的"


@pytest.mark.asyncio
async def test_card_action_resume_exception_returns_friendly_toast(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cubebox.api.routes.v1 import im_ingress

    async def fake_resume(**_: Any) -> bool:
        raise RuntimeError("boom")

    monkeypatch.setattr(im_ingress, "resume_paused_run", fake_resume)

    redis_state = {"cubebox-dev:run:run_1:awaiting_responder": "ou_user_1"}

    async def fake_get(k: str) -> str | None:
        return redis_state.get(k)

    async def fake_setnx(*_: Any, **__: Any) -> bool:
        return True

    monkeypatch.setattr(im_ingress, "_redis_get", fake_get)
    monkeypatch.setattr(im_ingress, "_redis_setnx", fake_setnx)

    event = {
        "header": {"event_type": "card.action.trigger", "token": "tok_x"},
        "event": {
            "operator": {"open_id": "ou_user_1"},
            "action": {"value": {"action": "ask_user", "run_id": "run_1", "choice": "yes"}},
        },
    }
    handled, toast = await im_ingress._handle_card_action(
        event, run_manager=_FakeRunManager(), redis_key_prefix="cubebox-dev"
    )
    assert handled is True
    assert toast == "暂时无法响应"


@pytest.mark.asyncio
async def test_card_action_resume_returns_false_surfaces_ended_toast(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When resume_paused_run returns False (run no longer pending) the
    user sees the "会话已结束" toast rather than a generic error."""
    from cubebox.api.routes.v1 import im_ingress

    async def fake_resume(**_: Any) -> bool:
        return False

    monkeypatch.setattr(im_ingress, "resume_paused_run", fake_resume)

    redis_state = {"cubebox-dev:run:run_1:awaiting_responder": "ou_user_1"}

    async def fake_get(k: str) -> str | None:
        return redis_state.get(k)

    async def fake_setnx(*_: Any, **__: Any) -> bool:
        return True

    monkeypatch.setattr(im_ingress, "_redis_get", fake_get)
    monkeypatch.setattr(im_ingress, "_redis_setnx", fake_setnx)

    event = {
        "header": {"event_type": "card.action.trigger", "token": "tok_e"},
        "event": {
            "operator": {"open_id": "ou_user_1"},
            "action": {"value": {"action": "ask_user", "run_id": "run_1", "choice": "yes"}},
        },
    }
    handled, toast = await im_ingress._handle_card_action(
        event, run_manager=_FakeRunManager(), redis_key_prefix="cubebox-dev"
    )
    assert handled is True
    assert toast == "会话已结束"


@pytest.mark.asyncio
async def test_card_action_invalid_payload_returns_toast(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cubebox.api.routes.v1 import im_ingress

    async def fake_setnx(*_: Any, **__: Any) -> bool:
        return True

    monkeypatch.setattr(im_ingress, "_redis_setnx", fake_setnx)

    event = {
        "header": {"event_type": "card.action.trigger", "token": "tok_q"},
        "event": {
            "operator": {"open_id": "ou_x"},
            "action": {"value": {"action": "weird", "run_id": "r", "choice": "c"}},
        },
    }
    handled, toast = await im_ingress._handle_card_action(
        event, run_manager=_FakeRunManager(), redis_key_prefix="cubebox-dev"
    )
    assert handled is True
    assert toast == "未知操作"


@pytest.mark.asyncio
async def test_card_action_missing_token_returns_toast() -> None:
    from cubebox.api.routes.v1 import im_ingress

    event = {
        "header": {"event_type": "card.action.trigger"},
        "event": {
            "operator": {"open_id": "ou_user_1"},
            "action": {"value": {"action": "ask_user", "run_id": "r", "choice": "yes"}},
        },
    }
    handled, toast = await im_ingress._handle_card_action(
        event, run_manager=_FakeRunManager(), redis_key_prefix="cubebox-dev"
    )
    assert handled is True
    assert toast == "缺少 token"
