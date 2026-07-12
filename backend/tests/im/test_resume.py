"""Tests for resume_paused_run — wraps run_manager.resume_run_with_answer.

The tests inject fakes for run_manager and conversation lookup so the
module-level helpers don't need a real Redis/DB.
"""

from __future__ import annotations

from typing import Any

import pytest


@pytest.mark.asyncio
async def test_resume_paused_run_sandbox_confirm_calls_run_manager(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cubeplex.im import resume as resume_mod

    seen: list[dict[str, Any]] = []

    class _FakeRunManager:
        async def resume_run_with_answer(self, **kwargs: Any) -> str:
            seen.append(kwargs)
            return "new_run_xyz"

    async def fake_resolve(run_id: str) -> tuple[str, str, str, str, str | None, bool] | None:
        if run_id == "run_1":
            return ("conv_1", "user_1", "org_1", "ws_1", None, False)
        return None

    monkeypatch.setattr(resume_mod, "_resolve_run_context", fake_resolve)

    ok = await resume_mod.resume_paused_run(
        run_id="run_1",
        input_kind="sandbox_confirm",
        choice="approve",
        operator_open_id="ou_x",
        question_id="qsc_1",
        run_manager=_FakeRunManager(),
    )
    assert ok is True
    assert len(seen) == 1
    call = seen[0]
    assert call["conversation_id"] == "conv_1"
    assert call["run_id"] == "run_1"
    assert call["question_id"] == "qsc_1"
    answer = call["answer"]
    assert getattr(answer, "decision", None) == "approve"


@pytest.mark.asyncio
async def test_resume_paused_run_sandbox_confirm_deny(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cubeplex.im import resume as resume_mod

    seen: list[dict[str, Any]] = []

    class _FakeRunManager:
        async def resume_run_with_answer(self, **kwargs: Any) -> str:
            seen.append(kwargs)
            return "new_run_xyz"

    async def fake_resolve(_: str) -> tuple[str, str, str, str, str | None, bool] | None:
        return ("conv_1", "user_1", "org_1", "ws_1", None, False)

    monkeypatch.setattr(resume_mod, "_resolve_run_context", fake_resolve)

    ok = await resume_mod.resume_paused_run(
        run_id="run_1",
        input_kind="sandbox_confirm",
        choice="deny",
        operator_open_id="ou_x",
        question_id="qsc_1",
        run_manager=_FakeRunManager(),
    )
    assert ok is True
    answer = seen[0]["answer"]
    assert getattr(answer, "decision", None) == "deny"


@pytest.mark.asyncio
async def test_resume_paused_run_ask_user_passes_choice_dict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cubeplex.im import resume as resume_mod

    seen: list[dict[str, Any]] = []

    class _FakeRunManager:
        async def resume_run_with_answer(self, **kwargs: Any) -> str:
            seen.append(kwargs)
            return "new_run_xyz"

    async def fake_resolve(_: str) -> tuple[str, str, str, str, str | None, bool] | None:
        return ("conv_1", "user_1", "org_1", "ws_1", None, False)

    monkeypatch.setattr(resume_mod, "_resolve_run_context", fake_resolve)

    ok = await resume_mod.resume_paused_run(
        run_id="run_1",
        input_kind="ask_user",
        choice="yes",
        operator_open_id="ou_x",
        question_id="q_1",
        answer_key="approve_deploy",
        run_manager=_FakeRunManager(),
    )
    assert ok is True
    # cubepi expects the answer dict keyed by the question schema's `key`.
    assert seen[0]["answer"] == {"approve_deploy": "yes"}


@pytest.mark.asyncio
async def test_resume_paused_run_ask_user_falls_back_to_choice_key_when_no_answer_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the card payload didn't carry answer_key (legacy / defensive),
    the answer falls back to ``{"choice": choice}`` so cubepi at least gets a
    syntactically valid dict — schema mismatch is then cubepi's to report."""
    from cubeplex.im import resume as resume_mod

    seen: list[dict[str, Any]] = []

    class _FakeRunManager:
        async def resume_run_with_answer(self, **kwargs: Any) -> str:
            seen.append(kwargs)
            return "new_run_xyz"

    async def fake_resolve(_: str) -> tuple[str, str, str, str, str | None, bool] | None:
        return ("conv_1", "user_1", "org_1", "ws_1", None, False)

    monkeypatch.setattr(resume_mod, "_resolve_run_context", fake_resolve)
    ok = await resume_mod.resume_paused_run(
        run_id="run_1",
        input_kind="ask_user",
        choice="yes",
        operator_open_id="ou_x",
        question_id="q_1",
        run_manager=_FakeRunManager(),
    )
    assert ok is True
    assert seen[0]["answer"] == {"choice": "yes"}


@pytest.mark.asyncio
async def test_resume_paused_run_returns_false_when_run_not_resolvable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cubeplex.im import resume as resume_mod

    class _FakeRunManager:
        async def resume_run_with_answer(self, **_: Any) -> str:
            raise AssertionError("must not be called when run unresolvable")

    async def fake_resolve(_: str) -> tuple[str, str, str, str, str | None, bool] | None:
        return None

    monkeypatch.setattr(resume_mod, "_resolve_run_context", fake_resolve)

    ok = await resume_mod.resume_paused_run(
        run_id="missing",
        input_kind="ask_user",
        choice="yes",
        operator_open_id="ou",
        question_id="q",
        run_manager=_FakeRunManager(),
    )
    assert ok is False


@pytest.mark.asyncio
async def test_resume_paused_run_returns_false_on_resume_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cubeplex.im import resume as resume_mod
    from cubeplex.streams.run_manager import ResumeNoPending

    class _FakeRunManager:
        async def resume_run_with_answer(self, **_: Any) -> str:
            raise ResumeNoPending("no pending")

    async def fake_resolve(_: str) -> tuple[str, str, str, str, str | None, bool] | None:
        return ("conv_1", "user_1", "org_1", "ws_1", None, False)

    monkeypatch.setattr(resume_mod, "_resolve_run_context", fake_resolve)

    ok = await resume_mod.resume_paused_run(
        run_id="run_1",
        input_kind="ask_user",
        choice="yes",
        operator_open_id="ou",
        question_id="q",
        run_manager=_FakeRunManager(),
    )
    assert ok is False


@pytest.mark.asyncio
async def test_resume_paused_run_returns_false_on_unknown_input_kind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cubeplex.im import resume as resume_mod

    class _FakeRunManager:
        async def resume_run_with_answer(self, **_: Any) -> str:
            raise AssertionError("must not be called for unknown input_kind")

    async def fake_resolve(_: str) -> tuple[str, str, str, str, str | None, bool] | None:
        return ("conv_1", "user_1", "org_1", "ws_1", None, False)

    monkeypatch.setattr(resume_mod, "_resolve_run_context", fake_resolve)

    ok = await resume_mod.resume_paused_run(
        run_id="run_1",
        input_kind="something_else",
        choice="yes",
        operator_open_id="ou",
        question_id="q",
        run_manager=_FakeRunManager(),
    )
    assert ok is False
