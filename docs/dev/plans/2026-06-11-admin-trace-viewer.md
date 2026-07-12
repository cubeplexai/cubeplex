# Admin Trace Viewer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Org admins can list and inspect their org's cubeplex agent traces (stored in Grafana Tempo) inside the admin console, with workspace/user/conversation filters and LLM-aware span detail rendering.

**Architecture:** Thin proxy in front of Tempo's HTTP query API. Backend has one service (`TempoClient`) that builds TraceQL + parses OTLP JSON into Pydantic models, and one router (`admin_traces.py`) that injects the session org's `org_id` predicate. No new database table — Tempo's TraceQL is the index. Frontend has one list page and one detail page under `app/admin/traces/`, both pure assemblies of small components.

**Tech Stack:** Python 3.13, FastAPI, httpx, Pydantic v2, pytest (backend). Next 15, React 19, Tailwind, `@cubeplex/core` api client, Lucide icons, Vitest (frontend). Grafana Tempo 2.8 over HTTP.

**Reference:** Spec at `docs/dev/specs/2026-06-11-admin-trace-viewer-design.md`. The visual reference (not code) is `~/cubetrace`'s `TraceDetail.tsx` and `TraceList.tsx`.

---

## File Structure

**Backend (new files):**

- `backend/cubeplex/api/schemas/trace.py` — Pydantic response models.
- `backend/cubeplex/services/tempo_client.py` — httpx client + OTLP→model parser. **This file is the only schema mapping.**
- `backend/cubeplex/api/routes/v1/admin_traces.py` — three routes.
- `backend/tests/fixtures/tempo/sample_search.json` — captured Tempo `/api/search` response.
- `backend/tests/fixtures/tempo/sample_trace_multi_turn.json` — captured Tempo `/api/traces/{id}` response with multi-turn agent + tool calls.
- `backend/tests/unit/test_tempo_parser.py` — pure-function parser tests.
- `backend/tests/unit/test_tempo_client.py` — httpx-mocked client tests.
- `backend/tests/e2e/test_admin_traces.py` — route-level E2E with a fake `TempoClient`.

**Backend (modified):**

- `backend/config.yaml` — add `tracing.tempo` block (commented stub).
- `backend/cubeplex/api/app.py:475-505` — register `admin_traces.router`.

**Frontend (new files):**

- `frontend/packages/web/lib/api/admin-traces.ts` — typed fetcher.
- `frontend/packages/web/app/admin/traces/page.tsx` — `TraceListPage`.
- `frontend/packages/web/app/admin/traces/[traceId]/page.tsx` — `TraceDetailPage`.
- `frontend/packages/web/components/admin/traces/TraceListTable.tsx`
- `frontend/packages/web/components/admin/traces/TraceFilterBar.tsx`
- `frontend/packages/web/components/admin/traces/SpanTree.tsx`
- `frontend/packages/web/components/admin/traces/SpanDetail.tsx`
- `frontend/packages/web/components/admin/traces/cards/LlmCard.tsx`
- `frontend/packages/web/components/admin/traces/cards/ToolCard.tsx`
- `frontend/packages/web/components/admin/traces/cards/JsonBlock.tsx`
- `frontend/packages/web/components/admin/traces/types.ts` — view-model types mirroring backend response.

**Frontend (modified):**

- `frontend/packages/web/components/admin/AdminSubNav.tsx:58-70` — add `/admin/traces` nav entry.
- `frontend/packages/web/messages/en.json` + `zh.json` — `adminNav.traces` and `adminTraces.*` strings.

---

## Task 1: Capture Tempo response fixtures

**Files:**
- Create: `backend/tests/fixtures/tempo/sample_search.json`
- Create: `backend/tests/fixtures/tempo/sample_trace_multi_turn.json`
- Create: `backend/tests/fixtures/tempo/sample_trace_oneshot.json`

The fixtures freeze real Tempo responses so parser tests don't need a live
Tempo. The local Tempo instance lives in the e2b infra stack at
`~/infra/e2b/repo/packages/local-dev`; start it (if not already up) with
`docker compose up -d tempo-init memcached tempo otel-collector grafana`.
Tempo's HTTP port is randomly mapped — find it with `docker ps | grep tempo`
and look for `0.0.0.0:NNNN->3200/tcp`.

- [ ] **Step 1: Find Tempo's host port**

```bash
docker ps --format '{{.Names}}\t{{.Ports}}' | grep tempo
```
Note the `0.0.0.0:NNNN->3200/tcp` mapping; export it: `TEMPO=http://localhost:NNNN`.

- [ ] **Step 2: Capture a search response (org-tagged traces in last 7 days)**

```bash
END=$(date -u +%s); START=$((END - 7*86400))
mkdir -p backend/tests/fixtures/tempo
curl -sS "$TEMPO/api/search?q=%7B%20resource.service.name%3D%22cubeplex%22%20%26%26%20span.cubepi.metadata.conversation_id%21%3D%22%22%20%7D&limit=5&start=${START}&end=${END}" \
  | python3 -m json.tool > backend/tests/fixtures/tempo/sample_search.json
```

Verify: file contains `"traces":` and at least one trace with `"rootServiceName": "cubeplex"`.

- [ ] **Step 3: Capture a multi-turn trace (≥3 turns, ≥1 tool call)**

```bash
TID=$(python3 -c "import json; d=json.load(open('backend/tests/fixtures/tempo/sample_search.json')); print(next(t['traceID'] for t in d['traces'] if t.get('durationMs',0) > 30000))")
curl -sS "$TEMPO/api/traces/$TID" | python3 -m json.tool \
  > backend/tests/fixtures/tempo/sample_trace_multi_turn.json
```

Verify: file has `batches` → `scopeSpans` → `spans` with names `invoke_agent`, `cubepi.turn`, `chat <model>`, `execute_tool execute`.

- [ ] **Step 4: Capture a one-shot trace (single chat span, no tools)**

```bash
TID=$(python3 -c "import json; d=json.load(open('backend/tests/fixtures/tempo/sample_search.json')); print(next(t['traceID'] for t in d['traces'] if t.get('durationMs',0) < 10000))")
curl -sS "$TEMPO/api/traces/$TID" | python3 -m json.tool \
  > backend/tests/fixtures/tempo/sample_trace_oneshot.json
```

- [ ] **Step 5: Commit fixtures**

```bash
git add backend/tests/fixtures/tempo/
git commit -m "test(traces): capture Tempo response fixtures"
```

---

## Task 2: Add tracing.tempo config block

**Files:**
- Modify: `backend/config.yaml:373-386`

- [ ] **Step 1: Add tempo query block under existing `tracing:` section**

Edit `backend/config.yaml` to insert under the existing `otlp:` sibling:

```yaml
  tracing:
    enabled: false
    directory: "./cubepi-traces"
    record_content: false
    otlp:
      endpoint: null
      headers: null
      timeout_seconds: 10
    tempo:
      # HTTP query endpoint for Tempo (admin trace viewer reads from here).
      # Distinct from otlp.endpoint, which is the write path.
      # Leave null to disable the admin trace UI in this environment.
      query_endpoint: null
      timeout_seconds: 10
```

- [ ] **Step 2: Commit**

```bash
git add backend/config.yaml
git commit -m "config(tracing): add tempo.query_endpoint stub"
```

---

## Task 3: Pydantic schemas for trace responses

**Files:**
- Create: `backend/cubeplex/api/schemas/trace.py`
- Test: `backend/tests/unit/test_trace_schema.py`

- [ ] **Step 1: Write the failing schema test**

`backend/tests/unit/test_trace_schema.py`:

```python
"""Shape contract for admin trace API responses."""
from cubeplex.api.schemas.trace import (
    LlmCallPayload,
    SpanNode,
    SpanKind,
    ToolCallPayload,
    TraceDetail,
    TraceSummary,
    TokenUsage,
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
```

- [ ] **Step 2: Run test, expect ImportError**

```bash
cd backend && uv run pytest tests/unit/test_trace_schema.py -v
```
Expected: ImportError on `cubeplex.api.schemas.trace`.

- [ ] **Step 3: Implement schemas**

`backend/cubeplex/api/schemas/trace.py`:

```python
"""Pydantic models for /api/v1/admin/traces responses.

This module is the single source of truth for the cubepi-Tempo → frontend
view model mapping. The TempoClient parser writes into these types; the
frontend reads from them. Update both in lockstep when cubepi span
attributes change.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class SpanKind(str, Enum):
    AGENT = "agent"      # cubepi invoke_agent span
    TURN = "turn"        # cubepi.turn span
    CHAT = "chat"        # gen_ai chat span (LLM call)
    TOOL = "tool"        # execute_tool span
    OTHER = "other"      # anything else


class TokenUsage(BaseModel):
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0


class ChatMessage(BaseModel):
    role: str
    parts: list[dict[str, Any]] = Field(default_factory=list)


class ToolDefinition(BaseModel):
    name: str
    description: Optional[str] = None
    parameters: Optional[dict[str, Any]] = None


class LlmCallPayload(BaseModel):
    """Detail carried by every `chat <model>` span."""
    model: str
    provider: Optional[str] = None
    request_max_tokens: Optional[int] = None
    request_temperature: Optional[float] = None
    request_stream: Optional[bool] = None
    tokens: TokenUsage = Field(default_factory=TokenUsage)
    finish_reasons: list[str] = Field(default_factory=list)
    time_to_first_chunk_seconds: Optional[float] = None
    response_id: Optional[str] = None
    system_instructions: list[ChatMessage] = Field(default_factory=list)
    messages: list[ChatMessage] = Field(default_factory=list)
    output_messages: list[ChatMessage] = Field(default_factory=list)
    tools: list[ToolDefinition] = Field(default_factory=list)
    raw_request: Optional[str] = None      # cubepi.llm.raw_request
    raw_response: Optional[str] = None     # cubepi.llm.raw_response


class ToolCallPayload(BaseModel):
    """Detail carried by every `execute_tool` span."""
    name: str
    description: Optional[str] = None
    arguments: Optional[str] = None
    result: Optional[str] = None
    is_error: bool = False
    execution_mode: Optional[str] = None
    tool_call_id: Optional[str] = None


class TurnPayload(BaseModel):
    index: int
    stop_reason: Optional[str] = None
    tool_calls_count: int = 0


class SpanNode(BaseModel):
    span_id: str
    parent_span_id: Optional[str] = None
    name: str
    kind: SpanKind
    start_time: datetime
    duration_ms: int
    status_code: Optional[str] = None    # "OK" / "ERROR" / None
    status_message: Optional[str] = None
    llm: Optional[LlmCallPayload] = None
    tool: Optional[ToolCallPayload] = None
    turn: Optional[TurnPayload] = None
    raw_attributes: dict[str, Any] = Field(default_factory=dict)  # for "other" kind
    children: list["SpanNode"] = Field(default_factory=list)


class TraceSummary(BaseModel):
    trace_id: str
    root_name: str
    start_time: datetime
    duration_ms: int
    span_count: int
    org_id: Optional[str] = None
    workspace_id: Optional[str] = None
    user_id: Optional[str] = None
    conversation_id: Optional[str] = None
    run_id: Optional[str] = None
    model: Optional[str] = None
    has_error: bool = False


class TraceListResponse(BaseModel):
    # Tempo /api/search has no native cursor; we cap the page at `limit`.
    traces: list[TraceSummary]


class TraceDetail(BaseModel):
    summary: TraceSummary
    root: SpanNode


class TagValuesResponse(BaseModel):
    values: list[str]


SpanNode.model_rebuild()
```

