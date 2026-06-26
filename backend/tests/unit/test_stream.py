"""stream tests — cubepi StreamEvent → cubebox SSE (M1.3)."""

import json

from cubepi import AgentToolResult
from cubepi.agent.types import (
    HitlAnswerEvent,
    HitlRequestEvent,
    MessageEndEvent,
    ToolExecutionEndEvent,
)
from cubepi.hitl.types import ApproveAnswer, ApproveRequest, HitlRequest
from cubepi.providers.base import (
    AssistantMessage,
    StreamEvent,
    TextContent,
    ToolCall,
    Usage,
    UserMessage,
)

from cubebox.agents.stream import convert_agent_event_to_sse, convert_event_to_sse


def _mk_assistant(text: str = "", tool_calls: list[ToolCall] | None = None) -> AssistantMessage:
    content: list = []
    if text:
        content.append(TextContent(text=text))
    if tool_calls:
        content.extend(tool_calls)
    return AssistantMessage(content=content, usage=Usage())


def test_text_delta_translates_to_text_delta() -> None:
    evt = StreamEvent(type="text_delta", delta="hello", partial=_mk_assistant("hello"))
    out = convert_event_to_sse(evt)
    assert out == [{"type": "text_delta", "delta": "hello"}]


def test_thinking_delta_translates_to_reasoning() -> None:
    evt = StreamEvent(type="thinking_delta", delta="thinking...", partial=_mk_assistant())
    out = convert_event_to_sse(evt)
    assert out == [{"type": "reasoning", "delta": "thinking..."}]


def test_toolcall_end_emits_tool_call() -> None:
    """toolcall_end → fully-formed tool_call (id+name+arguments)."""
    tc = ToolCall(id="tc1", name="search", arguments={"q": "x"})
    partial = _mk_assistant(tool_calls=[tc])
    evt = StreamEvent(type="toolcall_end", content_index=0, partial=partial)
    out = convert_event_to_sse(evt)
    assert len(out) == 1
    assert out[0]["type"] == "tool_call"
    assert out[0]["id"] == "tc1"
    assert out[0]["name"] == "search"
    assert out[0]["arguments"] == {"q": "x"}


def test_toolcall_end_missing_partial_drops() -> None:
    """Defensive: toolcall_end without partial → empty list."""
    evt = StreamEvent(type="toolcall_end", content_index=0)
    out = convert_event_to_sse(evt)
    assert out == []


def test_toolcall_delta_emits_tool_call_delta() -> None:
    evt = StreamEvent(
        type="toolcall_delta",
        delta='{"q": "x"',
        partial=_mk_assistant(tool_calls=[ToolCall(id="tc1", name="search", arguments={})]),
        content_index=0,
    )
    out = convert_event_to_sse(evt)
    assert out[0]["type"] == "tool_call_delta"
    assert out[0]["delta"] == '{"q": "x"'
    # identity carried so the live SSE path can route the chunk to its card
    assert out[0]["index"] == 0
    assert out[0]["id"] == "tc1"
    assert out[0]["name"] == "search"


def test_live_chain_toolcall_delta_reaches_frontend_shape() -> None:
    """End-to-end live seam: a streamed ``toolcall_delta`` must survive both
    translation hops (``convert_agent_event_to_sse`` then
    ``cubepi_dict_to_agent_event``) and arrive as a ``ToolCallDeltaEvent`` in
    the exact shape the frontend reducer consumes. This is the regression that
    broke during the langgraph→cubepi migration: the live drainer dropped
    tool_call_delta, so file_write / subagent previews only appeared at
    toolcall_end instead of streaming."""
    from cubepi.agent.types import MessageUpdateEvent

    from cubebox.agents.schemas import ToolCallDeltaEvent
    from cubebox.streams.run_manager import cubepi_dict_to_agent_event

    partial = _mk_assistant(tool_calls=[ToolCall(id="tc1", name="file_write", arguments={})])
    stream_evt = StreamEvent(
        type="toolcall_delta",
        delta='{"path": "a.txt"',
        partial=partial,
        content_index=0,
    )
    dicts = convert_agent_event_to_sse(MessageUpdateEvent(message=partial, stream_event=stream_evt))
    assert len(dicts) == 1

    evt = cubepi_dict_to_agent_event(dicts[0], "2026-05-27T00:00:00+00:00")
    assert isinstance(evt, ToolCallDeltaEvent)
    assert evt.data == {
        "tool_call_id": "tc1",
        "name": "file_write",
        "args_delta": '{"path": "a.txt"',
        "index": 0,
    }


def test_done_translates_to_done() -> None:
    evt = StreamEvent(type="done")
    out = convert_event_to_sse(evt)
    assert out == [{"type": "done"}]


