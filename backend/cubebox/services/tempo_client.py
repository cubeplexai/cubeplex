"""Tempo HTTP query client + OTLP→view-model parser.

This module is the single point where cubepi span attribute names are
translated into the API contract (cubebox.api.schemas.trace). Update both
in lockstep when cubepi semantic conventions change.
"""

from __future__ import annotations

import json as _json
import re
from datetime import UTC, datetime
from typing import Any

import httpx

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
    return ToolCallPayload(
        name=str(attrs.get("gen_ai.tool.name") or "?"),
        description=attrs.get("gen_ai.tool.description"),
        arguments=attrs.get("gen_ai.tool.call.arguments"),
        result=attrs.get("gen_ai.tool.call.result"),
        is_error=bool(attrs.get("cubepi.tool.is_error", False)),
        execution_mode=attrs.get("cubepi.tool.execution_mode"),
        tool_call_id=attrs.get("gen_ai.tool.call.id"),
    )


# ---------------------------------------------------------------------------
# Tempo HTTP client
# ---------------------------------------------------------------------------


class TempoQueryError(RuntimeError):
    """Raised when Tempo returns a non-2xx response."""


class TempoQueryValueError(ValueError):
    """Raised when a filter value would break TraceQL string escaping."""


_TRACEQL_FORBIDDEN = ('"', "\\", "\n", "\r", "\x00")
_TRACE_ID_RE = re.compile(r"^[a-fA-F0-9]{1,64}$")


def _quote_traceql(value: str) -> str:
    """Wrap a value in `"..."` for inclusion in a TraceQL clause.

    Rejects values containing characters that could break out of the
    surrounding double-quote pair. We reject rather than backslash-escape
    because cubebox business identifiers (workspace_id, user_id, conv_id,
    run_id, model) are well-formed slugs; any value outside that shape is
    either user error or an injection attempt.
    """
    for c in _TRACEQL_FORBIDDEN:
        if c in value:
            raise TempoQueryValueError(f"Filter value contains disallowed character {c!r}")
    return f'"{value}"'


