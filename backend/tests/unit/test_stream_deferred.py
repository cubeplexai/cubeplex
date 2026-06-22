"""Streaming unwrap of cubepi's `deferred_tool_call` dispatcher.

The dispatcher carries the real tool call inside its `arguments` envelope:

    name=deferred_tool_call
    arguments={"tool_name": "<real>", "arguments": {...}}

cubepi rewrites the call via `resolve_tool_call` before execution, so the
`tool_result` event uses the real tool name. Without unwrap here, the
streamed `tool_call_delta` / `tool_call` events would carry
`deferred_tool_call` plus a wrapper-JSON delta — the frontend would render
an opaque dispatcher card and only learn the real name when the result
arrives. `StreamConverter` peels the wrapper progressively so the title and
args stream as if the model had called the real tool directly.
"""

from __future__ import annotations

import json

from cubepi.providers.base import (
    AssistantMessage,
    StreamEvent,
    ToolCall,
    Usage,
)

from cubebox.agents.stream import StreamConverter


def _mk_assistant(tool_calls: list[ToolCall] | None = None) -> AssistantMessage:
    content: list = list(tool_calls or [])
    return AssistantMessage(content=content, usage=Usage())


def _deferred_partial(call_id: str = "tc1") -> AssistantMessage:
    """Partial message during streaming — ToolCall.arguments is still {} per the
    Anthropic provider; raw JSON lives in the delta string and we accumulate it
    in the converter's state."""
    return _mk_assistant(tool_calls=[ToolCall(id=call_id, name="deferred_tool_call", arguments={})])


def _feed(conv: StreamConverter, deltas: list[str]) -> list[list[dict]]:
    """Feed a sequence of toolcall_delta events at content_index=0 with the
    given delta chunks; return the converter's emitted dict lists per delta."""
    out: list[list[dict]] = []
    partial = _deferred_partial()
    for chunk in deltas:
        evt = StreamEvent(
            type="toolcall_delta",
            delta=chunk,
            partial=partial,
            content_index=0,
        )
        out.append(conv.convert(evt))
    return out


# ---------------------------------------------------------------------------
# toolcall_end (terminal) unwrap


def test_deferred_toolcall_end_emits_unwrapped_real_tool_call() -> None:
    """toolcall_end with name=deferred_tool_call and a parsed arguments dict
    must emit a `tool_call` SSE event whose name = inner.tool_name and
    arguments = inner.arguments. Without this the frontend card title stays
    `deferred_tool_call` and the args are the wrapper dict, not the real
    call's args."""
    inner_args = {"path": "a.txt", "content": "hello"}
    partial = _mk_assistant(
        tool_calls=[
            ToolCall(
                id="tc1",
                name="deferred_tool_call",
                arguments={"tool_name": "file_write", "arguments": inner_args},
            )
        ]
    )
    evt = StreamEvent(type="toolcall_end", content_index=0, partial=partial)
    out = StreamConverter().convert(evt)
    assert len(out) == 1
    assert out[0] == {
        "type": "tool_call",
        "id": "tc1",
        "name": "file_write",
        "arguments": inner_args,
    }


def test_non_deferred_toolcall_end_passes_through_unchanged() -> None:
    """Non-deferred tool calls keep the existing translation (no unwrap)."""
    partial = _mk_assistant(
        tool_calls=[ToolCall(id="tc1", name="file_write", arguments={"path": "a.txt"})]
    )
    evt = StreamEvent(type="toolcall_end", content_index=0, partial=partial)
    out = StreamConverter().convert(evt)
    assert out == [
        {
            "type": "tool_call",
            "id": "tc1",
            "name": "file_write",
            "arguments": {"path": "a.txt"},
        }
    ]


# ---------------------------------------------------------------------------
# Streaming unwrap — tool_name then arguments (typical schema order)


def test_streaming_emits_nothing_before_tool_name_known() -> None:
    """Until the `tool_name` value's closing quote is in the buffer, nothing
    streams — we don't know what title to attach to the card yet."""
    conv = StreamConverter()
    emits = _feed(conv, ['{"tool_n', 'ame": "fi'])
    assert emits == [[], []]


def test_streaming_starts_emitting_once_inner_args_opens() -> None:
    """First emit carries the resolved real tool name and the chars seen so far
    *inside* the inner arguments value — the wrapper prefix is dropped."""
    conv = StreamConverter()
    emits = _feed(
        conv,
        [
            '{"tool_name": "file_write", "arg',
            'uments": {"path":',
            ' "a.txt"',
        ],
    )
    # First two chunks: title not yet resolvable to "args started" — no emit
    # until the inner '{' is in the buffer.
    assert emits[0] == []
    # Second chunk reaches `"arguments": {` — emit kicks in with the inner '{'.
    assert len(emits[1]) == 1
    first = emits[1][0]
    assert first["type"] == "tool_call_delta"
    assert first["index"] == 0
    assert first["id"] == "tc1"
    assert first["name"] == "file_write"
    assert first["delta"] == '{"path":'
    # Third chunk: append the rest of the inner value seen so far.
    assert len(emits[2]) == 1
    assert emits[2][0]["delta"] == ' "a.txt"'
    assert emits[2][0]["name"] == "file_write"