- [ ] **Step 4: Run test, expect pass**

```bash
cd backend && uv run pytest tests/unit/test_trace_schema.py -v
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/api/schemas/trace.py backend/tests/unit/test_trace_schema.py
git commit -m "feat(admin-traces): pydantic schemas for trace responses"
```

---

## Task 4: OTLP→SpanNode parser (kind + tree shape)

**Files:**
- Create: `backend/cubeplex/services/tempo_client.py` (parser portion first)
- Test: `backend/tests/unit/test_tempo_parser.py`

The parser is a pure function that takes Tempo's `/api/traces/{id}` JSON and returns a `TraceDetail`. Building the tree before extracting LLM detail lets us iterate on payload extraction separately.

- [ ] **Step 1: Write the failing tree-shape test**

`backend/tests/unit/test_tempo_parser.py`:

```python
"""Parser: Tempo OTLP JSON → TraceDetail."""
import json
from pathlib import Path

import pytest

from cubeplex.api.schemas.trace import SpanKind
from cubeplex.services.tempo_client import parse_trace_detail

FIXTURES = Path(__file__).parent.parent / "fixtures" / "tempo"


@pytest.fixture
def multi_turn_json() -> dict:
    return json.loads((FIXTURES / "sample_trace_multi_turn.json").read_text())


@pytest.fixture
def oneshot_json() -> dict:
    return json.loads((FIXTURES / "sample_trace_oneshot.json").read_text())


def test_multi_turn_tree_shape(multi_turn_json: dict) -> None:
    detail = parse_trace_detail(multi_turn_json)
    assert detail.root.kind == SpanKind.AGENT
    assert detail.root.name == "invoke_agent"
    # Each immediate child of invoke_agent is a cubepi.turn
    turns = [c for c in detail.root.children if c.kind == SpanKind.TURN]
    assert len(turns) >= 2
    # Each turn has at least one chat child
    for turn in turns:
        chat_children = [c for c in turn.children if c.kind == SpanKind.CHAT]
        assert len(chat_children) >= 1


def test_summary_extracts_business_ids(multi_turn_json: dict) -> None:
    detail = parse_trace_detail(multi_turn_json)
    s = detail.summary
    assert s.org_id and s.org_id.startswith("org-")
    assert s.workspace_id and s.workspace_id.startswith("ws-")
    assert s.user_id and s.user_id.startswith("usr-")
    assert s.conversation_id and s.conversation_id.startswith("conv-")
    assert s.run_id
    assert s.duration_ms > 0
    assert s.span_count == _count(detail.root)


def test_oneshot_collapses_to_single_chat(oneshot_json: dict) -> None:
    detail = parse_trace_detail(oneshot_json)
    # One-shot (e.g. memory consolidate) has agent → chat directly, no turn.
    assert detail.root.kind == SpanKind.AGENT
    leaves = _leaves(detail.root)
    assert any(n.kind == SpanKind.CHAT for n in leaves)


def _count(node) -> int:
    return 1 + sum(_count(c) for c in node.children)


def _leaves(node) -> list:
    if not node.children:
        return [node]
    out = []
    for c in node.children:
        out.extend(_leaves(c))
    return out
```

- [ ] **Step 2: Run test, expect ImportError**

```bash
cd backend && uv run pytest tests/unit/test_tempo_parser.py -v
```

- [ ] **Step 3: Implement parser (full)**

`backend/cubeplex/services/tempo_client.py` — write the complete parser. The
client class lands in Tasks 7–8; the LLM/tool extractors get fleshed out in
Tasks 5–6.

Three classification + summary decisions worth calling out:

- `_classify` reads the OTel `gen_ai.operation.name` attribute first
  (the cubepi/OTel semantic-conv stable signal) and only falls back to span
  name when the attribute is absent. A cubepi rename of the span string
  must not silently degrade every LLM span to OTHER.
- `raw_attributes` is populated for **every** node, not just OTHER. Typed
  payloads (`llm`/`tool`/`turn`) are an overlay; the raw attrs are kept so
  a future cubepi-emitted field (new cache tier, new finish reason) is
  visible in the viewer without code changes.
- `_summary_metadata` scans **every** span for `cubepi.metadata.*` and
  `cubepi.run_id`, not just the AGENT root. A trace whose root isn't an
  AGENT span (e.g. a future cubepi shape) still surfaces its org_id /
  workspace_id / user_id correctly.

```python
"""Tempo HTTP query client + OTLP→view-model parser.

This module is the single point where cubepi span attribute names are
translated into the API contract (cubeplex.api.schemas.trace). Update both
in lockstep when cubepi semantic conventions change.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from cubeplex.api.schemas.trace import (
    LlmCallPayload,
    SpanKind,
    SpanNode,
    TokenUsage,
    ToolCallPayload,
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
    op = attrs.get("gen_ai.operation.name")
    if op == "invoke_agent":
        return SpanKind.AGENT
    if op == "chat":
        return SpanKind.CHAT
    if op == "execute_tool":
        return SpanKind.TOOL
    if name == "cubepi.turn":
        return SpanKind.TURN
    # Last-resort fallback for traces emitted before gen_ai.operation.name was set.
    if name == "invoke_agent":
        return SpanKind.AGENT
    if name.startswith("chat "):
        return SpanKind.CHAT
    if name.startswith("execute_tool"):
        return SpanKind.TOOL
    return SpanKind.OTHER


def _ns_to_dt(ns: str | int) -> datetime:
    return datetime.fromtimestamp(int(ns) / 1_000_000_000, tz=timezone.utc)


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
        if node.parent_span_id:
            children_map.setdefault(node.parent_span_id, []).append(node.span_id)

    for sid, child_ids in children_map.items():
        if sid in nodes:
            nodes[sid].children = sorted(
                [nodes[c] for c in child_ids if c in nodes],
                key=lambda n: n.start_time,
            )

    roots = [n for n in nodes.values() if not n.parent_span_id or n.parent_span_id not in nodes]
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
                if v:
                    out[k] = str(v)
        if "run_id" not in out:
            v = attrs.get("cubepi.run_id")
            if v:
                out["run_id"] = str(v)
    return out


def _extract_turn(attrs: dict[str, Any]) -> TurnPayload:
    return TurnPayload(
        index=int(attrs.get("cubepi.turn.index", 0) or 0),
        stop_reason=attrs.get("cubepi.turn.stop_reason"),
        tool_calls_count=int(attrs.get("cubepi.turn.tool_calls.count", 0) or 0),
    )


def _extract_llm(attrs: dict[str, Any]) -> LlmCallPayload:
    # Tasks 5 fills out the full payload. This stub returns enough for the
    # tree-shape tests in this task; the test asserting full LLM detail lives
    # in Task 5 and would fail against this stub on purpose.
    return LlmCallPayload(
        model=str(
            attrs.get("gen_ai.request.model")
            or attrs.get("gen_ai.response.model")
            or "unknown"
        ),
        provider=attrs.get("gen_ai.provider.name"),
    )


def _extract_tool(attrs: dict[str, Any]) -> ToolCallPayload:
    # Task 6 fills out the full payload.
    return ToolCallPayload(name=str(attrs.get("gen_ai.tool.name") or "?"))
```

- [ ] **Step 4: Run parser tests, expect pass**

```bash
cd backend && uv run pytest tests/unit/test_tempo_parser.py -v
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/services/tempo_client.py backend/tests/unit/test_tempo_parser.py
git commit -m "feat(admin-traces): OTLP→SpanNode parser (tree + summary)"
```

---

## Task 5: Flesh out LLM payload extractor

**Files:**
- Modify: `backend/cubeplex/services/tempo_client.py` (`_extract_llm`)
- Test: `backend/tests/unit/test_tempo_parser.py` (add)

- [ ] **Step 1: Add failing test for LLM extraction**

Append to `test_tempo_parser.py`:

```python
def test_chat_span_llm_payload(multi_turn_json: dict) -> None:
    detail = parse_trace_detail(multi_turn_json)
    chats = [n for n in _leaves(detail.root) if n.kind == SpanKind.CHAT]
    assert chats, "fixture must contain at least one chat span"
    llm = chats[0].llm
    assert llm is not None
    assert llm.model.startswith(("deepseek", "claude", "gpt", "kimi", "qwen"))
    assert llm.tokens.input > 0
    # cubepi always emits cubepi.llm.raw_request on chat spans
    assert llm.raw_request and llm.raw_request.startswith("{")
    assert llm.raw_response and llm.raw_response.startswith("{")
    # gen_ai.input.messages is a JSON-encoded list of role/parts
    assert isinstance(llm.messages, list)
```

- [ ] **Step 2: Run, expect fail (model="?" or tokens=0)**

```bash
cd backend && uv run pytest tests/unit/test_tempo_parser.py::test_chat_span_llm_payload -v
```

- [ ] **Step 3: Replace `_extract_llm` with full version**

