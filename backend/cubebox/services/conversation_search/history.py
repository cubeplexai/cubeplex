"""Bounded, agent-facing formatting for persisted conversation messages."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from typing import Any

_SENSITIVE_ARGUMENT_PARTS = ("secret", "token", "password", "authorization", "api_key")


@dataclass(frozen=True)
class FormattedHistoryPage:
    turns: list[dict[str, Any]]
    has_more: bool
    next_before_seq: int | None
    estimated_tokens: int
    truncated: bool


@dataclass(frozen=True)
class FormattedToolResult:
    tool_call_id: str
    tool_name: str | None
    content: str
    is_error: bool
    estimated_tokens: int
    truncated: bool


def estimate_tokens(value: object) -> int:
    """Return a deliberately cheap estimate suitable for output bounding."""
    return max(1, len(json.dumps(value, ensure_ascii=False)) // 4)


def format_history_turns(
    messages: list[dict[str, Any]], *, n: int, max_tokens: int, before_seq: int | None
) -> FormattedHistoryPage:
    """Format the newest bounded user-initiated turns in chronological order."""
    turns = _build_turns(messages, before_seq=before_seq)
    if n <= 0 or max_tokens <= 0:
        return FormattedHistoryPage([], bool(turns), _first_seq(turns), 0, False)

    selected: list[dict[str, Any]] = []
    used_tokens = 0
    truncated = False
    for turn in reversed(turns):
        if len(selected) == n:
            break
        turn_tokens = estimate_tokens(turn)
        if used_tokens + turn_tokens <= max_tokens:
            selected.append(turn)
            used_tokens += turn_tokens
            continue
        if not selected:
            bounded_turn = _truncate_turn(turn, max_tokens)
            selected.append(bounded_turn)
            used_tokens = estimate_tokens(bounded_turn)
            truncated = True
        break

    selected.reverse()
    has_more = len(selected) < len(turns)
    return FormattedHistoryPage(
        turns=selected,
        has_more=has_more,
        next_before_seq=_first_seq(selected) if has_more else None,
        estimated_tokens=used_tokens,
        truncated=truncated,
    )


def format_tool_result(
    messages: list[dict[str, Any]], *, tool_call_id: str, max_tokens: int
) -> FormattedToolResult | None:
    """Return one bounded historical tool result, or ``None`` when absent."""
    for message in sorted(messages, key=_seq):
        if message.get("role") != "tool_result" or message.get("tool_call_id") != tool_call_id:
            continue
        content = _message_text(message)
        tool_name = _optional_string(message.get("tool_name"))
        is_error = bool(message.get("is_error"))
        if (
            estimate_tokens(_tool_result_payload(tool_call_id, tool_name, content, is_error, False))
            <= max_tokens
        ):
            return FormattedToolResult(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                content=content,
                is_error=is_error,
                estimated_tokens=estimate_tokens(
                    _tool_result_payload(tool_call_id, tool_name, content, is_error, False)
                ),
                truncated=False,
            )
        truncated_content = _truncate_tool_result_content(
            tool_call_id, tool_name, content, is_error, max_tokens
        )
        payload = _tool_result_payload(tool_call_id, tool_name, truncated_content, is_error, True)
        return FormattedToolResult(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            content=truncated_content,
            is_error=is_error,
            estimated_tokens=estimate_tokens(payload),
            truncated=True,
        )
    return None


def _build_turns(messages: list[dict[str, Any]], *, before_seq: int | None) -> list[dict[str, Any]]:
    turns: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    results: dict[str, bool] = {}
    for message in sorted(messages, key=_seq):
        if before_seq is not None and _seq(message) >= before_seq:
            continue
        role = message.get("role")
        if role == "tool_result":
            call_id = message.get("tool_call_id")
            if isinstance(call_id, str):
                results[call_id] = bool(message.get("is_error"))
            continue
        if role == "user":
            current = {
                "seq": _seq(message),
                "user": {"text": _message_text(message)},
                "assistant": {"text": ""},
                "tool_calls": [],
            }
            turns.append(current)
            continue
        if current is None or role != "assistant":
            continue
        text = _message_text(message)
        if text:
            current["assistant"]["text"] += text
        for block in _tool_call_blocks(message):
            call_id = block.get("id")
            if not isinstance(call_id, str):
                continue
            current["tool_calls"].append(
                {
                    "tool_call_id": call_id,
                    "name": _optional_string(block.get("name")),
                    "arguments": _redact_arguments(block.get("arguments")),
                    "status": "pending",
                }
            )

    for turn in turns:
        for call in turn["tool_calls"]:
            call_id = call["tool_call_id"]
            if call_id in results:
                call["status"] = "errored" if results[call_id] else "completed"
    return turns


def _seq(message: dict[str, Any]) -> int:
    value = message.get("seq")
    return value if isinstance(value, int) else 0


def _first_seq(turns: list[dict[str, Any]]) -> int | None:
    return turns[0]["seq"] if turns else None


def _message_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if not isinstance(content, list):
        return ""
    return "\n".join(
        block["text"]
        for block in content
        if isinstance(block, dict)
        and block.get("type") == "text"
        and isinstance(block.get("text"), str)
    )


def _tool_call_blocks(message: dict[str, Any]) -> list[dict[str, Any]]:
    content = message.get("content")
    if not isinstance(content, list):
        return []
    return [
        block for block in content if isinstance(block, dict) and block.get("type") == "tool_call"
    ]


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _redact_arguments(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]"
            if any(part in key.lower() for part in _SENSITIVE_ARGUMENT_PARTS)
            else _redact_arguments(item)
            for key, item in value.items()
            if isinstance(key, str)
        }
    if isinstance(value, list):
        return [_redact_arguments(item) for item in value]
    return value


def _truncate_turn(turn: dict[str, Any], max_tokens: int) -> dict[str, Any]:
    bounded = copy.deepcopy(turn)
    fields = [
        (bounded["user"], turn["user"]["text"], "text"),
        (bounded["assistant"], turn["assistant"]["text"], "text"),
        *_argument_string_fields(bounded, turn),
    ]
    for container, _, key in fields:
        container[key] = ""
    if estimate_tokens(bounded) > max_tokens:
        bounded["user"]["text"] = _truncate_text(turn["user"]["text"], max_tokens // 2)
        bounded["assistant"]["text"] = _truncate_text(turn["assistant"]["text"], max_tokens // 2)
        return bounded
    for container, source, key in fields:
        container[key] = _longest_prefix_that_fits(
            container, key, source, max_tokens, lambda: estimate_tokens(bounded)
        )
    return bounded


def _argument_string_fields(
    bounded_turn: dict[str, Any], source_turn: dict[str, Any]
) -> list[tuple[Any, str, str | int]]:
    fields: list[tuple[Any, str, str | int]] = []
    for bounded_call, source_call in zip(
        bounded_turn["tool_calls"], source_turn["tool_calls"], strict=True
    ):
        fields.extend(_string_fields(bounded_call["arguments"], source_call["arguments"]))
    return fields


def _string_fields(bounded_value: object, source_value: object) -> list[tuple[Any, str, str | int]]:
    fields: list[tuple[Any, str, str | int]] = []
    if isinstance(bounded_value, dict) and isinstance(source_value, dict):
        values: list[tuple[str | int, object, object]] = [
            (key, value, source_value.get(key)) for key, value in bounded_value.items()
        ]
    elif isinstance(bounded_value, list) and isinstance(source_value, list):
        values = list(zip(range(len(bounded_value)), bounded_value, source_value, strict=False))
    else:
        return fields
    for key, value, source in values:
        if isinstance(value, str) and isinstance(source, str) and value != "[REDACTED]":
            fields.append((bounded_value, source, key))
        else:
            fields.extend(_string_fields(value, source))
    return fields


def _longest_prefix_that_fits(
    container: Any, key: str | int, text: str, max_tokens: int, estimate: Any
) -> str:
    lower = 0
    upper = len(text)
    while lower < upper:
        middle = (lower + upper + 1) // 2
        candidate = text[:middle]
        container[key] = candidate
        if estimate() <= max_tokens:
            lower = middle
        else:
            upper = middle - 1
    return _truncate_text(text[:lower], max(1, (lower + 3) // 4))


def _tool_result_payload(
    tool_call_id: str, tool_name: str | None, content: str, is_error: bool, truncated: bool
) -> dict[str, object]:
    return {
        "tool_call_id": tool_call_id,
        "tool_name": tool_name,
        "content": content,
        "is_error": is_error,
        "truncated": truncated,
    }


def _truncate_tool_result_content(
    tool_call_id: str, tool_name: str | None, content: str, is_error: bool, max_tokens: int
) -> str:
    lower = 0
    upper = len(content)
    while lower < upper:
        middle = (lower + upper + 1) // 2
        candidate = content[:middle]
        if (
            estimate_tokens(
                _tool_result_payload(tool_call_id, tool_name, candidate, is_error, True)
            )
            <= max_tokens
        ):
            lower = middle
        else:
            upper = middle - 1
    return _truncate_text(content[:lower], max(1, (lower + 3) // 4))


def _truncate_text(text: str, max_tokens: int) -> str:
    max_chars = max(0, max_tokens * 4)
    if len(text) <= max_chars:
        return text
    if max_chars <= 3:
        return text[:max_chars]
    return f"{text[: max_chars - 3]}..."
