"""Shape contract for admin trace API responses."""

from cubebox.api.schemas.trace import (
    LlmCallPayload,
    SpanKind,
    SpanNode,
    TokenUsage,
    TraceDetail,
    TraceSummary,
)


def test_trace_summary_required_fields() -> None:
    summary = TraceSummary(
        trace_id="abc",
        root_name="invoke_agent",
        start_time="2026-06-11T10:00:00+00:00",
        duration_ms=1234,
        span_count=5,
        org_id="org-1",
        workspace_id="ws-1",
        user_id="usr-1",
        conversation_id="conv-1",
        run_id="run-1",
        model="deepseek-v4-flash",
    )
    assert summary.trace_id == "abc"


def test_span_kind_discriminator() -> None:
    node = SpanNode(
        span_id="s1",
        parent_span_id=None,
        name="chat deepseek-v4-flash",
        kind=SpanKind.CHAT,
        start_time="2026-06-11T10:00:00+00:00",
        duration_ms=200,
        children=[],
        llm=LlmCallPayload(
            model="deepseek-v4-flash",
            provider="deepseek-anthropic-shape",
            tokens=TokenUsage(input=100, output=50, cache_read=20),
            messages=[],
            tools=[],
            raw_request='{"model":"..."}',
            raw_response='{"id":"..."}',
        ),
    )
    assert node.kind == SpanKind.CHAT
    assert node.llm and node.llm.tokens.input == 100


def test_trace_detail_carries_tree() -> None:
    detail = TraceDetail(
        summary=TraceSummary(
            trace_id="abc",
            root_name="invoke_agent",
            start_time="2026-06-11T10:00:00+00:00",
            duration_ms=1234,
            span_count=1,
            org_id="org-1",
            workspace_id="ws-1",
            user_id="usr-1",
            conversation_id="conv-1",
            run_id="run-1",
            model=None,
        ),
        root=SpanNode(
            span_id="s1",
            parent_span_id=None,
            name="invoke_agent",
            kind="agent",
            start_time="2026-06-11T10:00:00+00:00",
            duration_ms=1234,
            children=[],
        ),
    )
    assert detail.root.kind == "agent"