```python
import json as _json


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


def _extract_llm(attrs: dict[str, Any]) -> LlmCallPayload:
    finish = attrs.get("gen_ai.response.finish_reasons")
    finish_list = finish if isinstance(finish, list) else ([finish] if finish else [])
    return LlmCallPayload(
        model=str(
            attrs.get("gen_ai.request.model")
            or attrs.get("gen_ai.response.model")
            or "unknown"
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
        tools=_decode_tools(attrs.get("gen_ai.request.tools") or attrs.get("cubepi.agent.tools")),
        raw_request=attrs.get("cubepi.llm.raw_request"),
        raw_response=attrs.get("cubepi.llm.raw_response"),
    )


def _decode_tools(raw: Any) -> list[ToolDefinition]:
    if not raw:
        return []
    data = _json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(data, list):
        return []
    out: list[ToolDefinition] = []
    for item in data:
        if isinstance(item, dict) and "name" in item:
            out.append(ToolDefinition(
                name=str(item["name"]),
                description=item.get("description"),
                parameters=item.get("parameters") or item.get("input_schema"),
            ))
    return out


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
```

Also add the import at the top of the file:

```python
from cubeplex.api.schemas.trace import (
    ChatMessage,
    LlmCallPayload,
    SpanKind,
    SpanNode,
    TokenUsage,
    ToolDefinition,
    ToolCallPayload,
    TraceDetail,
    TraceSummary,
    TurnPayload,
)
```

- [ ] **Step 4: Run, expect pass**

```bash
cd backend && uv run pytest tests/unit/test_tempo_parser.py -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/services/tempo_client.py backend/tests/unit/test_tempo_parser.py
git commit -m "feat(admin-traces): extract LLM call payload from chat spans"
```

---

## Task 6: Flesh out tool payload extractor

**Files:**
- Modify: `backend/cubeplex/services/tempo_client.py` (`_extract_tool`)
- Test: `backend/tests/unit/test_tempo_parser.py` (add)

- [ ] **Step 1: Add failing test**

```python
def test_tool_span_tool_payload(multi_turn_json: dict) -> None:
    detail = parse_trace_detail(multi_turn_json)
    tools = [n for n in _leaves(detail.root) if n.kind == SpanKind.TOOL]
    if not tools:
        pytest.skip("fixture has no tool spans")
    t = tools[0].tool
    assert t is not None
    assert t.name and t.name != "?"
    # cubepi records arguments and result on execute_tool spans
    assert t.arguments is not None
    assert t.result is not None
```

- [ ] **Step 2: Run, expect fail**

```bash
cd backend && uv run pytest tests/unit/test_tempo_parser.py::test_tool_span_tool_payload -v
```

- [ ] **Step 3: Replace `_extract_tool`**

```python
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
```

- [ ] **Step 4: Run, expect pass**

```bash
cd backend && uv run pytest tests/unit/test_tempo_parser.py -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/services/tempo_client.py backend/tests/unit/test_tempo_parser.py
git commit -m "feat(admin-traces): extract tool call payload from execute_tool spans"
```

---

## Task 7: TempoClient — search

**Files:**
- Modify: `backend/cubeplex/services/tempo_client.py` (add class)
- Test: `backend/tests/unit/test_tempo_client.py`

- [ ] **Step 1: Write the failing client test using captured fixture**

`backend/tests/unit/test_tempo_client.py`:

```python
"""TempoClient unit tests (httpx mocked)."""
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
import respx

from cubeplex.services.tempo_client import TempoClient, TempoQueryError

FIXTURES = Path(__file__).parent.parent / "fixtures" / "tempo"


@pytest.fixture
def search_json() -> dict:
    return json.loads((FIXTURES / "sample_search.json").read_text())


@respx.mock
async def test_search_builds_traceql_with_filters(search_json: dict) -> None:
    client = TempoClient(endpoint="http://tempo.local", timeout_seconds=5)
    route = respx.get("http://tempo.local/api/search").mock(
        return_value=httpx.Response(200, json=search_json)
    )
    summaries = await client.search(
        org_id="org-1",
        workspace_id="ws-1",
        user_id=None,
        conversation_id="conv-9",
        run_id=None,
        model=None,
        start=datetime(2026, 6, 1, tzinfo=timezone.utc),
        end=datetime(2026, 6, 11, tzinfo=timezone.utc),
        limit=20,
    )
    assert route.called
    q = route.calls.last.request.url.params["q"]
    assert 'resource.service.name="cubeplex"' in q
    assert 'cubepi.metadata.org_id="org-1"' in q
    assert 'cubepi.metadata.workspace_id="ws-1"' in q
    assert 'cubepi.metadata.conversation_id="conv-9"' in q
    assert isinstance(summaries, list)


@respx.mock
async def test_search_raises_on_5xx() -> None:
    client = TempoClient(endpoint="http://tempo.local", timeout_seconds=5)
    respx.get("http://tempo.local/api/search").mock(
        return_value=httpx.Response(500, text="boom")
    )
    with pytest.raises(TempoQueryError):
        await client.search(org_id="org-1", limit=10)
```

- [ ] **Step 2: Install `respx` if missing**

```bash
cd backend && uv add --dev respx
```

- [ ] **Step 3: Run, expect ImportError on `TempoClient`**

```bash
cd backend && uv run pytest tests/unit/test_tempo_client.py -v
```

- [ ] **Step 4: Implement TempoClient.search**

Append to `backend/cubeplex/services/tempo_client.py`. Note three things:

- **TraceQL value escaping.** Every interpolated value goes through
  `_quote_traceql`, which rejects any string containing characters that
  could break out of the surrounding `"..."` (double-quote, backslash,
  newline, NUL). Returning a 400 on a bad value is safer than emitting a
  query that escapes the org_id clause. The route layer also runs the
  same validation via a regex whitelist on tag values (Task 10).
- **No cursor.** Tempo's `/api/search` returns a fixed page sized by
  `limit`; there is no native cursor token. We pass through `limit`
  only and document the cap.
- **No sort.** Tempo orders results by start time descending; the spec's
  optional duration sort would require client-side resort after fetch.
  Out of scope for v1; reflected in spec and response model.

```python
from datetime import datetime
from typing import Optional

import httpx


class TempoQueryError(RuntimeError):
    """Raised when Tempo returns a non-2xx response."""


class TempoQueryValueError(ValueError):
    """Raised when a filter value would break TraceQL string escaping."""


_TRACEQL_FORBIDDEN = ('"', "\\", "\n", "\r", "\x00")


def _quote_traceql(value: str) -> str:
    """Wrap a value in `"..."` for inclusion in a TraceQL clause.

    Rejects values containing the small set of characters that could break
    out of the surrounding double-quote pair. We reject rather than
    backslash-escape because cubeplex business identifiers (workspace_id,
    user_id, conv_id, run_id, model) are well-formed slugs; any value
    outside that shape is either user error or an injection attempt.
    """
    for c in _TRACEQL_FORBIDDEN:
        if c in value:
            raise TempoQueryValueError(
                f"Filter value contains disallowed character {c!r}"
            )
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
        workspace_id: Optional[str] = None,
        user_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        run_id: Optional[str] = None,
        model: Optional[str] = None,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        min_duration_ms: Optional[int] = None,
        max_duration_ms: Optional[int] = None,
        limit: int = 20,
    ) -> list[TraceSummary]:
        clauses = [
            'resource.service.name="cubeplex"',
            f"span.cubepi.metadata.org_id={_quote_traceql(org_id)}",
        ]
        if workspace_id:
            clauses.append(
                f"span.cubepi.metadata.workspace_id={_quote_traceql(workspace_id)}"
            )
        if user_id:
            clauses.append(f"span.cubepi.metadata.user_id={_quote_traceql(user_id)}")
        if conversation_id:
            clauses.append(
                f"span.cubepi.metadata.conversation_id={_quote_traceql(conversation_id)}"
            )
        if run_id:
            clauses.append(f"span.cubepi.run_id={_quote_traceql(run_id)}")
        if model:
            clauses.append(f"span.gen_ai.request.model={_quote_traceql(model)}")
        if min_duration_ms:
            clauses.append(f"trace:duration > {int(min_duration_ms)}ms")
        if max_duration_ms:
            clauses.append(f"trace:duration < {int(max_duration_ms)}ms")
        q = "{ " + " && ".join(clauses) + " }"

        params: dict[str, Any] = {"q": q, "limit": str(limit)}
        if start:
            params["start"] = str(int(start.timestamp()))
        if end:
            params["end"] = str(int(end.timestamp()))

        async with httpx.AsyncClient(timeout=self._timeout) as http:
            resp = await http.get(f"{self._endpoint}/api/search", params=params)
        if resp.status_code >= 400:
            raise TempoQueryError(
                f"Tempo /api/search returned {resp.status_code}"
            )
        payload = resp.json()
        return [_search_hit_to_summary(t) for t in payload.get("traces", [])]


def _search_hit_to_summary(t: dict[str, Any]) -> TraceSummary:
    return TraceSummary(
        trace_id=t["traceID"],
        root_name=t.get("rootTraceName", ""),
        start_time=_ns_to_dt(t.get("startTimeUnixNano", "0")),
        duration_ms=int(t.get("durationMs", 0)),
        span_count=t.get("spanSet", {}).get("matched", 0),
        # Tempo search hits don't carry span attributes by default — these
        # are filled in by the detail endpoint. For list view we only have
        # what TraceQL surfaced.
    )
```

Add a third unit test asserting the escape rejects forbidden values:

```python
async def test_search_rejects_injection_attempts() -> None:
    from cubeplex.services.tempo_client import TempoQueryValueError
    client = TempoClient(endpoint="http://tempo.local", timeout_seconds=5)
    with pytest.raises(TempoQueryValueError):
        await client.search(
            org_id='ws-x" || true || span.foo="',
        )
```

- [ ] **Step 5: Run, expect pass**

```bash
cd backend && uv run pytest tests/unit/test_tempo_client.py -v
```

- [ ] **Step 6: Commit**

```bash
git add backend/cubeplex/services/tempo_client.py backend/tests/unit/test_tempo_client.py backend/pyproject.toml backend/uv.lock
git commit -m "feat(admin-traces): TempoClient.search with TraceQL builder"
```

---

## Task 8: TempoClient — get_trace and tag_values

**Files:**
- Modify: `backend/cubeplex/services/tempo_client.py`
- Test: `backend/tests/unit/test_tempo_client.py` (append)

- [ ] **Step 1: Add failing tests**