class TempoClient:
    """Thin async wrapper around Tempo's HTTP query API.

    All search() calls inject the caller's org_id into the TraceQL so a
    misconstructed UI never sees another org's traces. Every interpolated
    value is escape-checked via _quote_traceql.
    """

    def __init__(self, endpoint: str, timeout_seconds: int = 10) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._timeout = timeout_seconds

    async def search(
        self,
        *,
        org_id: str,
        workspace_id: str | None = None,
        user_id: str | None = None,
        conversation_id: str | None = None,
        run_id: str | None = None,
        model: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        min_duration_ms: int | None = None,
        max_duration_ms: int | None = None,
        limit: int = 20,
    ) -> list[TraceSummary]:
        # cubepi.metadata.org_id lives on invoke_agent spans; gen_ai.request.model
        # lives on chat spans. A single {…} selector requires both on the same span,
        # which would miss cross-span matches. Use sibling spansets joined at the top
        # level so each selector matches independently within the same trace.
        metadata_clauses = [
            'resource.service.name="cubebox"',
            f"span.cubepi.metadata.org_id={_quote_traceql(org_id)}",
        ]
        if workspace_id:
            metadata_clauses.append(
                f"span.cubepi.metadata.workspace_id={_quote_traceql(workspace_id)}"
            )
        if user_id:
            metadata_clauses.append(f"span.cubepi.metadata.user_id={_quote_traceql(user_id)}")
        if conversation_id:
            metadata_clauses.append(
                f"span.cubepi.metadata.conversation_id={_quote_traceql(conversation_id)}"
            )
        if run_id:
            metadata_clauses.append(f"span.cubepi.run_id={_quote_traceql(run_id)}")
        if min_duration_ms is not None:
            metadata_clauses.append(f"trace:duration > {int(min_duration_ms)}ms")
        if max_duration_ms is not None:
            metadata_clauses.append(f"trace:duration < {int(max_duration_ms)}ms")

        model_clauses: list[str] = []
        if model:
            model_clauses.append(f"span.gen_ai.request.model={_quote_traceql(model)}")

        q = "{ " + " && ".join(metadata_clauses) + " }"
        if model_clauses:
            q += " && { " + " && ".join(model_clauses) + " }"
        q += (
            " | select("
            "span.cubepi.metadata.workspace_id, "
            "span.cubepi.metadata.user_id, "
            "span.cubepi.metadata.conversation_id, "
            "span.cubepi.run_id, "
            "span.gen_ai.request.model"
            ")"
        )

        params: dict[str, Any] = {"q": q, "limit": str(limit)}
        if start:
            params["start"] = str(int(start.timestamp()))
        if end:
            params["end"] = str(int(end.timestamp()))

        async with httpx.AsyncClient(timeout=self._timeout) as http:
            try:
                resp = await http.get(f"{self._endpoint}/api/search", params=params)
            except httpx.HTTPError as exc:
                raise TempoQueryError("Tempo /api/search request failed") from exc
        if resp.status_code >= 400:
            raise TempoQueryError(f"Tempo /api/search returned {resp.status_code}")
        try:
            payload = resp.json()
            hits = payload.get("traces") or []
            return [_search_hit_to_summary(t) for t in hits]
        except (ValueError, KeyError, TypeError) as exc:
            raise TempoQueryError("Tempo /api/search returned malformed payload") from exc

    async def get_trace(self, trace_id: str) -> TraceDetail:
        if not _TRACE_ID_RE.match(trace_id):
            raise TempoQueryValueError(f"Invalid trace id: {trace_id!r}")
        async with httpx.AsyncClient(timeout=self._timeout) as http:
            try:
                resp = await http.get(f"{self._endpoint}/api/traces/{trace_id}")
            except httpx.HTTPError as exc:
                raise TempoQueryError(f"Tempo /api/traces/{trace_id} request failed") from exc
        if resp.status_code == 404:
            raise TempoQueryError(f"Trace {trace_id} not found")
        if resp.status_code >= 400:
            raise TempoQueryError(f"Tempo /api/traces/{trace_id} returned {resp.status_code}")
        try:
            return parse_trace_detail(resp.json())
        except (ValueError, KeyError, TypeError) as exc:
            raise TempoQueryError(
                f"Tempo /api/traces/{trace_id} returned malformed payload"
            ) from exc

    async def tag_values(self, *, tag: str, org_id: str) -> list[str]:
        # Tempo v2 scoped tag-values endpoint. v1 ignores `q=` (verified against
        # 2.8.2), which would leak workspace/user/conversation/model identifiers
        # across orgs via autocomplete. v2 honors the org-scoping TraceQL.
        #
        # Tempo v2 takes the FULL prefixed tag name (e.g. `span.cubepi.run_id`)
        # as a single path segment — NOT `span/<tag>` as two segments. All tags
        # in _ALLOWED_TAGS live on spans, so we prepend `span.` here.
        params: dict[str, Any] = {
            "q": '{ resource.service.name="cubebox" '
            f"&& span.cubepi.metadata.org_id={_quote_traceql(org_id)} }}",
        }
        url = f"{self._endpoint}/api/v2/search/tag/span.{tag}/values"
        async with httpx.AsyncClient(timeout=self._timeout) as http:
            try:
                resp = await http.get(url, params=params)
            except httpx.HTTPError as exc:
                raise TempoQueryError("Tempo tag values request failed") from exc
        if resp.status_code >= 400:
            raise TempoQueryError(f"Tempo tag values returned {resp.status_code}")
        try:
            payload = resp.json()
            # v2 shape: {"tagValues": [{"type": "string", "value": "..."}], ...}
            return [
                str(item["value"])
                for item in (payload.get("tagValues") or [])
                if isinstance(item, dict) and "value" in item
            ]
        except (ValueError, KeyError, TypeError) as exc:
            raise TempoQueryError("Tempo tag values returned malformed payload") from exc


def _search_hit_to_summary(t: dict[str, Any]) -> TraceSummary:
    # Tempo surfaces select()-requested attrs on the matched spans inside
    # spanSets (plural, documented v2 shape). Some Tempo versions also emit
    # `spanSet` (singular legacy alias) — prefer the array, fall back to the alias.
    sets = t.get("spanSets") or []
    matched_set: dict[str, Any]
    if sets and isinstance(sets, list):
        matched_set = sets[0] if isinstance(sets[0], dict) else {}
    else:
        matched_set = t.get("spanSet") or {}
    spans = matched_set.get("spans") or []
    attrs_list = [
        {a["key"]: _attr_value(a) for a in (span.get("attributes") or [])} for span in spans
    ]

    def first(key: str) -> str | None:
        for attrs in attrs_list:
            v = attrs.get(key)
            if v not in (None, ""):
                return str(v)
        return None

    return TraceSummary(
        trace_id=t["traceID"],
        root_name=t.get("rootTraceName", ""),
        start_time=_ns_to_dt(t.get("startTimeUnixNano", "0")),
        duration_ms=int(t.get("durationMs", 0)),
        span_count=int(matched_set.get("matched") or 0),
        workspace_id=first("cubepi.metadata.workspace_id"),
        user_id=first("cubepi.metadata.user_id"),
        conversation_id=first("cubepi.metadata.conversation_id"),
        run_id=first("cubepi.run_id"),
        model=first("gen_ai.request.model"),
    )


def get_tempo_client() -> TempoClient | None:
    """FastAPI dependency. Returns None when the admin trace viewer is disabled."""
    from cubebox.config import config

    endpoint = config.get("tracing.tempo.query_endpoint", None)
    if not endpoint:
        return None
    timeout = int(config.get("tracing.tempo.timeout_seconds", 10) or 10)
    return TempoClient(endpoint=str(endpoint), timeout_seconds=timeout)
