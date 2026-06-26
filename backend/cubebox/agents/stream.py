"""cubepi event → cubebox SSE event dict translation.

Two layers:

1. ``StreamConverter`` — stateful per-stream translator. Use one instance per
   agent run when you need progressive unwrap of cubepi's
   ``deferred_tool_call`` dispatcher (the LLM-streamed deltas carry the
   wrapper JSON ``{"tool_name": ..., "arguments": ...}``; cubepi rewrites the
   call at execute time, so the ``tool_result`` arrives under the real name
   but the streamed ``tool_call_delta`` events would otherwise look like
   ``deferred_tool_call`` until ``toolcall_end``).

2. ``convert_event_to_sse`` / ``convert_agent_event_to_sse`` — stateless
   one-off wrappers around a fresh ``StreamConverter`` instance. Suitable for
   tests and any caller that processes a single event in isolation; the
   deferred unwrap still works for a complete event (e.g. ``toolcall_end``)
   but cannot stitch deltas across calls.

cubebox SSE event types (consumed by frontend):
    text_delta, reasoning, tool_call, tool_call_delta, tool_result,
    usage, error, done, artifact, injected_message,
    sandbox_confirm_request, sandbox_confirm_resolved,
    ask_user_request, ask_user_resolved.
"""

from __future__ import annotations

import json
from typing import Any

from cubepi import AgentToolResult
from cubepi.agent.types import (
    AgentEvent,
    HitlAnswerEvent,
    HitlRequestEvent,
    MessageEndEvent,
    MessageUpdateEvent,
    ToolExecutionEndEvent,
)
from cubepi.hitl.types import ApproveAnswer
from cubepi.providers.base import (
    AssistantMessage,
    StreamEvent,
    TextContent,
    ToolCall,
    UserMessage,
)

# cubepi's deferred dispatcher exposes this tool name to the model. Matches
# ``DISPATCH_TOOL_NAME`` in cubepi.deferred._dispatch_tool; pinned by string
# because it's a wire contract — changing it requires coordinated updates on
# both sides.
_DEFERRED_DISPATCH_TOOL_NAME = "deferred_tool_call"


def _stringify_tool_result(result: Any) -> tuple[str, Any]:
    """Extract a string and details payload from a cubepi tool result.

    ``ToolExecutionEndEvent.result`` is typed ``Any`` but is in practice an
    ``AgentToolResult`` whose ``content`` is a list of cubepi content blocks
    (text/image/etc.). The previous implementation forwarded the model
    object as-is and let downstream ``str()`` produce a Pydantic repr —
    which broke frontend JSON parsers (e.g. ``save_artifact`` rendering
    fell through to a regular tool-call card instead of the artifact card).

    We concatenate ``TextContent.text`` blocks and surface
    ``AgentToolResult.details`` separately so the live SSE shape matches
    the post-reload one (``ToolResultMessage.details``).
    """
    if isinstance(result, AgentToolResult):
        # CitationMiddleware rewrites .content to 【N-M】-marked chunk text for
        # the LLM and stashes the pre-rewrite raw output in
        # details["original_content"] so the frontend preview can still parse
        # the original (e.g. JSON for web_search). Prefer it when present.
        details = result.details
        if isinstance(details, dict) and isinstance(details.get("original_content"), str):
            return details["original_content"], details
        text = "".join(b.text for b in result.content if isinstance(b, TextContent))
        return text, details
    if isinstance(result, str):
        return result, None
    if result is None:
        return "", None
    return str(result), None


_ARTIFACT_PRODUCING_TOOLS = frozenset({"save_artifact", "generate_image"})


def _artifact_event_from_tool_result(
    tool_name: str, is_error: bool, result_text: str
) -> dict[str, Any] | None:
    """Build an artifact SSE dict from a tool result that produces artifacts.

    Both save_artifact and generate_image return
    ``{"action": ..., "artifact": {...}}`` as their result content.
    We surface that as a standalone ``artifact`` event so the frontend
    store is updated during the live run.
    """
    if tool_name not in _ARTIFACT_PRODUCING_TOOLS or is_error:
        return None
    try:
        parsed = json.loads(result_text)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(parsed, dict):
        return None
    artifact = parsed.get("artifact")
    if not isinstance(artifact, dict):
        return None
    return {
        "type": "artifact",
        "action": parsed.get("action", "created"),
        "artifact": artifact,
    }


# ---------------------------------------------------------------------------
# Streaming JSON scanner — extracts top-level `tool_name` / `arguments` value
# spans from a partial deferred-call wrapper while it's being streamed.
# ---------------------------------------------------------------------------