```python
@respx.mock
async def test_get_trace_returns_detail() -> None:
    payload = json.loads((FIXTURES / "sample_trace_multi_turn.json").read_text())
    client = TempoClient(endpoint="http://tempo.local", timeout_seconds=5)
    respx.get("http://tempo.local/api/traces/abc123").mock(
        return_value=httpx.Response(200, json=payload)
    )
    detail = await client.get_trace("abc123")
    assert detail.summary.trace_id == payload["batches"][0]["scopeSpans"][0]["spans"][0]["traceId"]
    assert detail.root.children


@respx.mock
async def test_tag_values_passes_through() -> None:
    client = TempoClient(endpoint="http://tempo.local", timeout_seconds=5)
    respx.get("http://tempo.local/api/search/tag/cubepi.metadata.workspace_id/values").mock(
        return_value=httpx.Response(200, json={"tagValues": ["ws-a", "ws-b"]})
    )
    values = await client.tag_values(
        tag="cubepi.metadata.workspace_id",
        org_id="org-1",
    )
    assert values == ["ws-a", "ws-b"]
```

(Note: an earlier draft accepted a `prefix=` arg on `tag_values`. Tempo's v1
endpoint silently ignores `filter=` — verified against a live instance — so
the prefix arg is dropped from both the client and the route. The typeahead
component does its own client-side narrowing on the returned list.)

- [ ] **Step 2: Run, expect AttributeError**

```bash
cd backend && uv run pytest tests/unit/test_tempo_client.py -v
```

- [ ] **Step 3: Add methods**

```python
    async def get_trace(self, trace_id: str) -> TraceDetail:
        async with httpx.AsyncClient(timeout=self._timeout) as http:
            resp = await http.get(f"{self._endpoint}/api/traces/{trace_id}")
        if resp.status_code == 404:
            raise TempoQueryError(f"Trace {trace_id} not found")
        if resp.status_code >= 400:
            raise TempoQueryError(
                f"Tempo /api/traces/{trace_id} returned {resp.status_code}"
            )
        return parse_trace_detail(resp.json())

    async def tag_values(self, *, tag: str, org_id: str) -> list[str]:
        # Tempo's v1 `/api/search/tag/{name}/values` endpoint returns the
        # complete value list for the tag scoped by the `q=` TraceQL.
        # Verified against Tempo 2.8: there is no server-side prefix filter
        # — the typeahead component does its own client-side narrowing.
        params: dict[str, Any] = {
            "q": "{ resource.service.name=\"cubeplex\" "
                 f"&& span.cubepi.metadata.org_id={_quote_traceql(org_id)} }}",
        }
        async with httpx.AsyncClient(timeout=self._timeout) as http:
            resp = await http.get(
                f"{self._endpoint}/api/search/tag/{tag}/values",
                params=params,
            )
        if resp.status_code >= 400:
            raise TempoQueryError(
                f"Tempo tag values returned {resp.status_code}"
            )
        payload = resp.json()
        return [str(v) for v in payload.get("tagValues", [])]
```

- [ ] **Step 4: Run, expect pass**

```bash
cd backend && uv run pytest tests/unit/test_tempo_client.py -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/services/tempo_client.py backend/tests/unit/test_tempo_client.py
git commit -m "feat(admin-traces): TempoClient.get_trace and tag_values"
```

---

## Task 9: TempoClient factory + DI hook

**Files:**
- Modify: `backend/cubeplex/services/tempo_client.py`

The routes need a way to obtain a `TempoClient` keyed off config, with `None` returned (→ 503) when the endpoint is unset. The factory also exists so E2E tests can override it.

- [ ] **Step 1: Add factory function**

```python
def get_tempo_client() -> TempoClient | None:
    """FastAPI dependency. Returns None when the admin trace viewer is disabled."""
    from cubeplex.config import config

    endpoint = config.get("tracing.tempo.query_endpoint", None)
    if not endpoint:
        return None
    timeout = int(config.get("tracing.tempo.timeout_seconds", 10) or 10)
    return TempoClient(endpoint=str(endpoint), timeout_seconds=timeout)
```

- [ ] **Step 2: Commit**

```bash
git add backend/cubeplex/services/tempo_client.py
git commit -m "feat(admin-traces): TempoClient factory honoring tracing.tempo config"
```

---

## Task 10: Admin traces routes — list + tag-values

**Files:**
- Create: `backend/cubeplex/api/routes/v1/admin_traces.py`
- Modify: `backend/cubeplex/api/app.py` (register router)
- Test: `backend/tests/e2e/test_admin_traces.py`

- [ ] **Step 1: Write the failing E2E test (list)**

`backend/tests/e2e/test_admin_traces.py`:

```python
"""E2E for /api/v1/admin/traces routes."""
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from cubeplex.api.routes.v1 import admin_traces
from cubeplex.api.schemas.trace import TraceSummary

pytestmark = pytest.mark.e2e


@pytest.fixture
def fake_tempo(monkeypatch) -> AsyncMock:
    """Replace the TempoClient factory with a mock returning canned data."""
    client = AsyncMock()
    client.search.return_value = [
        TraceSummary(
            trace_id="t1",
            root_name="invoke_agent",
            start_time=datetime(2026, 6, 11, tzinfo=timezone.utc),
            duration_ms=2300,
            span_count=5,
        ),
    ]
    client.tag_values.return_value = ["ws-a", "ws-b"]
    monkeypatch.setattr(admin_traces, "get_tempo_client", lambda: client)
    return client


async def test_list_traces_requires_admin(admin_client, fake_tempo) -> None:
    client, _ws = admin_client
    resp = await client.get("/api/v1/admin/traces")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["traces"][0]["trace_id"] == "t1"


async def test_list_injects_org_id(admin_client, fake_tempo) -> None:
    client, _ws = admin_client
    resp = await client.get("/api/v1/admin/traces?workspace_id=ws-a")
    assert resp.status_code == 200
    call = fake_tempo.search.await_args
    assert call.kwargs["org_id"].startswith("org-")
    assert call.kwargs["workspace_id"] == "ws-a"


async def test_list_returns_503_when_tempo_unset(admin_client, monkeypatch) -> None:
    monkeypatch.setattr(admin_traces, "get_tempo_client", lambda: None)
    client, _ws = admin_client
    resp = await client.get("/api/v1/admin/traces")
    assert resp.status_code == 503


async def test_tag_values_whitelist(admin_client, fake_tempo) -> None:
    client, _ws = admin_client
    ok = await client.get(
        "/api/v1/admin/traces/tag-values?tag=cubepi.metadata.workspace_id"
    )
    assert ok.status_code == 200
    bad = await client.get("/api/v1/admin/traces/tag-values?tag=secret.bearer")
    assert bad.status_code == 400
```

- [ ] **Step 2: Run, expect ModuleNotFoundError**

```bash
cd backend && uv run pytest tests/e2e/test_admin_traces.py -v
```

- [ ] **Step 3: Implement the router**

`backend/cubeplex/api/routes/v1/admin_traces.py`:

```python
"""Admin trace viewer routes. Gated by require_org_admin.

See docs/dev/specs/2026-06-11-admin-trace-viewer-design.md.

Tempo errors are logged with full body server-side and surfaced to the
admin client as a constant 502 message, so internal hostnames / Tempo
parse errors / query strings never reach the frontend.
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.api.schemas.trace import (
    TagValuesResponse,
    TraceDetail,
    TraceListResponse,
)
from cubeplex.auth.dependencies import require_org_admin, resolve_current_org_id
from cubeplex.db import get_session
from cubeplex.models import User
from cubeplex.services.tempo_client import (
    TempoClient,
    TempoQueryError,
    TempoQueryValueError,
    get_tempo_client,
)

router = APIRouter(prefix="/admin/traces", tags=["admin-traces"])

_ALLOWED_TAGS = frozenset(
    {
        "cubepi.metadata.workspace_id",
        "cubepi.metadata.user_id",
        "cubepi.metadata.conversation_id",
        "gen_ai.request.model",
    }
)


async def _client_or_503() -> TempoClient:
    client = get_tempo_client()
    if client is None:
        raise HTTPException(
            status_code=503,
            detail="Admin trace viewer is not configured for this deployment.",
        )
    return client


def _bad_upstream(exc: Exception) -> HTTPException:
    logger.warning("Tempo upstream error: {}", exc)
    return HTTPException(status_code=502, detail="Upstream trace store error")


# Order matters: /tag-values is registered before /{trace_id} so FastAPI
# matches the literal path first instead of treating "tag-values" as a
# trace_id.
@router.get("", response_model=TraceListResponse)
async def list_traces(
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
    workspace_id: Optional[str] = Query(default=None),
    user_id: Optional[str] = Query(default=None),
    conversation_id: Optional[str] = Query(default=None),
    run_id: Optional[str] = Query(default=None),
    model: Optional[str] = Query(default=None),
    start: Optional[datetime] = Query(default=None),
    end: Optional[datetime] = Query(default=None),
    min_duration_ms: Optional[int] = Query(default=None, ge=0),
    max_duration_ms: Optional[int] = Query(default=None, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
) -> TraceListResponse:
    if start and end and start >= end:
        raise HTTPException(status_code=400, detail="start must be earlier than end")
    client = await _client_or_503()
    org_id = await resolve_current_org_id(user, session)
    try:
        traces = await client.search(
            org_id=org_id,
            workspace_id=workspace_id,
            user_id=user_id,
            conversation_id=conversation_id,
            run_id=run_id,
            model=model,
            start=start,
            end=end,
            min_duration_ms=min_duration_ms,
            max_duration_ms=max_duration_ms,
            limit=limit,
        )
    except TempoQueryValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except TempoQueryError as exc:
        raise _bad_upstream(exc) from exc
    return TraceListResponse(traces=traces)


@router.get("/tag-values", response_model=TagValuesResponse)
async def get_tag_values(
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
    tag: str = Query(..., description="Tag name; must be in the allow list."),
) -> TagValuesResponse:
    if tag not in _ALLOWED_TAGS:
        raise HTTPException(status_code=400, detail=f"Tag '{tag}' not allowed")
    client = await _client_or_503()
    org_id = await resolve_current_org_id(user, session)
    try:
        values = await client.tag_values(tag=tag, org_id=org_id)
    except TempoQueryError as exc:
        raise _bad_upstream(exc) from exc
    return TagValuesResponse(values=values)
```