def test_error_translates_to_error() -> None:
    evt = StreamEvent(type="error", error_message="boom")
    out = convert_event_to_sse(evt)
    assert out == [{"type": "error", "error": "boom"}]


def test_error_with_missing_message_has_fallback() -> None:
    evt = StreamEvent(type="error")
    out = convert_event_to_sse(evt)
    assert out == [{"type": "error", "error": "unknown error"}]


def test_silent_events_are_dropped() -> None:
    """Events with no cubebox SSE equivalent return empty list."""
    for t in [
        "text_start",
        "text_end",
        "thinking_start",
        "thinking_end",
        "toolcall_start",
        "start",
    ]:
        evt = StreamEvent(type=t)
        out = convert_event_to_sse(evt)
        assert out == [], f"event type {t!r} should be silently dropped, got {out!r}"


# ---------------------------------------------------------------------------
# convert_agent_event_to_sse — MessageEndEvent → usage


def test_message_end_with_usage_emits_usage_event() -> None:
    """MessageEndEvent carrying AssistantMessage with usage → usage SSE dict."""
    msg = AssistantMessage(
        content=[],
        usage=Usage(
            input_tokens=10,
            output_tokens=5,
            cache_read_tokens=3,
            cache_write_tokens=2,
        ),
    )
    evt = MessageEndEvent(message=msg)
    out = convert_agent_event_to_sse(evt)
    assert len(out) == 1
    assert out[0] == {
        "type": "usage",
        "input_tokens": 10,
        "output_tokens": 5,
        "cache_read_tokens": 3,
        "cache_write_tokens": 2,
    }


def test_message_end_with_zero_input_tokens_is_dropped() -> None:
    """MessageEndEvent with input_tokens=0 produces no usage event (intermediate chunk)."""
    msg = AssistantMessage(
        content=[],
        usage=Usage(input_tokens=0, output_tokens=0),
    )
    evt = MessageEndEvent(message=msg)
    out = convert_agent_event_to_sse(evt)
    assert out == []


def test_message_end_with_none_usage_is_dropped() -> None:
    """MessageEndEvent with usage=None produces no usage event."""
    msg = AssistantMessage(content=[], usage=None)
    evt = MessageEndEvent(message=msg)
    out = convert_agent_event_to_sse(evt)
    assert out == []


# ---------------------------------------------------------------------------
# convert_agent_event_to_sse — ToolExecutionEndEvent → tool_result
#
# Regression: cubepi's ToolExecutionEndEvent.result is an ``AgentToolResult``
# Pydantic model. The previous implementation forwarded the model object as
# the SSE dict's ``result`` field; downstream ``str()`` produced a Pydantic
# repr like ``content=[TextContent(text='{"foo":1}')] details=None ...``
# which broke frontend JSON.parse and surfaced ``save_artifact`` as a regular
# tool call card instead of an artifact card during live runs.


def test_tool_result_extracts_text_from_agent_tool_result() -> None:
    """AgentToolResult.content TextContent → string in SSE ``result`` field."""
    payload = AgentToolResult(content=[TextContent(text='{"action":"created"}')])
    evt = ToolExecutionEndEvent(tool_call_id="tc-a", tool_name="save_artifact", result=payload)
    out = convert_agent_event_to_sse(evt)
    assert len(out) == 1
    d = out[0]
    assert d["type"] == "tool_result"
    assert d["tool_call_id"] == "tc-a"
    assert d["name"] == "save_artifact"
    assert d["result"] == '{"action":"created"}'
    assert d["is_error"] is False


def test_save_artifact_emits_artifact_event_after_tool_result() -> None:
    """save_artifact result carrying an artifact → tool_result + artifact event.

    Regression: the artifact event was never emitted live, so the frontend
    store stayed empty until a page reload triggered loadArtifacts.
    """
    artifact = {"id": "art_1", "conversation_id": "conv_1", "name": "x", "version": 1}
    payload = AgentToolResult(
        content=[TextContent(text=json.dumps({"action": "created", "artifact": artifact}))]
    )
    evt = ToolExecutionEndEvent(tool_call_id="tc-art", tool_name="save_artifact", result=payload)
    out = convert_agent_event_to_sse(evt)
    assert [d["type"] for d in out] == ["tool_result", "artifact"]
    art_evt = out[1]
    assert art_evt["action"] == "created"
    assert art_evt["artifact"] == artifact


def test_save_artifact_error_does_not_emit_artifact_event() -> None:
    """An errored save_artifact result must not produce an artifact event."""
    payload = AgentToolResult(content=[TextContent(text='{"error": "Path not found"}')])
    evt = ToolExecutionEndEvent(
        tool_call_id="tc-err", tool_name="save_artifact", result=payload, is_error=True
    )
    out = convert_agent_event_to_sse(evt)
    assert [d["type"] for d in out] == ["tool_result"]


