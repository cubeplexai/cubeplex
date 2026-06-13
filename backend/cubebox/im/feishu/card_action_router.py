"""Pure routing logic for inbound CardKit `card.action.trigger` events.

`parse_action_payload(event)` extracts a typed `ActionPayload` from the
raw event body. `dispatch(payload, expected_responder_open_id)` validates
the responder identity and produces the `ResumeAction` cubepi should
consume, or `None` to silently drop (the caller surfaces a Feishu toast).

IO-free; the webhook ingress (Task 15) calls these helpers and then
invokes the cubepi resume API (Task 17).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

ActionKind = Literal["ask_user", "sandbox_confirm"]


class InvalidAction(ValueError):
    """The card.action payload is malformed or carries an unknown action."""


@dataclass(slots=True, frozen=True)
class ActionPayload:
    kind: ActionKind
    run_id: str
    choice: str
    operator_open_id: str
    question_id: str = ""
    answer_key: str = ""
    """cubepi form-schema key the resume call uses to build {answer_key: choice}."""


@dataclass(slots=True, frozen=True)
class ResumeAction:
    run_id: str
    input_kind: ActionKind
    choice: str
    operator_open_id: str
    question_id: str = ""
    answer_key: str = ""


def parse_action_payload(event: dict[str, Any]) -> ActionPayload:
    operator = event.get("operator") or {}
    operator_open_id = str(operator.get("open_id") or "")
    if not operator_open_id:
        raise InvalidAction("missing operator.open_id")
    action = event.get("action") or {}
    value = action.get("value") or {}
    kind_raw = str(value.get("action") or "")
    if kind_raw not in ("ask_user", "sandbox_confirm"):
        raise InvalidAction(f"unknown action kind: {kind_raw!r}")
    run_id = str(value.get("run_id") or "")
    choice = str(value.get("choice") or "")
    if not run_id or not choice:
        raise InvalidAction("missing run_id or choice")
    return ActionPayload(
        kind=kind_raw,  # type: ignore[arg-type]
        run_id=run_id,
        choice=choice,
        operator_open_id=operator_open_id,
        question_id=str(value.get("question_id") or ""),
        answer_key=str(value.get("answer_key") or ""),
    )


def dispatch(
    payload: ActionPayload,
    *,
    expected_responder_open_id: str | None,
) -> ResumeAction | None:
    """Validate responder and produce the ResumeAction cubepi consumes.

    Returns None when the responder does not match (caller surfaces a
    toast and otherwise no-ops). `expected_responder_open_id=None` denies
    all — None means "we have no record of awaiting input", which is the
    right default if Redis lost the binding or the run already finished.
    """
    if expected_responder_open_id is None:
        return None
    if payload.operator_open_id != expected_responder_open_id:
        return None
    return ResumeAction(
        run_id=payload.run_id,
        input_kind=payload.kind,
        choice=payload.choice,
        operator_open_id=payload.operator_open_id,
        question_id=payload.question_id,
        answer_key=payload.answer_key,
    )


__all__ = [
    "ActionKind",
    "ActionPayload",
    "InvalidAction",
    "ResumeAction",
    "dispatch",
    "parse_action_payload",
]