Both routes keep `session: Depends(get_session)` because
`resolve_current_org_id(user, session)` needs an explicit AsyncSession — this
matches the pattern used by `admin_skills` and other existing admin routers.

- [ ] **Step 4: Register router in app.py**

Edit `backend/cubeplex/api/app.py:475-510`. Add `admin_traces` to the import block:

```python
        admin_traces,
```

And add the include line near the other `admin_*`:

```python
    app.include_router(admin_traces.router, prefix="/api/v1")
```

Also add `admin_traces` to `backend/cubeplex/api/routes/v1/__init__.py` in
both the `from cubeplex.api.routes.v1 import (...)` block and the `__all__`
list, matching the convention used by `admin_skills`, `admin_mcp`, etc.
The package import isn't strictly required by the FastAPI registration,
but keeping `__all__` in sync is the project convention.

- [ ] **Step 5: Run, expect first three tests passing, detail test failing later**

```bash
cd backend && uv run pytest tests/e2e/test_admin_traces.py -v
```
Expected: `test_list_traces_requires_admin`, `test_list_injects_org_id`,
`test_list_returns_503_when_tempo_unset`, `test_tag_values_whitelist` pass.

- [ ] **Step 6: Commit**

```bash
git add backend/cubeplex/api/routes/v1/admin_traces.py backend/cubeplex/api/app.py backend/cubeplex/api/routes/v1/__init__.py backend/tests/e2e/test_admin_traces.py
git commit -m "feat(admin-traces): list and tag-values routes with org_id injection"
```

---

## Task 11: Admin traces detail route with org double-check

**Files:**
- Modify: `backend/cubeplex/api/routes/v1/admin_traces.py`
- Modify: `backend/tests/e2e/test_admin_traces.py` (append)

- [ ] **Step 1: Add failing detail tests**

```python
@pytest.fixture
def fake_resolve_org(monkeypatch):
    """Pin resolve_current_org_id to a known org id, auto-restored after the test."""
    from cubeplex.api.routes.v1 import admin_traces as mod
    async def _fake(*_a, **_kw):
        return "org-MATCH"
    monkeypatch.setattr(mod, "resolve_current_org_id", _fake)
    return "org-MATCH"


async def test_detail_returns_trace(admin_client, fake_tempo, fake_resolve_org) -> None:
    from cubeplex.api.schemas.trace import SpanKind, SpanNode, TraceDetail
    fake_tempo.get_trace.return_value = TraceDetail(
        summary=TraceSummary(
            trace_id="t1",
            root_name="invoke_agent",
            start_time=datetime(2026, 6, 11, tzinfo=timezone.utc),
            duration_ms=1000,
            span_count=1,
            org_id="org-MATCH",
        ),
        root=SpanNode(
            span_id="s1",
            parent_span_id=None,
            name="invoke_agent",
            kind=SpanKind.AGENT,
            start_time=datetime(2026, 6, 11, tzinfo=timezone.utc),
            duration_ms=1000,
            children=[],
        ),
    )
    client, _ws = admin_client
    resp = await client.get("/api/v1/admin/traces/t1")
    assert resp.status_code == 200
    assert resp.json()["summary"]["trace_id"] == "t1"


async def test_detail_404_on_org_mismatch(admin_client, fake_tempo, fake_resolve_org) -> None:
    from cubeplex.api.schemas.trace import SpanKind, SpanNode, TraceDetail
    fake_tempo.get_trace.return_value = TraceDetail(
        summary=TraceSummary(
            trace_id="t1",
            root_name="invoke_agent",
            start_time=datetime(2026, 6, 11, tzinfo=timezone.utc),
            duration_ms=1000,
            span_count=1,
            org_id="org-OTHER",   # someone else's org
        ),
        root=SpanNode(
            span_id="s1",
            parent_span_id=None,
            name="invoke_agent",
            kind=SpanKind.AGENT,
            start_time=datetime(2026, 6, 11, tzinfo=timezone.utc),
            duration_ms=1000,
            children=[],
        ),
    )
    client, _ws = admin_client
    resp = await client.get("/api/v1/admin/traces/t1")
    assert resp.status_code == 404
```

The `fake_resolve_org` fixture uses `monkeypatch.setattr`, so the override is
automatically reverted after the test. A direct `mod.resolve_current_org_id = …`
assignment would leak into every subsequent test running in the same session.

- [ ] **Step 2: Run, expect 404 from missing route**

```bash
cd backend && uv run pytest tests/e2e/test_admin_traces.py::test_detail_returns_trace -v
```

- [ ] **Step 3: Add detail route**

Append to `admin_traces.py`:

```python
@router.get("/{trace_id}", response_model=TraceDetail)
async def get_trace_detail(
    trace_id: str,
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TraceDetail:
    client = await _client_or_503()
    org_id = await resolve_current_org_id(user, session)
    try:
        detail = await client.get_trace(trace_id)
    except TempoQueryError as exc:
        if "not found" in str(exc).lower():
            raise HTTPException(status_code=404, detail="Trace not found") from exc
        raise _bad_upstream(exc) from exc

    # Defence in depth: TraceQL is the primary gate, but a stray trace
    # without an org_id, or one belonging to another org, must never reach
    # the caller.
    if detail.summary.org_id is None or detail.summary.org_id != org_id:
        raise HTTPException(status_code=404, detail="Trace not found")
    return detail
```

- [ ] **Step 4: Run, expect pass**

```bash
cd backend && uv run pytest tests/e2e/test_admin_traces.py -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/api/routes/v1/admin_traces.py backend/tests/e2e/test_admin_traces.py
git commit -m "feat(admin-traces): detail route with org double-check"
```

---

## Task 12: Frontend types + API client

**Files:**
- Create: `frontend/packages/web/components/admin/traces/types.ts`
- Create: `frontend/packages/web/lib/api/admin-traces.ts`

- [ ] **Step 1: Write the types (mirror backend `schemas/trace.py`)**

`frontend/packages/web/components/admin/traces/types.ts`:

```typescript
export type SpanKind = 'agent' | 'turn' | 'chat' | 'tool' | 'other'

export interface TokenUsage {
  input: number
  output: number
  cache_read: number
  cache_write: number
}

export interface ChatMessage {
  role: string
  parts: Array<Record<string, unknown>>
}

export interface ToolDefinition {
  name: string
  description?: string | null
  parameters?: Record<string, unknown> | null
}

export interface LlmCallPayload {
  model: string
  provider?: string | null
  request_max_tokens?: number | null
  request_temperature?: number | null
  request_stream?: boolean | null
  tokens: TokenUsage
  finish_reasons: string[]
  time_to_first_chunk_seconds?: number | null
  response_id?: string | null
  system_instructions: ChatMessage[]
  messages: ChatMessage[]
  output_messages: ChatMessage[]
  tools: ToolDefinition[]
  raw_request?: string | null
  raw_response?: string | null
}

export interface ToolCallPayload {
  name: string
  description?: string | null
  arguments?: string | null
  result?: string | null
  is_error: boolean
  execution_mode?: string | null
  tool_call_id?: string | null
}

export interface TurnPayload {
  index: number
  stop_reason?: string | null
  tool_calls_count: number
}

export interface SpanNode {
  span_id: string
  parent_span_id?: string | null
  name: string
  kind: SpanKind
  start_time: string
  duration_ms: number
  status_code?: string | null
  status_message?: string | null
  llm?: LlmCallPayload | null
  tool?: ToolCallPayload | null
  turn?: TurnPayload | null
  raw_attributes: Record<string, unknown>
  children: SpanNode[]
}

export interface TraceSummary {
  trace_id: string
  root_name: string
  start_time: string
  duration_ms: number
  span_count: number
  org_id?: string | null
  workspace_id?: string | null
  user_id?: string | null
  conversation_id?: string | null
  run_id?: string | null
  model?: string | null
  has_error: boolean
}

export interface TraceListResponse {
  traces: TraceSummary[]
  next_cursor?: string | null
}

export interface TraceDetail {
  summary: TraceSummary
  root: SpanNode
}

export interface TraceFilterValues {
  workspace_id?: string
  user_id?: string
  conversation_id?: string
  run_id?: string
  model?: string
  start?: string
  end?: string
  min_duration_ms?: number
  max_duration_ms?: number
  limit?: number
}
```

- [ ] **Step 2: Write the API client**

The existing admin API helpers in `lib/api/presets.ts` use plain `fetch` with
`credentials: 'include'` + `readApiError` from `@/lib/csrf`. Follow that
pattern — no `createApiClient` at module load. Admin endpoints don't need
the workspace-id-scoped client state that `createApiClient` provides.

`frontend/packages/web/lib/api/admin-traces.ts`:

```typescript
import { readApiError } from '@/lib/csrf'
import type {
  TraceDetail,
  TraceFilterValues,
  TraceListResponse,
} from '@/components/admin/traces/types'

function toQuery(filters: TraceFilterValues): string {
  const params = new URLSearchParams()
  for (const [k, v] of Object.entries(filters)) {
    if (v === undefined || v === null || v === '') continue
    params.set(k, String(v))
  }
  return params.toString()
}

export class AdminTracesDisabledError extends Error {
  constructor() {
    super('Admin trace viewer is not configured for this deployment.')
    this.name = 'AdminTracesDisabledError'
  }
}

async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url, { credentials: 'include' })
  if (res.status === 503) throw new AdminTracesDisabledError()
  if (!res.ok) throw new Error(await readApiError(res))
  return (await res.json()) as T
}

export async function listAdminTraces(
  filters: TraceFilterValues,
): Promise<TraceListResponse> {
  const qs = toQuery(filters)
  return getJson<TraceListResponse>(
    `/api/v1/admin/traces${qs ? `?${qs}` : ''}`,
  )
}

export async function getAdminTraceDetail(traceId: string): Promise<TraceDetail> {
  return getJson<TraceDetail>(
    `/api/v1/admin/traces/${encodeURIComponent(traceId)}`,
  )
}

export async function getAdminTraceTagValues(tag: string): Promise<string[]> {
  const params = new URLSearchParams({ tag })
  const res = await getJson<{ values: string[] }>(
    `/api/v1/admin/traces/tag-values?${params.toString()}`,
  )
  return res.values
}
```

`AdminTracesDisabledError` lets the page render a dedicated empty state for
the 503-disabled case instead of a generic error toast.

- [ ] **Step 3: Type-check**