def test_streaming_stops_at_inner_args_close() -> None:
    """When the closing `}` of inner arguments arrives — possibly followed by
    the outer `}` — only the inner closing brace is emitted, never the wrapper
    close."""
    conv = StreamConverter()
    emits = _feed(
        conv,
        [
            '{"tool_name": "file_write", "arguments": {"path": "a.txt"',
            "}}",
        ],
    )
    # First chunk emits the inner-args prefix up to current end.
    assert emits[0][0]["delta"] == '{"path": "a.txt"'
    # Second chunk emits only the inner '}' — outer '}' is dropped.
    assert len(emits[1]) == 1
    assert emits[1][0]["delta"] == "}"


# ---------------------------------------------------------------------------
# Streaming unwrap — arguments first, then tool_name


def test_streaming_buffers_when_arguments_appears_before_tool_name() -> None:
    """Some models may emit `arguments` before `tool_name` (the JSON object key
    order is not guaranteed). The converter must NOT emit args deltas with no
    title — it buffers until tool_name is resolved, then emits the whole
    inner-args block in one chunk with the resolved name."""
    conv = StreamConverter()
    emits = _feed(
        conv,
        [
            '{"arguments": {"path": "a.txt"}, "tool_n',
            'ame": "file_write"}',
        ],
    )
    # First chunk: args fully seen but tool_name still unknown → no emit yet.
    assert emits[0] == []
    # Second chunk: tool_name resolves; the entire buffered inner-args block
    # is emitted as one delta carrying the now-known name.
    assert len(emits[1]) == 1
    assert emits[1][0]["name"] == "file_write"
    assert emits[1][0]["delta"] == '{"path": "a.txt"}'


# ---------------------------------------------------------------------------
# Streaming unwrap — single-shot full delta


def test_streaming_full_payload_in_one_delta_unwraps_inner() -> None:
    """If the provider hands us the whole JSON in one delta event, the emit
    still strips the wrapper and shows real name + inner args."""
    conv = StreamConverter()
    emits = _feed(
        conv,
        ['{"tool_name": "file_write", "arguments": {"path": "a.txt"}}'],
    )
    assert len(emits[0]) == 1
    assert emits[0][0]["name"] == "file_write"
    assert emits[0][0]["delta"] == '{"path": "a.txt"}'


def test_streaming_empty_inner_arguments() -> None:
    """`"arguments": {}` produces a `{}` delta — distinguishes deliberate no-arg
    calls from the wrapper-not-yet-opened case."""
    conv = StreamConverter()
    emits = _feed(conv, ['{"tool_name": "ping", "arguments": {}}'])
    assert emits[0][0]["delta"] == "{}"
    assert emits[0][0]["name"] == "ping"


# ---------------------------------------------------------------------------
# Non-deferred streaming — must pass through unchanged


def test_non_deferred_streaming_delta_passes_through_with_identity() -> None:
    """Non-deferred tool calls keep their original delta JSON and outer name."""
    conv = StreamConverter()
    partial = _mk_assistant(tool_calls=[ToolCall(id="tc1", name="file_write", arguments={})])
    evt = StreamEvent(
        type="toolcall_delta",
        delta='{"path": "a.txt"',
        partial=partial,
        content_index=0,
    )
    out = conv.convert(evt)
    assert out == [
        {
            "type": "tool_call_delta",
            "delta": '{"path": "a.txt"',
            "index": 0,
            "id": "tc1",
            "name": "file_write",
        }
    ]


# ---------------------------------------------------------------------------
# Independent state per content_index — parallel deferred calls don't bleed


def test_independent_state_per_content_index() -> None:
    """Two deferred calls streamed in parallel (different content_index)
    must not mix buffers — each gets its own scanning state."""
    conv = StreamConverter()
    p0 = _mk_assistant(
        tool_calls=[
            ToolCall(id="tc0", name="deferred_tool_call", arguments={}),
            ToolCall(id="tc1", name="deferred_tool_call", arguments={}),
        ]
    )

    def evt(idx: int, delta: str) -> StreamEvent:
        return StreamEvent(
            type="toolcall_delta",
            delta=delta,
            partial=p0,
            content_index=idx,
        )

    # Interleave deltas for tc0 (file_write) and tc1 (file_read).
    conv.convert(evt(0, '{"tool_name": "file_write", '))
    conv.convert(evt(1, '{"tool_name": "file_read", '))
    out0 = conv.convert(evt(0, '"arguments": {"path": "a.txt"}}'))
    out1 = conv.convert(evt(1, '"arguments": {"path": "b.txt"}}'))
    assert out0[0]["name"] == "file_write"
    assert out0[0]["delta"] == '{"path": "a.txt"}'
    assert out1[0]["name"] == "file_read"
    assert out1[0]["delta"] == '{"path": "b.txt"}'