def test_non_artifact_tool_result_emits_only_tool_result() -> None:
    """A non-save_artifact tool never produces an artifact event."""
    payload = AgentToolResult(content=[TextContent(text='{"artifact": {"id": "x"}}')])
    evt = ToolExecutionEndEvent(tool_call_id="tc-o", tool_name="echo", result=payload)
    out = convert_agent_event_to_sse(evt)
    assert [d["type"] for d in out] == ["tool_result"]


def test_save_artifact_non_json_result_emits_only_tool_result() -> None:
    """Defensive: unparseable save_artifact result → no artifact event, no raise."""
    payload = AgentToolResult(content=[TextContent(text="not json")])
    evt = ToolExecutionEndEvent(tool_call_id="tc-nj", tool_name="save_artifact", result=payload)
    out = convert_agent_event_to_sse(evt)
    assert [d["type"] for d in out] == ["tool_result"]


def test_tool_result_concatenates_multiple_text_blocks() -> None:
    """Multiple TextContent blocks concatenate; non-text blocks are dropped."""
    payload = AgentToolResult(content=[TextContent(text="part-1 "), TextContent(text="part-2")])
    evt = ToolExecutionEndEvent(tool_call_id="tc-b", tool_name="echo", result=payload)
    out = convert_agent_event_to_sse(evt)
    assert out[0]["result"] == "part-1 part-2"


def test_tool_result_propagates_details() -> None:
    """AgentToolResult.details survives to the SSE dict, so frontend gets
    ``details.subagent_events`` live (matching the reload-from-DB shape)."""
    payload = AgentToolResult(
        content=[TextContent(text="inner final")],
        details={"subagent_events": [{"type": "text_delta", "delta": "hi"}]},
    )
    evt = ToolExecutionEndEvent(tool_call_id="tc-c", tool_name="subagent", result=payload)
    out = convert_agent_event_to_sse(evt)
    assert out[0]["details"] == {"subagent_events": [{"type": "text_delta", "delta": "hi"}]}


def test_tool_result_handles_plain_string_result() -> None:
    """If a producer ever hands us a plain string instead of AgentToolResult,
    pass it through unchanged."""
    evt = ToolExecutionEndEvent(tool_call_id="tc-d", tool_name="raw", result="plain text")
    out = convert_agent_event_to_sse(evt)
    assert out[0]["result"] == "plain text"
    assert out[0]["details"] is None


def test_tool_result_handles_none_result() -> None:
    """None result → empty string + no details, no exception."""
    evt = ToolExecutionEndEvent(tool_call_id="tc-e", tool_name="silent", result=None)
    out = convert_agent_event_to_sse(evt)
    assert out[0]["result"] == ""
    assert out[0]["details"] is None


def test_tool_result_preserves_is_error_flag() -> None:
    payload = AgentToolResult(content=[TextContent(text="boom")])
    evt = ToolExecutionEndEvent(
        tool_call_id="tc-f", tool_name="fail", result=payload, is_error=True
    )
    out = convert_agent_event_to_sse(evt)
    assert out[0]["is_error"] is True
    assert out[0]["result"] == "boom"


def test_tool_result_prefers_details_original_content_for_sse() -> None:
    """CitationMiddleware rewrites .content to 【N-M】 marker text for the LLM and
    stashes the pre-rewrite JSON in details["original_content"]. The SSE path
    must surface the original so frontend previews (e.g. SearchResultView)
    receive parseable JSON instead of the marker text."""
    payload = AgentToolResult(
        content=[TextContent(text="【1-0】 [url: http://x] chunk")],
        details={
            "citations": [{"citation_id": 1}],
            "original_content": '{"query":"q","results":[{"url":"http://x","title":"X"}]}',
        },
    )
    evt = ToolExecutionEndEvent(tool_call_id="tc-g", tool_name="web_search", result=payload)
    out = convert_agent_event_to_sse(evt)
    assert out[0]["result"] == '{"query":"q","results":[{"url":"http://x","title":"X"}]}'
    assert out[0]["details"]["citations"] == [{"citation_id": 1}]


# ---------------------------------------------------------------------------
# convert_agent_event_to_sse — injected steer UserMessage → injected_message


def test_injected_user_message_becomes_injected_message_dict() -> None:
    msg = UserMessage(content=[TextContent(text="do X instead")], metadata={"steer_id": "s1"})
    out = convert_agent_event_to_sse(MessageEndEvent(message=msg))
    assert out == [{"type": "injected_message", "content": "do X instead", "steer_id": "s1"}]