def _scan_string(raw: str, start: int) -> tuple[int, bool]:
    """Scan a JSON string starting at ``raw[start] == '"'``.

    Returns ``(end_offset, done)`` where ``end_offset`` points at the closing
    ``"`` (so ``raw[start:end_offset + 1]`` is the full JSON string literal).
    ``done`` is True only when the closing quote is present.
    """
    n = len(raw)
    if start >= n or raw[start] != '"':
        return start, False
    i = start + 1
    while i < n:
        c = raw[i]
        if c == "\\":
            i += 2
            continue
        if c == '"':
            return i, True
        i += 1
    return n, False


def _scan_object(raw: str, start: int) -> tuple[int, bool]:
    """Scan a JSON object starting at ``raw[start] == '{'``.

    Returns ``(end_offset, done)`` where ``end_offset`` is the index JUST PAST
    the closing ``}`` (so ``raw[start:end_offset]`` is the full object).
    Tracks string state so braces inside string values don't pop depth early.
    """
    n = len(raw)
    if start >= n or raw[start] != "{":
        return start, False
    depth = 1
    in_string = False
    i = start + 1
    while i < n:
        c = raw[i]
        if in_string:
            if c == "\\":
                i += 2
                continue
            if c == '"':
                in_string = False
            i += 1
            continue
        if c == '"':
            in_string = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i + 1, True
        i += 1
    return n, False


def _scan_array(raw: str, start: int) -> tuple[int, bool]:
    n = len(raw)
    if start >= n or raw[start] != "[":
        return start, False
    depth = 1
    in_string = False
    i = start + 1
    while i < n:
        c = raw[i]
        if in_string:
            if c == "\\":
                i += 2
                continue
            if c == '"':
                in_string = False
            i += 1
            continue
        if c == '"':
            in_string = True
        elif c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                return i + 1, True
        i += 1
    return n, False


def _scan_value(raw: str, start: int) -> tuple[int, bool]:
    """Skip past any JSON value beginning at ``raw[start]``."""
    n = len(raw)
    if start >= n:
        return start, False
    c = raw[start]
    if c == '"':
        end, done = _scan_string(raw, start)
        return (end + 1, True) if done else (end, False)
    if c == "{":
        return _scan_object(raw, start)
    if c == "[":
        return _scan_array(raw, start)
    for lit in ("true", "false", "null"):
        if raw.startswith(lit, start):
            return start + len(lit), True
    if c == "-" or c.isdigit():
        i = start
        if raw[i] == "-":
            i += 1
        while i < n and (raw[i].isdigit() or raw[i] in ".eE+-"):
            i += 1
        # We can't tell whether a number is "done" mid-stream without lookahead;
        # treat it as done iff we hit a JSON structural char after digits.
        if i < n and raw[i] in ",}] \t\n\r":
            return i, True
        return i, False
    return start, False


def _scan_deferred_wrapper(raw: str) -> tuple[str | None, int | None, int | None]:
    """Scan a partial ``deferred_tool_call`` wrapper JSON object.

    Returns ``(tool_name, args_value_start, args_value_end)`` — any field that
    is not yet resolvable from ``raw`` is ``None``. ``args_value_*`` are byte
    offsets into ``raw``: ``args_value_start`` points at the inner ``{`` and
    ``args_value_end`` at the index JUST PAST the inner closing ``}``.
    """
    tool_name: str | None = None
    args_start: int | None = None
    args_end: int | None = None

    n = len(raw)
    i = 0
    while i < n and raw[i] in " \t\n\r":
        i += 1
    if i >= n or raw[i] != "{":
        return None, None, None
    i += 1

    while i < n:
        while i < n and raw[i] in " \t\n\r,":
            i += 1
        if i >= n or raw[i] == "}":
            break
        if raw[i] != '"':
            break
        key_end, key_done = _scan_string(raw, i)
        if not key_done:
            break
        try:
            key = json.loads(raw[i : key_end + 1])
        except json.JSONDecodeError:
            break
        i = key_end + 1
        while i < n and raw[i] in " \t\n\r":
            i += 1
        if i >= n or raw[i] != ":":
            break
        i += 1
        while i < n and raw[i] in " \t\n\r":
            i += 1
        if i >= n:
            break

        if key == "tool_name" and tool_name is None:
            if raw[i] != '"':
                break
            v_end, v_done = _scan_string(raw, i)
            if not v_done:
                break
            try:
                tool_name = json.loads(raw[i : v_end + 1])
            except json.JSONDecodeError:
                break
            i = v_end + 1
        elif key == "arguments" and args_start is None:
            if raw[i] != "{":
                break
            args_start = i
            v_end, v_done = _scan_object(raw, i)
            if v_done:
                args_end = v_end
                i = v_end
            else:
                # Inner object still streaming — start known, end unknown.
                break
        else:
            v_end, v_done = _scan_value(raw, i)
            if not v_done:
                break
            i = v_end

    return tool_name, args_start, args_end