# ---------------------------------------------------------------------------
# State cleanup at toolcall_end — the deferred buffer must not accumulate


def test_deferred_state_cleared_after_toolcall_end() -> None:
    """toolcall_end must drop the per-content-index buffer so a long run with
    many deferred calls doesn't grow the converter's working set unboundedly."""
    conv = StreamConverter()
    _feed(conv, ['{"tool_name": "file_write", "arguments": {"path": "a.txt"}}'])
    assert 0 in conv._deferred  # streaming state is present mid-call
    end_evt = StreamEvent(
        type="toolcall_end",
        content_index=0,
        partial=_mk_assistant(
            tool_calls=[
                ToolCall(
                    id="tc1",
                    name="deferred_tool_call",
                    arguments={"tool_name": "file_write", "arguments": {"path": "a.txt"}},
                )
            ]
        ),
    )
    conv.convert(end_evt)
    assert conv._deferred == {}


def test_parallel_deferred_end_clears_only_finished_call() -> None:
    """Ending one parallel deferred call must NOT wipe the in-flight sibling's
    buffer — that would corrupt the still-streaming call's args."""
    conv = StreamConverter()
    partial = _mk_assistant(
        tool_calls=[
            ToolCall(id="tc0", name="deferred_tool_call", arguments={}),
            ToolCall(id="tc1", name="deferred_tool_call", arguments={}),
        ]
    )

    def delta_evt(idx: int, chunk: str) -> StreamEvent:
        return StreamEvent(type="toolcall_delta", delta=chunk, partial=partial, content_index=idx)

    conv.convert(delta_evt(0, '{"tool_name": "file_write", "arguments": {"path": "a.txt"}}'))
    conv.convert(delta_evt(1, '{"tool_name": "file_read", "arguments": {"pa'))
    assert 0 in conv._deferred and 1 in conv._deferred

    end_evt0 = StreamEvent(
        type="toolcall_end",
        content_index=0,
        partial=_mk_assistant(
            tool_calls=[
                ToolCall(
                    id="tc0",
                    name="deferred_tool_call",
                    arguments={"tool_name": "file_write", "arguments": {"path": "a.txt"}},
                ),
                ToolCall(id="tc1", name="deferred_tool_call", arguments={}),
            ]
        ),
    )
    conv.convert(end_evt0)
    assert 0 not in conv._deferred
    assert 1 in conv._deferred  # sibling still streaming — buffer intact


def test_non_deferred_end_clears_any_stale_state_at_index() -> None:
    """If an earlier deferred call somehow left state at an index and a
    non-deferred end fires there next, the stale buffer must be dropped — a
    defense-in-depth guarantee that no idx is silently shared across calls."""
    conv = StreamConverter()
    # Plant stale state by partially streaming a deferred call at idx=0.
    _feed(conv, ['{"tool_name": "x", "arguments": {"y": 1'])
    assert 0 in conv._deferred
    non_deferred_end = StreamEvent(
        type="toolcall_end",
        content_index=0,
        partial=_mk_assistant(
            tool_calls=[ToolCall(id="tc-other", name="file_write", arguments={"path": "a.txt"})]
        ),
    )
    conv.convert(non_deferred_end)
    assert conv._deferred == {}


# ---------------------------------------------------------------------------
# String escapes inside inner args must not confuse the brace scanner


def test_streaming_handles_escaped_braces_in_inner_string_value() -> None:
    """A `}` inside a string value must not close the inner-args object early.
    Regression guard for the brace-depth scanner ignoring string contents."""
    conv = StreamConverter()
    emits = _feed(
        conv,
        [
            '{"tool_name": "echo", "arguments": {"msg": "use }} braces"}}',
        ],
    )
    body = json.loads(emits[0][0]["delta"])
    assert body == {"msg": "use }} braces"}


def test_streaming_handles_escaped_quote_in_string_value() -> None:
    """An escaped `\\\"` inside a string must not be treated as a close-quote."""
    conv = StreamConverter()
    emits = _feed(
        conv,
        ['{"tool_name": "echo", "arguments": {"msg": "say \\"hi\\""}}'],
    )
    body = json.loads(emits[0][0]["delta"])
    assert body == {"msg": 'say "hi"'}
