"""Tempo HTTP query client + OTLP→view-model parser.

This module is the single point where cubepi span attribute names are
translated into the API contract (cubebox.api.schemas.trace). Update both
in lockstep when cubepi semantic conventions change.
"""

from __future__ import annotations

import json as _json
from datetime import UTC, datetime
from typing import Any

from cubebox.api.schemas.trace import (
    ChatMessage,
    LlmCallPayload,
    SpanKind,
    SpanNode,
    TokenUsage,
    ToolCallPayload,
    ToolDefinition,
    TraceDetail,
    TraceSummary,
    TurnPayload,
)


def _attr_value(attr: dict[str, Any]) -> Any:
    """OTLP attribute values are {"stringValue": ...} / {"intValue": ...} etc."""
    v = attr.get("value") or {}
    for k in ("stringValue", "intValue", "doubleValue", "boolValue"):
        if k in v:
            return v[k]
    if "arrayValue" in v:
        values = v["arrayValue"].get("values", [])
        return [list(x.values())[0] if x else None for x in values]
    return None


def _attrs_to_dict(attrs: list[dict[str, Any]]) -> dict[str, Any]:
    return {a["key"]: _attr_value(a) for a in attrs}


def _classify(name: str, attrs: dict[str, Any]) -> SpanKind:
    # Stable signal: gen_ai.operation.name (set explicitly by cubepi).
    # Fall back to span name only when the attribute is absent, so a cubepi
    # rename of the span string doesn't silently degrade all LLM spans.
    op = attrs.get("gen_ai.operation.name")
    if op == "invoke_agent":
        return SpanKind.AGENT
    if op == "chat":
        return SpanKind.CHAT
    if op == "execute_tool":
        return SpanKind.TOOL
    if name == "cubepi.turn":
        return SpanKind.TURN
    if name == "invoke_agent":
        return SpanKind.AGENT
    if name.startswith("chat "):
        return SpanKind.CHAT
    if name.startswith("execute_tool"):
        return SpanKind.TOOL
    return SpanKind.OTHER


def _ns_to_dt(ns: str | int) -> datetime:
    return datetime.fromtimestamp(int(ns) / 1_000_000_000, tz=UTC)


def _flatten_spans(payload: dict[str, Any]) -> list[dict[str, Any]]:
    spans: list[dict[str, Any]] = []
    for batch in payload.get("batches", []):
        for scope in batch.get("scopeSpans", []):
            spans.extend(scope.get("spans", []))
    return spans