# ---------------------------------------------------------------------------
# Streaming converter — holds per-content-index state across deltas so the
# deferred-call wrapper can be peeled progressively as the LLM streams it.
# ---------------------------------------------------------------------------


class _DeferredCallState:
    """State for one in-flight deferred_tool_call streaming through the agent."""

    __slots__ = ("raw", "tool_id", "tool_name", "args_value_start", "emitted_inner_chars")

    def __init__(self, tool_id: str) -> None:
        self.raw: str = ""
        self.tool_id: str = tool_id
        self.tool_name: str | None = None
        self.args_value_start: int | None = None
        self.emitted_inner_chars: int = 0


class StreamConverter:
    """Stateful translator from cubepi events to cubebox SSE dicts.

    Use one instance per agent run / subscriber listener. The state holds
    in-progress ``deferred_tool_call`` buffers keyed by ``content_index`` —
    cubepi's provider emits ``toolcall_delta`` events whose ``partial`` carries
    the resolved wrapper name (``deferred_tool_call``) but not the raw JSON
    being streamed inside ``arguments``; that JSON only exists as the
    concatenation of every ``delta`` chunk we receive. We accumulate locally,
    pull the inner ``tool_name`` and inner ``arguments`` value span out via
    :func:`_scan_deferred_wrapper`, and emit synthetic ``tool_call_delta``
    events whose ``name`` / ``delta`` look like a direct call to the real
    tool — so the frontend card renders with the real title and only the
    inner JSON streams into the args view.
    """

    def __init__(self) -> None:
        self._deferred: dict[int, _DeferredCallState] = {}

    # ------------------------------------------------------------------
    # Top-level entrypoints

    def convert(self, evt: StreamEvent) -> list[dict[str, Any]]:
        """Translate a single cubepi StreamEvent into 0..N cubebox SSE dicts."""
        t = evt.type
        if t == "text_delta":
            return [{"type": "text_delta", "delta": evt.delta or ""}]
        if t == "thinking_delta":
            return [{"type": "reasoning", "delta": evt.delta or ""}]
        if t == "toolcall_delta":
            return self._on_toolcall_delta(evt)
        if t == "toolcall_end":
            return self._on_toolcall_end(evt)
        if t == "done":
            return [{"type": "done"}]
        if t == "error":
            return [{"type": "error", "error": evt.error_message or "unknown error"}]
        return []

    def convert_agent_event(self, evt: AgentEvent) -> list[dict[str, Any]]:
        """Translate a single cubepi AgentEvent into 0..N cubebox SSE dicts."""
        if isinstance(evt, MessageUpdateEvent):
            return self.convert(evt.stream_event)
        return _convert_terminal_agent_event(evt)

    # ------------------------------------------------------------------
    # toolcall_delta / toolcall_end with deferred unwrap

    def _block_for(self, evt: StreamEvent) -> ToolCall | None:
        if evt.partial is None or evt.content_index is None:
            return None
        try:
            block = evt.partial.content[evt.content_index]
        except (IndexError, TypeError):
            return None
        return block if isinstance(block, ToolCall) else None

    def _on_toolcall_delta(self, evt: StreamEvent) -> list[dict[str, Any]]:
        block = self._block_for(evt)
        idx = evt.content_index
        delta_chunk = evt.delta or ""

        if block is None or block.name != _DEFERRED_DISPATCH_TOOL_NAME:
            out: dict[str, Any] = {
                "type": "tool_call_delta",
                "delta": delta_chunk,
                "index": idx,
            }
            if block is not None:
                out["id"] = block.id
                out["name"] = block.name
            return [out]

        assert idx is not None  # _block_for guarantees both partial and index
        state = self._deferred.setdefault(idx, _DeferredCallState(tool_id=block.id))
        state.raw += delta_chunk

        tool_name, args_start, args_end = _scan_deferred_wrapper(state.raw)
        if tool_name is not None and state.tool_name is None:
            state.tool_name = tool_name
        if args_start is not None and state.args_value_start is None:
            state.args_value_start = args_start

        # Emit only once BOTH the real name and the inner-args open brace are
        # in view — otherwise we'd either label the args under the wrong title
        # or stream a wrapper prefix the frontend has no use for.
        if state.tool_name is None or state.args_value_start is None:
            return []

        slice_end = args_end if args_end is not None else len(state.raw)
        slice_start = state.args_value_start + state.emitted_inner_chars
        if slice_end <= slice_start:
            return []
        new_chars = state.raw[slice_start:slice_end]
        state.emitted_inner_chars += len(new_chars)
        return [
            {
                "type": "tool_call_delta",
                "delta": new_chars,
                "index": idx,
                "id": state.tool_id,
                "name": state.tool_name,
            }
        ]

    def _on_toolcall_end(self, evt: StreamEvent) -> list[dict[str, Any]]:
        block = self._block_for(evt)
        if block is None:
            return []
        idx = evt.content_index
        assert idx is not None

        if block.name != _DEFERRED_DISPATCH_TOOL_NAME:
            self._deferred.pop(idx, None)
            return [
                {
                    "type": "tool_call",
                    "id": block.id,
                    "name": block.name,
                    "arguments": block.arguments,
                }
            ]

        # block.arguments at toolcall_end is the parsed wrapper dict:
        # {"tool_name": "<real>", "arguments": {...}}. cubepi's resolver will
        # rewrite the call to <real> + inner arguments before execute; we
        # surface the same shape to the frontend so the tool_call event lines
        # up with the eventual tool_result event (which uses the real name).
        resolved = _resolve_deferred_wrapper(block.arguments)
        self._deferred.pop(idx, None)
        if resolved is None:
            # Wrapper failed cubepi's resolver checks (missing/non-str
            # tool_name, or non-dict non-None arguments) — emit the raw
            # dispatcher call so cubepi's "Unknown deferred tool" / dispatcher
            # error fallback still surfaces in the same card.
            return [
                {
                    "type": "tool_call",
                    "id": block.id,
                    "name": block.name,
                    "arguments": block.arguments,
                }
            ]
        inner_name, inner_args = resolved
        return [
            {
                "type": "tool_call",
                "id": block.id,
                "name": inner_name,
                "arguments": inner_args,
            }
        ]