def test_injected_user_message_without_steer_id_is_dropped() -> None:
    msg = UserMessage(content=[TextContent(text="seed prompt")])
    assert convert_agent_event_to_sse(MessageEndEvent(message=msg)) == []


def test_injected_group_chat_message_carries_sender_identity() -> None:
    # Group-chat steers persist sender_user_id/sender_display_name in metadata;
    # the SSE event must forward them so live viewers render the SenderBadge.
    msg = UserMessage(
        content=[TextContent(text="hi team")],
        metadata={
            "steer_id": "s1",
            "sender_user_id": "user_abc",
            "sender_display_name": "Alice",
        },
    )
    out = convert_agent_event_to_sse(MessageEndEvent(message=msg))
    assert out == [
        {
            "type": "injected_message",
            "content": "hi team",
            "steer_id": "s1",
            "sender_user_id": "user_abc",
            "sender_display_name": "Alice",
        }
    ]


def test_injected_message_omits_sender_identity_when_absent() -> None:
    # 1:1 chats persist no sender fields; the event stays minimal.
    msg = UserMessage(content=[TextContent(text="solo steer")], metadata={"steer_id": "s2"})
    out = convert_agent_event_to_sse(MessageEndEvent(message=msg))
    assert out == [{"type": "injected_message", "content": "solo steer", "steer_id": "s2"}]


# ---------------------------------------------------------------------------
# convert_agent_event_to_sse — HitlRequestEvent → sandbox_confirm_request


def _mk_hitl_request(
    question_id: str = "q1",
    tool_call_id: str = "tc1",
    tool_name: str = "execute",
    args: dict | None = None,
    details: dict | None = None,
    timeout_seconds: float | None = 180.0,
) -> HitlRequest:
    payload = ApproveRequest(
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        args=args or {"command": "rm -rf /"},
        details=details or {"matched_pattern": "rm *"},
    )
    return HitlRequest(
        question_id=question_id,
        thread_id="thread-1",
        payload=payload,
        created_at=1700000000.0,
        timeout_seconds=timeout_seconds,
    )


def test_hitl_request_event_emits_sandbox_confirm_request() -> None:
    req = _mk_hitl_request()
    evt = HitlRequestEvent(request=req)
    out = convert_agent_event_to_sse(evt)
    assert len(out) == 1
    d = out[0]
    assert d["type"] == "sandbox_confirm_request"
    assert d["question_id"] == "q1"
    assert d["tool_call_id"] == "tc1"
    assert d["tool_name"] == "execute"
    assert d["args"] == {"command": "rm -rf /"}
    assert d["details"] == {"matched_pattern": "rm *"}
    assert d["timeout_seconds"] == 180.0


def test_hitl_request_non_approve_kind_is_dropped() -> None:
    # ConfirmRequest / AskRequest — not sandbox approve; drop silently
    from cubepi.hitl.types import ConfirmRequest

    req = HitlRequest(
        question_id="q2",
        thread_id=None,
        payload=ConfirmRequest(prompt="Are you sure?"),
        created_at=1700000000.0,
    )
    evt = HitlRequestEvent(request=req)
    assert convert_agent_event_to_sse(evt) == []


# ---------------------------------------------------------------------------
# convert_agent_event_to_sse — HitlAnswerEvent → sandbox_confirm_resolved


def test_hitl_answer_approved_emits_sandbox_confirm_resolved() -> None:
    answer = ApproveAnswer(decision="approve", reason="looks fine")
    evt = HitlAnswerEvent(question_id="q1", answer=answer, cancelled=False, timed_out=False)
    out = convert_agent_event_to_sse(evt)
    assert len(out) == 1
    d = out[0]
    assert d["type"] == "sandbox_confirm_resolved"
    assert d["question_id"] == "q1"
    assert d["cancelled"] is False
    assert d["timed_out"] is False
    assert d["decision"] == "approve"
    assert d["reason"] == "looks fine"


def test_hitl_answer_cancelled_emits_sandbox_confirm_resolved_no_decision() -> None:
    evt = HitlAnswerEvent(question_id="q1", answer=None, cancelled=True, timed_out=False)
    out = convert_agent_event_to_sse(evt)
    assert len(out) == 1
    d = out[0]
    assert d["type"] == "sandbox_confirm_resolved"
    assert d["cancelled"] is True
    assert "decision" not in d


def test_hitl_answer_timed_out_emits_sandbox_confirm_resolved_no_decision() -> None:
    evt = HitlAnswerEvent(question_id="q1", answer=None, cancelled=False, timed_out=True)
    out = convert_agent_event_to_sse(evt)
    assert len(out) == 1
    d = out[0]
    assert d["timed_out"] is True
    assert "decision" not in d