```bash
cd frontend && pnpm --filter web typecheck
```
Expected: no new errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/packages/web/components/admin/traces/types.ts frontend/packages/web/lib/api/admin-traces.ts
git commit -m "feat(admin-traces): frontend types + API client"
```

---

## Task 13: TraceListPage with filter bar and table

**Files:**
- Create: `frontend/packages/web/app/admin/traces/page.tsx`
- Create: `frontend/packages/web/components/admin/traces/TraceFilterBar.tsx`
- Create: `frontend/packages/web/components/admin/traces/TraceListTable.tsx`
- Modify: `frontend/packages/web/messages/en.json`, `zh.json`

- [ ] **Step 1: Add i18n strings**

Two separate edits to **both** `en.json` and `zh.json`:

**Edit 1 — under existing `"adminNav"` block, append a `traces` key:**

```json
"adminNav": {
  // ...existing keys...
  "traces": "Traces"   // zh: "Trace"
}
```

This key is what Task 16's `t('traces')` in `AdminSubNav` resolves to.
Missing it produces a literal `"adminNav.traces"` label in the sidebar.

**Edit 2 — at top level, add `adminTraces`:**

```json
"adminTraces": {
  "title": "Traces",
  "subtitle": "Agent runs traced to Tempo for this org.",
  "filters": {
    "workspace": "Workspace",
    "user": "User",
    "conversation": "Conversation",
    "model": "Model",
    "runId": "Run ID",
    "from": "From",
    "to": "To"
  },
  "columns": {
    "startTime": "Start",
    "duration": "Duration",
    "model": "Model",
    "spans": "Spans",
    "workspace": "Workspace",
    "user": "User",
    "conversation": "Conversation"
  },
  "empty": "No traces match these filters.",
  "disabled": "Trace viewer is not configured for this deployment.",
  "loading": "Loading traces…"
}
```

Mirror both edits in `zh.json` with translated values.

- [ ] **Step 2: Write `TraceFilterBar.tsx`**

```tsx
'use client'

import { useTranslations } from 'next-intl'
import type { TraceFilterValues } from './types'

interface Props {
  value: TraceFilterValues
  onChange: (next: TraceFilterValues) => void
}

export function TraceFilterBar({ value, onChange }: Props) {
  const t = useTranslations('adminTraces.filters')
  const field = (k: keyof TraceFilterValues, label: string, type = 'text') => (
    <label className="flex flex-col gap-1 text-xs text-muted-foreground">
      <span>{label}</span>
      <input
        type={type}
        value={(value[k] as string | number | undefined) ?? ''}
        onChange={(e) => onChange({ ...value, [k]: e.target.value || undefined })}
        className="rounded border border-border bg-card px-2 py-1 text-sm text-foreground"
      />
    </label>
  )
  return (
    <div className="flex flex-wrap gap-3 border-b border-border bg-card/40 px-4 py-3">
      {field('workspace_id', t('workspace'))}
      {field('user_id', t('user'))}
      {field('conversation_id', t('conversation'))}
      {field('model', t('model'))}
      {field('run_id', t('runId'))}
      {field('start', t('from'), 'datetime-local')}
      {field('end', t('to'), 'datetime-local')}
    </div>
  )
}
```

- [ ] **Step 3: Write `TraceListTable.tsx`**

```tsx
'use client'

import Link from 'next/link'
import { useTranslations } from 'next-intl'
import type { TraceSummary } from './types'

interface Props {
  traces: TraceSummary[]
}