def _convert_terminal_agent_event(evt: AgentEvent) -> list[dict[str, Any]]:
    """Translation for AgentEvents that don't carry streaming state."""
    if isinstance(evt, ToolExecutionEndEvent):
        text, details = _stringify_tool_result(evt.result)
        out: list[dict[str, Any]] = [
            {
                "type": "tool_result",
                "tool_call_id": evt.tool_call_id,
                "name": evt.tool_name,
                "result": text,
                "details": details,
                "is_error": evt.is_error,
            }
        ]
        artifact_event = _artifact_event_from_tool_result(evt.tool_name, evt.is_error, text)
        if artifact_event is not None:
            out.append(artifact_event)
        return out

    if isinstance(evt, MessageEndEvent) and isinstance(evt.message, AssistantMessage):
        msg = evt.message
        if msg.usage is not None and msg.usage.input_tokens > 0:
            return [
                {
                    "type": "usage",
                    "input_tokens": msg.usage.input_tokens,
                    "output_tokens": msg.usage.output_tokens or 0,
                    "cache_read_tokens": msg.usage.cache_read_tokens or 0,
                    "cache_write_tokens": msg.usage.cache_write_tokens or 0,
                }
            ]

    if isinstance(evt, MessageEndEvent) and isinstance(evt.message, UserMessage):
        meta = evt.message.metadata
        steer_id = meta.get("steer_id")
        if steer_id:
            text = "".join(c.text for c in evt.message.content if isinstance(c, TextContent))
            injected: dict[str, Any] = {
                "type": "injected_message",
                "content": text,
                "steer_id": steer_id,
            }
            # Group-chat sender identity so live viewers render the SenderBadge
            # without waiting for a refresh (history reads these from metadata).
            if meta.get("sender_user_id") and meta.get("sender_display_name"):
                injected["sender_user_id"] = meta["sender_user_id"]
                injected["sender_display_name"] = meta["sender_display_name"]
            return [injected]

    if isinstance(evt, HitlRequestEvent):
        req = evt.request
        payload = req.payload
        if payload.kind == "approve":
            return [
                {
                    "type": "sandbox_confirm_request",
                    "question_id": req.question_id,
                    "tool_call_id": payload.tool_call_id,
                    "tool_name": payload.tool_name,
                    "args": payload.args,
                    "details": payload.details,
                    "timeout_seconds": req.timeout_seconds,
                }
            ]
        if payload.kind == "ask":
            return [
                {
                    "type": "ask_user_request",
                    "question_id": req.question_id,
                    "questions": [q.model_dump() for q in payload.questions],
                    "timeout_seconds": req.timeout_seconds,
                }
            ]
        return []

    if isinstance(evt, HitlAnswerEvent):
        if isinstance(evt.answer, dict):
            return [
                {
                    "type": "ask_user_resolved",
                    "question_id": evt.question_id,
                    "answers": evt.answer,
                    "cancelled": evt.cancelled,
                    "timed_out": evt.timed_out,
                }
            ]
        resolved: dict[str, Any] = {
            "type": "sandbox_confirm_resolved",
            "question_id": evt.question_id,
            "cancelled": evt.cancelled,
            "timed_out": evt.timed_out,
        }
        if isinstance(evt.answer, ApproveAnswer):
            resolved["decision"] = evt.answer.decision
            resolved["reason"] = evt.answer.reason
        return [resolved]

    return []