def parse_trace_detail(payload: dict[str, Any]) -> TraceDetail:
    raw_spans = _flatten_spans(payload)
    if not raw_spans:
        raise ValueError("Trace contained no spans")

    nodes: dict[str, SpanNode] = {}
    children_map: dict[str, list[str]] = {}
    all_attrs: list[dict[str, Any]] = []
    chat_models: list[str] = []
    has_error = False

    for raw in raw_spans:
        attrs = _attrs_to_dict(raw.get("attributes", []))
        all_attrs.append(attrs)
        kind = _classify(raw["name"], attrs)
        if kind == SpanKind.CHAT:
            model = attrs.get("gen_ai.request.model") or attrs.get("gen_ai.response.model")
            if model:
                chat_models.append(str(model))
        start_ns = raw.get("startTimeUnixNano", "0")
        end_ns = raw.get("endTimeUnixNano", "0")
        duration_ms = max(0, (int(end_ns) - int(start_ns)) // 1_000_000)
        status = raw.get("status") or {}
        if status.get("code") == "STATUS_CODE_ERROR":
            has_error = True
        node = SpanNode(
            span_id=raw["spanId"],
            parent_span_id=raw.get("parentSpanId") or None,
            name=raw["name"],
            kind=kind,
            start_time=_ns_to_dt(start_ns),
            duration_ms=duration_ms,
            status_code=status.get("code"),
            status_message=status.get("message"),
            llm=_extract_llm(attrs) if kind == SpanKind.CHAT else None,
            tool=_extract_tool(attrs) if kind == SpanKind.TOOL else None,
            turn=_extract_turn(attrs) if kind == SpanKind.TURN else None,
            raw_attributes=attrs,
        )
        nodes[node.span_id] = node
        if node.parent_span_id and node.parent_span_id != node.span_id:
            children_map.setdefault(node.parent_span_id, []).append(node.span_id)

    for sid, child_ids in children_map.items():
        if sid in nodes:
            nodes[sid].children = sorted(
                [nodes[c] for c in child_ids if c in nodes],
                key=lambda n: n.start_time,
            )

    roots = [
        n
        for n in nodes.values()
        if not n.parent_span_id
        or n.parent_span_id == n.span_id  # self-cycle: treat as root
        or n.parent_span_id not in nodes
    ]
    if not roots:
        raise ValueError("Trace has no root span")
    root = next((r for r in roots if r.kind == SpanKind.AGENT), roots[0])

    metadata = _summary_metadata(all_attrs)
    trace_id = raw_spans[0].get("traceId", "")
    summary = TraceSummary(
        trace_id=trace_id,
        root_name=root.name,
        start_time=root.start_time,
        duration_ms=root.duration_ms,
        span_count=len(nodes),
        org_id=metadata.get("org_id"),
        workspace_id=metadata.get("workspace_id"),
        user_id=metadata.get("user_id"),
        conversation_id=metadata.get("conversation_id"),
        run_id=metadata.get("run_id"),
        model=chat_models[0] if chat_models else None,
        has_error=has_error,
    )
    return TraceDetail(summary=summary, root=root)


def _summary_metadata(all_attrs: list[dict[str, Any]]) -> dict[str, str]:
    """Scan every span for cubepi.metadata.* and cubepi.run_id.

    Picks the first non-empty value seen. Falls back across span kinds so a
    trace without an `invoke_agent` root still surfaces its identifiers.
    """
    keys = ("org_id", "workspace_id", "user_id", "conversation_id")
    out: dict[str, str] = {}
    for attrs in all_attrs:
        for k in keys:
            if k not in out:
                v = attrs.get(f"cubepi.metadata.{k}")
                if v not in (None, ""):
                    out[k] = str(v)
        if "run_id" not in out:
            v = attrs.get("cubepi.run_id")
            if v not in (None, ""):
                out["run_id"] = str(v)
    return out


def _extract_turn(attrs: dict[str, Any]) -> TurnPayload:
    return TurnPayload(
        index=int(attrs.get("cubepi.turn.index", 0) or 0),
        stop_reason=attrs.get("cubepi.turn.stop_reason"),
        tool_calls_count=int(attrs.get("cubepi.turn.tool_calls.count", 0) or 0),
    )


def _safe_int(v: Any) -> int | None:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _safe_float(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _decode_messages(raw: Any) -> list[ChatMessage]:
    if not raw:
        return []
    if isinstance(raw, str):
        try:
            data = _json.loads(raw)
        except _json.JSONDecodeError:
            return []
    else:
        data = raw
    if not isinstance(data, list):
        return []
    out: list[ChatMessage] = []
    for item in data:
        if isinstance(item, dict) and "role" in item:
            out.append(ChatMessage(role=str(item["role"]), parts=item.get("parts", []) or []))
    return out


def _decode_tools(raw: Any) -> list[ToolDefinition]:
    if not raw:
        return []
    if isinstance(raw, str):
        try:
            data = _json.loads(raw)
        except _json.JSONDecodeError:
            return []
    else:
        data = raw
    if not isinstance(data, list):
        return []
    out: list[ToolDefinition] = []
    for item in data:
        if isinstance(item, dict) and "name" in item:
            out.append(
                ToolDefinition(
                    name=str(item["name"]),
                    description=item.get("description"),
                    parameters=item.get("parameters") or item.get("input_schema"),
                )
            )
    return out


def _extract_llm(attrs: dict[str, Any]) -> LlmCallPayload:
    finish = attrs.get("gen_ai.response.finish_reasons")
    finish_list = finish if isinstance(finish, list) else ([finish] if finish else [])
    return LlmCallPayload(
        model=str(
            attrs.get("gen_ai.request.model") or attrs.get("gen_ai.response.model") or "unknown"
        ),
        provider=attrs.get("gen_ai.provider.name"),
        request_max_tokens=_safe_int(attrs.get("gen_ai.request.max_tokens")),
        request_temperature=_safe_float(attrs.get("gen_ai.request.temperature")),
        request_stream=attrs.get("gen_ai.request.stream"),
        tokens=TokenUsage(
            input=_safe_int(attrs.get("gen_ai.usage.input_tokens")) or 0,
            output=_safe_int(attrs.get("gen_ai.usage.output_tokens")) or 0,
            cache_read=_safe_int(attrs.get("gen_ai.usage.cache_read.input_tokens")) or 0,
            cache_write=_safe_int(attrs.get("gen_ai.usage.cache_creation.input_tokens")) or 0,
        ),
        finish_reasons=[str(f) for f in finish_list if f],
        time_to_first_chunk_seconds=_safe_float(attrs.get("gen_ai.response.time_to_first_chunk")),
        response_id=attrs.get("gen_ai.response.id"),
        system_instructions=_decode_messages(attrs.get("gen_ai.system_instructions")),
        messages=_decode_messages(attrs.get("gen_ai.input.messages")),
        output_messages=_decode_messages(attrs.get("gen_ai.output.messages")),
        tools=_decode_tools(
            attrs.get("gen_ai.tool.definitions")
            or attrs.get("gen_ai.request.tools")
            or attrs.get("cubepi.agent.tools")
        ),
        raw_request=attrs.get("cubepi.llm.raw_request"),
        raw_response=attrs.get("cubepi.llm.raw_response"),
    )


def _extract_tool(attrs: dict[str, Any]) -> ToolCallPayload:
    # Task 6 fills out the full payload.
    return ToolCallPayload(name=str(attrs.get("gen_ai.tool.name") or "?"))