export function TraceListTable({ traces }: Props) {
  const t = useTranslations('adminTraces.columns')
  return (
    <table className="w-full text-sm">
      <thead className="text-left text-xs uppercase text-muted-foreground">
        <tr>
          <th className="px-3 py-2">{t('startTime')}</th>
          <th className="px-3 py-2">{t('duration')}</th>
          <th className="px-3 py-2">{t('model')}</th>
          <th className="px-3 py-2">{t('workspace')}</th>
          <th className="px-3 py-2">{t('user')}</th>
          <th className="px-3 py-2">{t('conversation')}</th>
          <th className="px-3 py-2">{t('spans')}</th>
        </tr>
      </thead>
      <tbody>
        {traces.map((tr) => (
          <tr
            key={tr.trace_id}
            className="border-t border-border/60 hover:bg-muted/40"
          >
            <td className="px-3 py-2">
              <Link
                className="font-mono text-primary hover:underline"
                href={`/admin/traces/${encodeURIComponent(tr.trace_id)}`}
              >
                {new Date(tr.start_time).toLocaleString()}
              </Link>
            </td>
            <td className="px-3 py-2">{tr.duration_ms} ms</td>
            <td className="px-3 py-2">{tr.model ?? '—'}</td>
            <td className="px-3 py-2 font-mono text-xs">{tr.workspace_id ?? '—'}</td>
            <td className="px-3 py-2 font-mono text-xs">{tr.user_id ?? '—'}</td>
            <td className="px-3 py-2 font-mono text-xs">{tr.conversation_id ?? '—'}</td>
            <td className="px-3 py-2">{tr.span_count}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
```

- [ ] **Step 4: Write `page.tsx`**

```tsx
'use client'

import { useCallback, useEffect, useState } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import { useTranslations } from 'next-intl'

import { TraceFilterBar } from '@/components/admin/traces/TraceFilterBar'
import { TraceListTable } from '@/components/admin/traces/TraceListTable'
import type {
  TraceFilterValues,
  TraceSummary,
} from '@/components/admin/traces/types'
import {
  AdminTracesDisabledError,
  listAdminTraces,
} from '@/lib/api/admin-traces'

function valuesFromSearchParams(sp: URLSearchParams): TraceFilterValues {
  const v: TraceFilterValues = {}
  for (const k of ['workspace_id', 'user_id', 'conversation_id', 'run_id', 'model', 'start', 'end'] as const) {
    const val = sp.get(k)
    if (val) v[k] = val
  }
  return v
}

export default function AdminTracesPage() {
  const t = useTranslations('adminTraces')
  const router = useRouter()
  const sp = useSearchParams()
  const [filters, setFilters] = useState<TraceFilterValues>(() =>
    valuesFromSearchParams(new URLSearchParams(sp?.toString() ?? '')),
  )
  const [traces, setTraces] = useState<TraceSummary[]>([])
  const [error, setError] = useState<string | null>(null)
  const [disabled, setDisabled] = useState(false)
  const [loading, setLoading] = useState(false)

  const fetchPage = useCallback(async (f: TraceFilterValues) => {
    setLoading(true)
    setError(null)
    setDisabled(false)
    try {
      const res = await listAdminTraces({ ...f, limit: 50 })
      setTraces(res.traces)
    } catch (e: unknown) {
      if (e instanceof AdminTracesDisabledError) {
        setDisabled(true)
      } else {
        setError(e instanceof Error ? e.message : String(e))
      }
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchPage(filters)
  }, [filters, fetchPage])

  const handleChange = (next: TraceFilterValues) => {
    setFilters(next)
    const usp = new URLSearchParams()
    for (const [k, v] of Object.entries(next)) {
      if (v) usp.set(k, String(v))
    }
    router.replace(`/admin/traces${usp.toString() ? `?${usp.toString()}` : ''}`)
  }

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-border bg-card px-6 py-4">
        <h1 className="text-lg font-semibold">{t('title')}</h1>
        <p className="text-sm text-muted-foreground">{t('subtitle')}</p>
      </div>
      <TraceFilterBar value={filters} onChange={handleChange} />
      <div className="flex-1 overflow-auto">
        {disabled && (
          <div className="p-6 text-sm text-muted-foreground">{t('disabled')}</div>
        )}
        {!disabled && loading && (
          <div className="p-6 text-sm text-muted-foreground">{t('loading')}</div>
        )}
        {!disabled && error && (
          <div className="p-6 text-sm text-destructive">{error}</div>
        )}
        {!disabled && !loading && !error && traces.length === 0 && (
          <div className="p-6 text-sm text-muted-foreground">{t('empty')}</div>
        )}
        {!disabled && !loading && !error && traces.length > 0 && (
          <TraceListTable traces={traces} />
        )}
      </div>
    </div>
  )
}
```

- [ ] **Step 5: Type-check + dev-server smoke**

```bash
cd frontend && pnpm --filter web typecheck
```

Smoke (in a separate terminal in the worktree):

```bash
cd backend && CUBEPLEX_TRACING__TEMPO__QUERY_ENDPOINT=http://localhost:32770 \
  uv run python main.py    # uses PORT from .worktree.env

# in another shell:
cd frontend && pnpm --filter web dev
# open http://localhost:3023/admin/traces (port from .worktree.env)
```

Verify: page loads; filters surface in URL; a real trace list appears.

- [ ] **Step 6: Commit**

```bash
git add frontend/packages/web/app/admin/traces frontend/packages/web/components/admin/traces frontend/packages/web/lib/api/admin-traces.ts frontend/packages/web/messages/
git commit -m "feat(admin-traces): list page with filter bar and URL-state filters"
```

---

## Task 14: TraceDetailPage — shell, SpanTree, SpanDetail wiring

**Files:**
- Create: `frontend/packages/web/app/admin/traces/[traceId]/page.tsx`
- Create: `frontend/packages/web/components/admin/traces/SpanTree.tsx`
- Create: `frontend/packages/web/components/admin/traces/SpanDetail.tsx`
- Create: `frontend/packages/web/components/admin/traces/cards/JsonBlock.tsx`

- [ ] **Step 1: Write `JsonBlock.tsx` (used by every card)**

```tsx
'use client'

import { useState } from 'react'
import { Check, Copy } from 'lucide-react'

interface Props {
  value: string | undefined | null
  language?: 'json' | 'text'
}

export function JsonBlock({ value, language = 'json' }: Props) {
  const [copied, setCopied] = useState(false)
  if (!value) return null
  const formatted = (() => {
    if (language !== 'json') return value
    try {
      return JSON.stringify(JSON.parse(value), null, 2)
    } catch {
      return value
    }
  })()
  const copy = async () => {
    await navigator.clipboard.writeText(formatted)
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }
  return (
    <div className="relative">
      <button
        type="button"
        onClick={copy}
        className="absolute right-2 top-2 rounded bg-card/80 p-1 text-muted-foreground hover:text-foreground"
        aria-label="Copy"
      >
        {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
      </button>
      <pre className="max-h-96 overflow-auto rounded bg-muted/40 p-3 text-xs font-mono text-foreground">
        {formatted}
      </pre>
    </div>
  )
}
```

- [ ] **Step 2: Write `SpanTree.tsx`**

```tsx
'use client'

import { useMemo } from 'react'
import type { SpanNode } from './types'

interface Props {
  root: SpanNode
  selectedSpanId: string
  onSelect: (id: string) => void
}

const KIND_COLOR: Record<string, string> = {
  agent: 'bg-purple-500/20 text-purple-300',
  turn: 'bg-amber-500/20 text-amber-300',
  chat: 'bg-blue-500/20 text-blue-300',
  tool: 'bg-emerald-500/20 text-emerald-300',
  other: 'bg-muted text-muted-foreground',
}

interface FlatRow {
  node: SpanNode
  depth: number
  offsetMs: number
  totalMs: number
}

function flatten(root: SpanNode): FlatRow[] {
  const rootStart = new Date(root.start_time).getTime()
  const totalMs = root.duration_ms
  const out: FlatRow[] = []
  const walk = (n: SpanNode, depth: number) => {
    const off = new Date(n.start_time).getTime() - rootStart
    out.push({ node: n, depth, offsetMs: off, totalMs })
    for (const c of n.children) walk(c, depth + 1)
  }
  walk(root, 0)
  return out
}

export function SpanTree({ root, selectedSpanId, onSelect }: Props) {
  const rows = useMemo(() => flatten(root), [root])
  return (
    <ul className="select-none">
      {rows.map(({ node, depth, offsetMs, totalMs }) => {
        const left = totalMs ? (offsetMs / totalMs) * 100 : 0
        const width = totalMs ? Math.max((node.duration_ms / totalMs) * 100, 0.5) : 0
        const selected = node.span_id === selectedSpanId
        return (
          <li
            key={node.span_id}
            onClick={() => onSelect(node.span_id)}
            className={`flex cursor-pointer items-center gap-3 border-b border-border/30 px-3 py-1.5 text-xs ${
              selected ? 'bg-primary/10' : 'hover:bg-muted/30'
            }`}
          >
            <div
              className="flex-1 truncate"
              style={{ paddingLeft: depth * 16 }}
            >
              <span className="truncate font-medium">{node.name}</span>
              <span
                className={`ml-2 rounded px-1.5 py-0.5 text-[10px] ${
                  KIND_COLOR[node.kind] ?? KIND_COLOR.other
                }`}
              >
                {node.kind}
              </span>
            </div>
            <div className="relative h-4 w-[40%] rounded bg-muted/30">
              <div
                className={`absolute h-3 rounded ${
                  KIND_COLOR[node.kind]?.split(' ')[0] ?? 'bg-muted'
                }`}
                style={{ left: `${left}%`, width: `${width}%`, top: 2 }}
              />
            </div>
            <span className="w-16 text-right font-mono text-muted-foreground">
              {node.duration_ms} ms
            </span>
          </li>
        )
      })}
    </ul>
  )
}
```

- [ ] **Step 3: Write `SpanDetail.tsx` (placeholder shell — Task 15 fills in cards)**

```tsx
'use client'

import { useTranslations } from 'next-intl'
import type { SpanNode } from './types'
import { JsonBlock } from './cards/JsonBlock'

interface Props {
  node: SpanNode
}

export function SpanDetail({ node }: Props) {
  const t = useTranslations('adminTraces')
  return (
    <div className="space-y-4 p-4">
      <div>
        <h2 className="text-base font-semibold">{node.name}</h2>
        <p className="text-xs text-muted-foreground">
          {node.kind} · {node.duration_ms} ms · {new Date(node.start_time).toLocaleString()}
        </p>
      </div>
      {node.kind === 'other' && (
        <JsonBlock value={JSON.stringify(node.raw_attributes, null, 2)} />
      )}
      {/* LLM and tool cards land in Task 15. */}
    </div>
  )
}
```

- [ ] **Step 4: Write `[traceId]/page.tsx`**

```tsx
'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { useParams, useRouter, useSearchParams } from 'next/navigation'

import { SpanDetail } from '@/components/admin/traces/SpanDetail'
import { SpanTree } from '@/components/admin/traces/SpanTree'
import type {
  SpanNode,
  TraceDetail,
} from '@/components/admin/traces/types'
import { getAdminTraceDetail } from '@/lib/api/admin-traces'

function findSpan(root: SpanNode, id: string): SpanNode | null {
  if (root.span_id === id) return root
  for (const c of root.children) {
    const hit = findSpan(c, id)
    if (hit) return hit
  }
  return null
}

export default function AdminTraceDetailPage() {
  const params = useParams<{ traceId: string }>()
  const router = useRouter()
  const sp = useSearchParams()
  const [detail, setDetail] = useState<TraceDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const initialSpanId = sp?.get('span') ?? ''
  const [selectedSpanId, setSelectedSpanId] = useState<string>(initialSpanId)

  useEffect(() => {
    let cancelled = false
    getAdminTraceDetail(params.traceId)
      .then((d) => {
        if (cancelled) return
        setDetail(d)
        if (!selectedSpanId) setSelectedSpanId(d.root.span_id)
      })
      .catch((e: unknown) => !cancelled && setError(String(e)))
    return () => {
      cancelled = true
    }
  }, [params.traceId, selectedSpanId])

  const selected = useMemo(
    () => (detail && selectedSpanId ? findSpan(detail.root, selectedSpanId) : null),
    [detail, selectedSpanId],
  )

  const onSelect = useCallback(
    (id: string) => {
      setSelectedSpanId(id)
      const next = new URLSearchParams(sp?.toString() ?? '')
      next.set('span', id)
      router.replace(`?${next.toString()}`)
    },
    [router, sp],
  )

  if (error) return <div className="p-6 text-sm text-destructive">{error}</div>
  if (!detail) return <div className="p-6 text-sm text-muted-foreground">…</div>

  return (
    <div className="grid h-full grid-cols-[420px_1fr] overflow-hidden">
      <div className="overflow-y-auto border-r border-border">
        <div className="border-b border-border px-3 py-2 text-xs text-muted-foreground">
          <div className="font-mono">{detail.summary.trace_id}</div>
          <div>{detail.summary.duration_ms} ms · {detail.summary.span_count} spans</div>
        </div>
        <SpanTree
          root={detail.root}
          selectedSpanId={selectedSpanId}
          onSelect={onSelect}
        />
      </div>
      <div className="overflow-y-auto">
        {selected ? <SpanDetail node={selected} /> : null}
      </div>
    </div>
  )
}
```

- [ ] **Step 5: Type-check + smoke**

```bash
cd frontend && pnpm --filter web typecheck
```
Open `/admin/traces/<a real trace id>` in the dev server. The tree renders, clicking spans updates `?span=…`.

- [ ] **Step 6: Commit**

```bash
git add frontend/packages/web/app/admin/traces frontend/packages/web/components/admin/traces
git commit -m "feat(admin-traces): detail page shell + span tree with duration bars"
```

---

## Task 15: LLM and tool cards in SpanDetail

**Files:**
- Create: `frontend/packages/web/components/admin/traces/cards/LlmCard.tsx`
- Create: `frontend/packages/web/components/admin/traces/cards/ToolCard.tsx`
- Modify: `frontend/packages/web/components/admin/traces/SpanDetail.tsx`
- Modify: `frontend/packages/web/messages/en.json`, `zh.json`

- [ ] **Step 1: Extend i18n**

Add under `adminTraces` in `en.json`:

```json
"sections": {
  "model": "Model",
  "tokens": "Token usage",
  "messages": "Messages",
  "system": "System instructions",
  "output": "Output messages",
  "tools": "Tools defined",
  "rawRequest": "Raw request",
  "rawResponse": "Raw response",
  "performance": "Performance",
  "toolInfo": "Tool",
  "arguments": "Arguments",
  "result": "Result"
}
```

(Mirror in zh.json with translations.)

- [ ] **Step 2: Write `LlmCard.tsx`**

```tsx
'use client'

import { useState } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'
import { useTranslations } from 'next-intl'

import type { ChatMessage, LlmCallPayload } from '../types'
import { JsonBlock } from './JsonBlock'

interface Props {
  llm: LlmCallPayload
}

function Section({
  title,
  defaultOpen = true,
  children,
}: {
  title: string
  defaultOpen?: boolean
  children: React.ReactNode
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="rounded border border-border bg-card">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex w-full items-center justify-between px-3 py-2 text-sm font-medium"
      >
        <span>{title}</span>
        {open ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
      </button>
      {open && <div className="border-t border-border px-3 py-3">{children}</div>}
    </div>
  )
}

function Messages({ items }: { items: ChatMessage[] }) {
  if (!items.length) return <div className="text-xs text-muted-foreground">—</div>
  return (
    <div className="space-y-3">
      {items.map((m, i) => (
        <div key={i} className="rounded border border-border/60 bg-muted/20 p-2">
          <div className="mb-1 text-xs font-semibold uppercase text-muted-foreground">
            {m.role}
          </div>
          <JsonBlock value={JSON.stringify(m.parts, null, 2)} />
        </div>
      ))}
    </div>
  )
}

export function LlmCard({ llm }: Props) {
  const t = useTranslations('adminTraces.sections')
  return (
    <div className="space-y-3">
      <Section title={t('model')}>
        <dl className="grid grid-cols-2 gap-2 text-xs">
          <dt className="text-muted-foreground">Model</dt>
          <dd className="font-mono">{llm.model}</dd>
          <dt className="text-muted-foreground">Provider</dt>
          <dd className="font-mono">{llm.provider ?? '—'}</dd>
          <dt className="text-muted-foreground">Max tokens</dt>
          <dd className="font-mono">{llm.request_max_tokens ?? '—'}</dd>
          <dt className="text-muted-foreground">Temperature</dt>
          <dd className="font-mono">{llm.request_temperature ?? '—'}</dd>
          <dt className="text-muted-foreground">Stream</dt>
          <dd className="font-mono">{String(llm.request_stream ?? '—')}</dd>
          <dt className="text-muted-foreground">Finish</dt>
          <dd className="font-mono">{llm.finish_reasons.join(', ') || '—'}</dd>
        </dl>
      </Section>

      <Section title={t('tokens')}>
        <dl className="grid grid-cols-4 gap-2 text-center text-xs">
          <div>
            <dt className="text-muted-foreground">input</dt>
            <dd className="font-mono">{llm.tokens.input}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">output</dt>
            <dd className="font-mono">{llm.tokens.output}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">cache read</dt>
            <dd className="font-mono">{llm.tokens.cache_read}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">cache write</dt>
            <dd className="font-mono">{llm.tokens.cache_write}</dd>
          </div>
        </dl>
      </Section>

      {llm.tools.length > 0 && (
        <Section title={t('tools')} defaultOpen={false}>
          <ul className="space-y-2 text-xs">
            {llm.tools.map((tool) => (
              <li key={tool.name} className="rounded border border-border/60 p-2">
                <div className="font-mono font-semibold">{tool.name}</div>
                {tool.description && (
                  <div className="text-muted-foreground">{tool.description}</div>
                )}
              </li>
            ))}
          </ul>
        </Section>
      )}

      <Section title={t('system')} defaultOpen={false}>
        <Messages items={llm.system_instructions} />
      </Section>
      <Section title={t('messages')}>
        <Messages items={llm.messages} />
      </Section>
      <Section title={t('output')}>
        <Messages items={llm.output_messages} />
      </Section>
      <Section title={t('rawRequest')} defaultOpen={false}>
        <JsonBlock value={llm.raw_request} />
      </Section>
      <Section title={t('rawResponse')} defaultOpen={false}>
        <JsonBlock value={llm.raw_response} />
      </Section>

      <Section title={t('performance')} defaultOpen={false}>
        <dl className="grid grid-cols-2 gap-2 text-xs">
          <dt className="text-muted-foreground">Time to first chunk</dt>
          <dd className="font-mono">
            {llm.time_to_first_chunk_seconds != null
              ? `${llm.time_to_first_chunk_seconds.toFixed(2)} s`
              : '—'}
          </dd>
          <dt className="text-muted-foreground">Response ID</dt>
          <dd className="font-mono">{llm.response_id ?? '—'}</dd>
        </dl>
      </Section>
    </div>
  )
}
```

- [ ] **Step 3: Write `ToolCard.tsx`**

```tsx
'use client'

import { useTranslations } from 'next-intl'

import type { ToolCallPayload } from '../types'
import { JsonBlock } from './JsonBlock'

interface Props {
  tool: ToolCallPayload
}

export function ToolCard({ tool }: Props) {
  const t = useTranslations('adminTraces.sections')
  return (
    <div className="space-y-3">
      <div className="rounded border border-border bg-card p-3">
        <div className="text-xs text-muted-foreground">{t('toolInfo')}</div>
        <div className="mt-1 font-mono text-sm font-semibold">{tool.name}</div>
        {tool.description && (
          <div className="mt-1 text-xs text-muted-foreground">{tool.description}</div>
        )}
        {tool.is_error && (
          <div className="mt-2 text-xs font-medium text-destructive">errored</div>
        )}
      </div>
      <div>
        <div className="mb-1 text-xs font-medium text-muted-foreground">
          {t('arguments')}
        </div>
        <JsonBlock value={tool.arguments} />
      </div>
      <div>
        <div className="mb-1 text-xs font-medium text-muted-foreground">
          {t('result')}
        </div>
        <JsonBlock value={tool.result} language="text" />
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Wire cards into `SpanDetail.tsx`**

Replace `SpanDetail.tsx`:

```tsx
'use client'

import type { SpanNode } from './types'
import { JsonBlock } from './cards/JsonBlock'
import { LlmCard } from './cards/LlmCard'
import { ToolCard } from './cards/ToolCard'

interface Props {
  node: SpanNode
}

export function SpanDetail({ node }: Props) {
  return (
    <div className="space-y-4 p-4">
      <div>
        <h2 className="text-base font-semibold">{node.name}</h2>
        <p className="text-xs text-muted-foreground">
          {node.kind} · {node.duration_ms} ms ·{' '}
          {new Date(node.start_time).toLocaleString()}
        </p>
      </div>
      {node.llm && <LlmCard llm={node.llm} />}
      {node.tool && <ToolCard tool={node.tool} />}
      {node.turn && (
        <div className="rounded border border-border bg-card p-3 text-xs">
          <div className="font-semibold">Turn {node.turn.index}</div>
          <div className="text-muted-foreground">
            stop: {node.turn.stop_reason ?? '—'} · tool_calls: {node.turn.tool_calls_count}
          </div>
        </div>
      )}
      {node.kind === 'other' && (
        <JsonBlock value={JSON.stringify(node.raw_attributes, null, 2)} />
      )}
    </div>
  )
}
```

- [ ] **Step 5: Type-check + smoke**

```bash
cd frontend && pnpm --filter web typecheck
```

In the dev server, click a `chat` span; verify model, tokens, messages, and
raw_request/raw_response render. Click an `execute_tool` span; verify
arguments/result render.

- [ ] **Step 6: Commit**

```bash
git add frontend/packages/web/components/admin/traces frontend/packages/web/messages
git commit -m "feat(admin-traces): LLM and tool cards in span detail"
```

---

## Task 16: Add admin nav entry

**Files:**
- Modify: `frontend/packages/web/components/admin/AdminSubNav.tsx`

- [ ] **Step 1: Add the nav row**

In `AdminSubNav.tsx`, import `Activity` from `lucide-react` (or use an
existing icon like `BarChart3`), and add to `NATIVE_ITEMS` between
`insights` and the end:

```tsx
    { href: '/admin/traces', label: t('traces'), icon: Activity },
```

- [ ] **Step 2: Type-check**

```bash
cd frontend && pnpm --filter web typecheck
```

- [ ] **Step 3: Verify in dev server** — the Traces entry appears in the admin sidebar and routes to the list page.

- [ ] **Step 4: Commit**

```bash
git add frontend/packages/web/components/admin/AdminSubNav.tsx
git commit -m "feat(admin-traces): add admin nav entry"
```

---

## Task 17: Full backend test sweep + lint

**Files:** none (verification only)

- [ ] **Step 1: Run all changed-module backend tests**

```bash
cd backend && uv run pytest tests/unit/test_trace_schema.py tests/unit/test_tempo_parser.py tests/unit/test_tempo_client.py tests/e2e/test_admin_traces.py -v
```
Expected: all pass.

- [ ] **Step 2: mypy on the new files**

```bash
cd backend && uv run mypy cubeplex/api/schemas/trace.py cubeplex/services/tempo_client.py cubeplex/api/routes/v1/admin_traces.py
```
Expected: Success.

- [ ] **Step 3: ruff**

```bash
cd backend && uv run ruff check cubeplex/api/schemas/trace.py cubeplex/services/tempo_client.py cubeplex/api/routes/v1/admin_traces.py tests/unit/test_tempo_parser.py tests/unit/test_tempo_client.py tests/e2e/test_admin_traces.py
```

- [ ] **Step 4: Frontend lint + typecheck**

```bash
cd frontend && pnpm --filter web lint && pnpm --filter web typecheck
```

- [ ] **Step 5: Open PR**

```bash
git push -u origin feat/admin-trace-viewer
gh pr create --title "feat: admin trace viewer" --body "$(cat <<'EOF'
## Summary
- Org admins can list and inspect their org's cubeplex agent traces stored in Grafana Tempo.
- Backend proxies Tempo's HTTP query API; org_id predicate is injected into every TraceQL.
- Frontend renders a span tree + LLM-aware detail cards.

Spec: docs/dev/specs/2026-06-11-admin-trace-viewer-design.md
Plan: docs/dev/plans/2026-06-11-admin-trace-viewer.md

## Test plan
- [ ] Visit /admin/traces with tracing.tempo.query_endpoint unset → 503 + disabled message
- [ ] Visit /admin/traces as org admin with endpoint set → list renders
- [ ] Filter by workspace_id / conversation_id → URL updates, list narrows
- [ ] Open a trace detail → span tree renders, duration bars look right
- [ ] Click a chat span → LLM card shows model, tokens, messages, raw request/response
- [ ] Click an execute_tool span → tool card shows arguments + result
- [ ] As non-admin user, /admin/traces redirects away (existing admin guard)
EOF
)"
```

Then run the `/pr-codex-review-loop` skill until the PR is clean.

---

## Self-Review

**Spec coverage:**
- Org-admin route gating → Task 10, 11 (require_org_admin)
- TraceQL org_id injection + escaping → Task 7 (`_quote_traceql`)
- Detail double-check → Task 11
- 3 routes (list / tag-values / detail) → Tasks 10, 11
- `tempo_client.py` as single mapping point → Tasks 4–9
- pydantic schemas → Task 3
- Frontend list + detail pages → Tasks 13, 14
- SpanTree + collapsible LLM cards → Tasks 14, 15
- URL-state filters → Task 13
- `?span=` preselect → Task 14
- 503 when `tempo.query_endpoint` is null → Tasks 9, 10, 13 (disabled empty state)
- Span kind taxonomy (agent/turn/chat/tool/other) → Tasks 3, 4

**Out-of-scope items kept out:** no debugger, no cross-org view, no Loki/Grafana deep-link, no Postgres index, no copy of cubetrace source, no cursor pagination (Tempo has no native cursor — documented in spec), no duration sort (deferred). ✓

**Placeholder scan:** Task 4 now ships a single complete parser implementation — no `NotImplementedError` placeholder, no orphan `_build_summary`. The `_extract_llm` / `_extract_tool` stubs are explicit: they return enough for tree-shape tests in Task 4 and get fleshed out in Tasks 5 / 6.

**Type consistency:** `LlmCallPayload`, `ToolCallPayload`, `TurnPayload`, `SpanNode`, `TraceSummary`, `TraceDetail` field names are identical between `schemas/trace.py` (Task 3) and `types.ts` (Task 12). `TempoClient.search` / `tag_values` keyword args (Tasks 7, 8) match `list_traces` / `get_tag_values` query params (Task 10). `get_tempo_client` factory (Task 9) is referenced by Task 10's `_client_or_503` and overridden in Task 10's `fake_tempo` fixture via `monkeypatch.setattr`.

**Security / leak posture:** every TraceQL value is escape-checked (`_quote_traceql`); Tempo upstream error bodies are logged server-side but the admin response is a constant `"Upstream trace store error"`; `cubepi.llm.raw_request` / `raw_response` are gated behind org-admin + double-org-check.
