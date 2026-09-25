"""allow_input options survive projection and Feishu form submit."""

from __future__ import annotations

from typing import Any

import pytest

from cubeplex.im.feishu.card_action_router import InvalidAction, parse_action_payload
from cubeplex.im.feishu.card_renderer import render
from cubeplex.im.outbound import fold_event
from cubeplex.im.types import RenderState


def _fold(questions: list[dict[str, Any]]) -> RenderState:
    state = RenderState(bot_name="CubePlex", run_id="run_custom")
    state.card_id = "card_1"
    fold_event(
        {
            "type": "ask_user_request",
            "data": {"question_id": "q_custom", "questions": questions},
        },
        state,
        now=0.0,
    )
    return state


def _form(card: dict[str, Any]) -> dict[str, Any]:
    return next(el for el in card["body"]["elements"] if el.get("element_id") == "pending_input")


def _submit_value(card: dict[str, Any]) -> dict[str, Any]:
    form = _form(card)
    assert form["tag"] == "form"
    button = next(el for el in form["elements"] if el.get("form_action_type") == "submit")
    value = button["behaviors"][0]["value"]
    assert isinstance(value, dict)
    return value


def _input_name(value: dict[str, Any], question_key: str, option_value: str) -> str:
    name = value["custom_inputs"][question_key][option_value]
    assert isinstance(name, str)
    return name


def _parse(value: dict[str, Any], form_value: dict[str, Any]) -> dict[str, Any]:
    parsed = parse_action_payload(
        {
            "operator": {"open_id": "ou_user"},
            "action": {"value": value, "form_value": form_value},
        }
    )
    assert parsed.answers is not None
    return parsed.answers


def test_single_custom_choice_submits_typed_text() -> None:
    state = _fold(
        [
            {
                "key": "repo",
                "prompt": "Which repository?",
                "options": [
                    {"label": "Main", "value": "main"},
                    {"label": "Other repository", "value": "repo_url", "allow_input": True},
                ],
                "required": True,
            }
        ]
    )
    pending = state.card_state.pending_input
    assert pending is not None and pending.choices == []
    card = render(state.card_state)
    value = _submit_value(card)
    input_name = _input_name(value, "repo", "repo_url")

    answers = _parse(
        value,
        {"repo": "repo_url", input_name: "  https://github.com/acme/app  "},
    )
    assert answers == {"repo": "https://github.com/acme/app"}

    fixed = _parse(value, {"repo": "main", input_name: "ignored"})
    assert fixed == {"repo": "main"}

    with pytest.raises(InvalidAction, match="empty custom input"):
        _parse(value, {"repo": "repo_url", input_name: "   "})


def test_multi_question_form_renders_custom_input_beside_dropdown() -> None:
    state = _fold(
        [
            {
                "key": "repo",
                "prompt": "Repository?",
                "options": [
                    {"label": "Main", "value": "main"},
                    {"label": "Other", "value": "repo_url", "allow_input": True},
                ],
            },
            {
                "key": "branch",
                "prompt": "Branch?",
                "options": [{"label": "Feature", "value": "feature", "allow_input": True}],
            },
        ]
    )
    card = render(state.card_state)
    form = _form(card)
    tags = [el.get("tag") for el in form["elements"]]
    assert tags.count("select_static") == 2
    assert tags.count("input") == 2
    value = _submit_value(card)
    repo_input = _input_name(value, "repo", "repo_url")
    branch_input = _input_name(value, "branch", "feature")
    answers = _parse(
        value,
        {
            "repo": "main",
            "branch": "feature",
            repo_input: "unused",
            branch_input: "  topic/ask  ",
        },
    )
    assert answers == {"repo": "main", "branch": "topic/ask"}


def test_resolved_receipt_keeps_custom_text_that_matches_another_value() -> None:
    state = _fold(
        [
            {
                "key": "repo",
                "prompt": "Which repository?",
                "options": [
                    {"label": "Main repository", "value": "main"},
                    {"label": "Other", "value": "other", "allow_input": True},
                ],
            }
        ]
    )
    card = render(state.card_state)
    value = _submit_value(card)
    input_name = _input_name(value, "repo", "other")
    answers = _parse(value, {"repo": "other", input_name: "main"})
    fold_event(
        {
            "type": "ask_user_resolved",
            "data": {"question_id": "q_custom", "answers": answers},
        },
        state,
        now=1.0,
    )
    pending = state.card_state.pending_input
    assert pending is not None
    assert pending.resolved_choice == "main"


def test_multi_select_allow_input_replaces_only_the_custom_choice() -> None:
    state = _fold(
        [
            {
                "key": "tags",
                "prompt": "Tags",
                "multi_select": True,
                "options": [
                    {"label": "A", "value": "a"},
                    {"label": "Other", "value": "other", "allow_input": True},
                ],
            }
        ]
    )
    card = render(state.card_state)
    value = _submit_value(card)
    input_name = _input_name(value, "tags", "other")
    answers = _parse(value, {"tags": ["a", "other"], input_name: "zzz"})
    assert answers == {"tags": ["a", "zzz"]}