def convert_event_to_sse(evt: StreamEvent) -> list[dict[str, Any]]:
    """One-off translation of a single StreamEvent.

    Allocates a fresh :class:`StreamConverter`, so deferred unwrap works only
    when the event already carries the complete information (e.g. a
    ``toolcall_end`` with a parsed wrapper dict). For progressive delta
    unwrap across multiple events, hold a long-lived ``StreamConverter``.
    """
    return StreamConverter().convert(evt)


def convert_agent_event_to_sse(evt: AgentEvent) -> list[dict[str, Any]]:
    """One-off translation of a single AgentEvent. See :func:`convert_event_to_sse`."""
    return StreamConverter().convert_agent_event(evt)


def unwrap_deferred_in_message_dicts(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Rewrite persisted ``deferred_tool_call`` wrapper blocks to their resolved
    real tool form, for read-side display.

    cubepi's ``resolve_tool_call`` rewrites the dispatched call at execute time
    but does NOT mutate the persisted AssistantMessage — the dispatcher block
    stays in checkpoint history as ``name="deferred_tool_call"`` with the
    wrapper arguments. The tool_result, by contrast, gets persisted under the
    resolved name (cubepi emits ``ToolExecutionEndEvent`` with the rewritten
    ``rtc.name``), so without this read-side fix a reloaded conversation
    renders mismatched cards (``deferred_tool_call`` request → real-name
    result) and loses any frontend rendering keyed off real tool names.

    We touch only the dict copy — the underlying cubepi messages remain
    intact, so model replay still sees the dispatcher block (whichever name
    is in the prompt cache stays in the prompt cache). Malformed wrappers
    (inner ``tool_name`` not a string) pass through unchanged so cubepi's
    "Unknown deferred tool" error chain stays visible in the UI.
    """
    out: list[dict[str, Any]] = []
    for msg in messages:
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            out.append(msg)
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            out.append(msg)
            continue
        new_content: list[Any] = []
        rewrote = False
        for block in content:
            unwrapped = _unwrap_deferred_block(block)
            if unwrapped is not block:
                rewrote = True
            new_content.append(unwrapped)
        if not rewrote:
            out.append(msg)
            continue
        new_msg = dict(msg)
        new_msg["content"] = new_content
        out.append(new_msg)
    return out


def _unwrap_deferred_block(block: Any) -> Any:
    if (
        not isinstance(block, dict)
        or block.get("type") != "tool_call"
        or block.get("name") != _DEFERRED_DISPATCH_TOOL_NAME
    ):
        return block
    resolved = _resolve_deferred_wrapper(block.get("arguments"))
    if resolved is None:
        return block
    inner_name, inner_args = resolved
    new_block = dict(block)
    new_block["name"] = inner_name
    new_block["arguments"] = inner_args
    return new_block


def _resolve_deferred_wrapper(wrapper: Any) -> tuple[str, dict[str, Any]] | None:
    """Resolve the inner (real_name, args) target from a dispatcher wrapper.

    Mirrors ``cubepi.deferred.middleware.DeferredToolsMiddleware.resolve_tool_call``:
    ``tool_name`` must be a string; ``arguments`` must be a dict OR ``None``
    (the latter is the explicit no-arg path cubepi coerces to ``{}``). Any
    other non-dict ``arguments`` value makes cubepi's resolver return
    ``None`` so the dispatcher's own ``_execute`` runs and produces an
    is_error AgentToolResult — UI rewrites must defer to the same fate so
    the user sees the dispatcher error against the dispatcher name, not a
    real-name card with empty args masking the failure.
    """
    if not isinstance(wrapper, dict):
        return None
    name = wrapper.get("tool_name")
    if not isinstance(name, str):
        return None
    args = wrapper.get("arguments")
    if args is None:
        args = {}
    elif not isinstance(args, dict):
        return None
    return name, args
