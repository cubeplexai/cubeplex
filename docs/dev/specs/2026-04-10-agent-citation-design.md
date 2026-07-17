# Agent Citation System Design

## Overview

A citation system that allows the cubeplex agent to reliably mark inline references in its output, sourced from tool call results. Citations are collected and standardized at the middleware layer, keeping tools and the agent graph decoupled from citation logic.

## Goals

- Agent can reference specific passages from tool results using `【N-M】` markers
- Citations work across both casual conversation and deep research reports
- Adding citation support for a new tool requires only a config change (no code)
- Citation IDs are unique and incrementing within a conversation session
- Main agent and subagents share a unified numbering scheme

## Non-Goals

- Multi-stage research pipelines (researcher → digest → reporter) like cubemanus
- Evidence authority tiering (T0/T1/T2/T3)
- Semantic chunking or embedding-based retrieval
- Citation verification / hallucination firewall

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                   Agent Graph                        │
│                                                      │
│  Tool returns ToolMessage                            │
│       ↓                                              │
│  CitationMiddleware.awrap_tool_call()                │
│       │                                              │
│       ├─→ Look up tool's citation_metadata_mapping   │
│       │   (no mapping → pass through unchanged)      │
│       │                                              │
│       ├─→ Extract metadata from tool output JSON     │
│       │   via declarative mapping                    │
│       ├─→ Chunk text (paragraph → sentence → fixed)  │
│       ├─→ CitationCounter assigns session-level IDs  │
│       │                                              │
│       ├─→ Rewrite ToolMessage.content                │
│       │   (inject 【N-M】 markers for LLM)            │
│       │   Store original in additional_kwargs        │
│       │                                              │
│       └─→ Push citation SSE event via ContextVar     │
│           event queue (metadata for frontend)        │
│                                                      │
│  CitationMiddleware.awrap_model_call()               │
│       └─→ Inject CITATION_PROMPT into system message │
│                                                      │
│  LLM sees numbered ToolMessage → generates text      │
│  with 【N-M】 markers                                 │
│       ↓                                              │
│  SSE stream: tool_result (original content)          │
│            + citation (metadata for frontend)        │
│            + text_delta (with 【N-M】 markers)         │
└─────────────────────────────────────────────────────┘
```

### Data Flow Summary

1. Tool executes, returns raw result
2. CitationMiddleware intercepts the ToolMessage
3. Checks if the tool has `citation` config — if not, passes through unchanged
4. Parses tool output JSON, extracts result items via `content_field`
5. For each item: extracts metadata via `mapping`, chunks the text, assigns citation IDs
6. Rewrites ToolMessage.content with `【N-M】` prefixed chunks (for LLM)
7. Stores original content in `ToolMessage.additional_kwargs["original_content"]` (for frontend)
8. Pushes `citation` SSE events via ContextVar event queue (for frontend)
9. LLM generates response with inline `【N-M】` references
10. SSE event generator buffers incomplete `【...】` markers before sending to frontend

---

## Data Models

### Citation SSE Event

New event type `citation`, emitted by CitationMiddleware after processing a tool result:

```python
class CitationEvent(AgentEvent):
    type: Literal["citation"] = "citation"
    # data schema:
    # {
    #     "citation_id": int,           # Session-level incrementing ID (e.g., 3)
    #     "chunks": [                   # All chunks for this source
    #         {
    #             "chunk_index": int,   # 0-based index within this source
    #             "content": str,       # Chunk text (200-300 chars)
    #         }
    #     ],
    #     "metadata": {                 # Extracted via tool's mapping config
    #         "source_type": str,       # "web", "file", "api", etc.
    #         "url": str | None,
    #         "title": str | None,
    #         "domain": str | None,
    #         "published_at": str | None,
    #     },
    #     "tool_call_id": str,          # From ToolMessage context
    # }
```

One `citation` event per source. A single tool call (e.g., `web_search`) may produce multiple citation events if it returns multiple results.

LLM text references use `【citation_id-chunk_index】` format, e.g., `【3-0】`, `【3-1】`.

### Citation Metadata Schema

Unified schema for all source types. All fields except `source_type` are optional to accommodate diverse tools:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `source_type` | string | yes | Source type: `web`, `file`, `api`, etc. |
| `url` | string | no | Source URL |
| `title` | string | no | Source title |
| `domain` | string | no | Domain name |
| `published_at` | string | no | Publication date |

This schema is intentionally minimal. New fields can be added as new tool types are supported — the frontend should tolerate unknown fields gracefully.

### Tool Result Preservation

When CitationMiddleware rewrites a ToolMessage, the original content is preserved:

```python
result.additional_kwargs["original_content"] = raw_content  # For frontend display
result.content = "【1-0】 chunk text...\n\n【1-1】 chunk text..."  # For LLM
```

In `stream.py`'s `_extract_tool_events`, the `tool_result` SSE event uses `original_content` when available, so the frontend always sees the raw tool output for preview rendering.

---

## Tool Configuration

Citation support is declared per-tool in `config.yaml` via the existing MCP tool config:

```yaml
mcp:
  enabled: true
  servers:
    web-tools:
      transport: streamable_http
      url: "http://..."
      tools:
        - name: web_search
          content_type: text
          citation:
            source_type: web
            content_field: "results"    # JSON path to result array
            mapping:                     # tool output field → citation metadata field
              url: "link"
              title: "title"
              domain: "domain"
              snippet: "snippet"         # Text field to chunk
              published_at: "date"
        - name: web_fetch
          content_type: text
          citation:
            source_type: web
            content_field: null          # Entire output is the content
            mapping:
              url: "url"
              title: "title"
```

### Config Fields

- `citation.source_type`: The `source_type` value in citation metadata. Set directly in config, not mapped from tool output.
- `citation.content_field`: JSON path to the result array in tool output. `null` means the entire output is a single result.
- `citation.mapping`: Maps citation metadata field names (left) to tool output JSON field names (right).
  - The special key `snippet` indicates which field contains the text to be chunked. If absent, the entire content string is chunked.

### Adding a New Tool

To add citation support for a new tool, only add its `citation` block to config.yaml:

```yaml
        - name: file_read
          citation:
            source_type: file
            content_field: null
            mapping:
              title: "file_name"
              snippet: "content"
```

No code changes required.

---

## CitationMiddleware

### File Structure

```
cubeplex/middleware/
├── citations/
│   ├── __init__.py          # Export CitationMiddleware
│   ├── middleware.py         # CitationMiddleware class
│   ├── counter.py           # CitationCounter + ContextVar
│   ├── chunker.py           # chunk_text() pure function
│   └── config.py            # CitationConfig pydantic model, parsed from config.yaml
```

### CitationCounter

Session-level incrementing counter shared between main agent and subagents:

```python
class CitationCounter:
    """Thread-safe session-level citation ID counter."""
    def __init__(self, start: int = 1):
        self._next = start
        self._lock = asyncio.Lock()

    async def next(self) -> int:
        async with self._lock:
            val = self._next
            self._next += 1
            return val

# Shared via ContextVar — subagents inherit from parent coroutine
citation_counter_var: ContextVar[CitationCounter | None] = ContextVar(
    "citation_counter", default=None
)
```

### Cross-Turn Recovery

At the start of each `astream()` call (in the `send_message` route), before creating the CitationCounter:

1. Load thread messages from checkpointer
2. Scan all messages for `【(\d+)-\d+】` pattern
3. Find max citation_id
4. Create `CitationCounter(start=max_id + 1)`
5. Set it in `citation_counter_var`

This avoids modifying LangGraph's state schema. The scan cost is negligible since it's a regex over existing message strings that are already loaded for the conversation.

### Subagent Integration

No special handling required:

- `ContextVar` is automatically inherited by child coroutines in asyncio
- Subagent's `_run_subagent` runs in the same event loop
- CitationMiddleware must be included in subagent's middleware stack — this is done in `create_cubeplex_agent` by adding CitationMiddleware before SubAgentMiddleware, and passing it through to subagent creation

The subagent already uses `subagent_event_queue` ContextVar for forwarding SSE events. Citation events follow the same path.

### Middleware Methods

**`awrap_tool_call`**: Intercepts tool results, performs chunking/numbering/event-pushing.

**`awrap_model_call`**: Injects `CITATION_PROMPT` into system message. Only injects when at least one tool has citation config (avoids polluting the prompt when citations are not in use).

---

## Text Chunker

Pure function, no external dependencies.

### Strategy

Three-level fallback: paragraph → sentence → fixed character limit.

Target chunk size: 200-300 characters.

```
Input text
  ↓
Split by \n\n into paragraphs
  ↓
For each paragraph:
  ├─ len ≤ max_size (300) → one chunk
  ├─ len > max_size → split by sentence boundaries (。！？.!?\n)
  │     For each sentence:
  │       ├─ accumulated len ≤ max_size → keep accumulating
  │       ├─ accumulated len > max_size → flush as chunk
  │       └─ single sentence > max_size → hard split at max_size
  ↓
Merge short chunks:
  If chunk < min_size (200) and not last → merge with next
```

### Sentence Boundaries

Supports both Chinese and English punctuation: `。！？.!?\n`

### Edge Cases

- Empty / whitespace-only text: return empty list, no citation produced
- Total length < min_size: return single chunk
- Hard split respects character boundaries (Python str guarantees no UTF-8 breakage)

### Not Doing

- No semantic splitting (no NLP libraries)
- No overlapping windows (this is citation positioning, not RAG indexing)
- Stateless pure function, easy to unit test

---

## Prompt Engineering

### Citation Prompt

Injected by `CitationMiddleware.awrap_model_call` when citation-enabled tools are configured:

```
## Citation Rules

When your response uses information from tool results that contain citation markers
like 【N-M】, you MUST follow these rules:

1. **Citation syntax**: Use 【N-M】 format only. N is the source number, M is the chunk
   index. Example: 【3-0】, 【3-1】. Do NOT use other formats like [1], (source 1),
   markdown links, or footnotes.

2. **Inline placement**: Place citations immediately after the fact they support.
   Example: "The revenue grew 15% in Q3 【2-0】 while costs decreased 【2-1】【3-0】."

3. **Preserve original IDs**: Never renumber citations. If the tool result says 【5-2】,
   use 【5-2】 exactly. Renumbering breaks frontend reference linking.

4. **Multiple sources**: When a fact is supported by multiple chunks, list them
   consecutively: 【1-0】【2-1】【3-0】

5. **No citation needed**: For your own reasoning, general knowledge, or conversation
   context, do NOT add citations. Only cite tool results.

6. **No separate references section**: Do NOT add a "References" or "Sources" list at
   the end. Citations are inline only.
```

### Design Decisions

- Single prompt, not multi-stage (cubeplex is a general-purpose agent, not a research pipeline)
- No evidence tiering — unnecessary complexity for general use
- No hallucination firewall rules — keep prompt concise for higher LLM compliance
- Only injected when citation tools are configured

---

## SSE Stream Buffering

### Problem

LLM streams `text_delta` token by token. A citation marker `【3-1】` may be split across chunks:

```
chunk1: "增长了15%【"
chunk2: "3-1】，同时"
```

Frontend would briefly render an incomplete `【`, degrading UX.

### Solution

Buffer in the SSE event generator layer (not middleware):

```python
# In conversations.py event_generator
buffer = ""

for event in events:
    if event["type"] != "text_delta":
        if buffer:  # flush before non-text events
            yield text_delta_event(buffer)
            buffer = ""
        yield event
        continue

    content = buffer + event["data"]["content"]
    buffer = ""

    # Check for unclosed 【 that might be a citation marker
    last_bracket = content.rfind("【")
    if last_bracket != -1 and "】" not in content[last_bracket:]:
        buffer = content[last_bracket:]
        content = content[:last_bracket]

    if content:
        event["data"]["content"] = content
        yield event

# Flush remaining buffer at stream end
if buffer:
    yield text_delta_event(buffer)
```

### Why This Works

- `【` almost never appears in normal LLM output — near-zero false positives
- Buffer duration is 1-2 chunks (tens of milliseconds), imperceptible to users
- Logic lives in the event generator, not in middleware — clean separation

---

## Integration Points

### graph.py — Middleware Registration

Add CitationMiddleware to the stack in `create_cubeplex_agent()`:

```python
# After TimestampMiddleware, before other middleware
citation_configs = load_citation_configs()  # From config.yaml
if citation_configs:
    middleware.append(CitationMiddleware(citation_configs=citation_configs))
```

### stream.py — Preserve Original Content

In `_extract_tool_events`, use `original_content` from `additional_kwargs` for the `tool_result` SSE event:

```python
original = additional_kwargs.get("original_content")
content_for_sse = original if original else content
```

### conversations.py — Counter Initialization + Event Queue

In `send_message` route, before `astream()`:

1. Scan thread history for max citation_id
2. Create and set `CitationCounter` in ContextVar
3. Set `citation_event_queue` ContextVar to the same `event_q` used by the SSE generator

Citation events are pushed from inside `awrap_tool_call` (which runs within the `astream()` loop) to the unified event queue via a new ContextVar:

```python
# cubeplex/middleware/citations/counter.py
citation_event_queue: ContextVar[asyncio.Queue | None] = ContextVar(
    "citation_event_queue", default=None
)
```

The event generator handles a new queue item kind `"citation"`:

```python
# In the event_generator while loop:
elif kind == "citation":
    agent_id, citation_data = item[1], item[2]
    citation_event = CitationEvent(
        timestamp=datetime.now(UTC).isoformat(),
        data=citation_data,
        agent_id=agent_id,
    )
    yield f"data: {citation_event.model_dump_json()}\n\n"
```

This reuses the existing unified queue pattern (same as subagent events) without introducing a separate channel.

### schemas.py — New Event Type

Add `CitationEvent` class to the schema definitions.

### subagents.py — Pass Through Middleware

Ensure CitationMiddleware is included when creating subagent middleware stacks.

---

## File Changes Summary

| File | Change |
|------|--------|
| `cubeplex/middleware/citations/__init__.py` | New — export CitationMiddleware |
| `cubeplex/middleware/citations/middleware.py` | New — CitationMiddleware class |
| `cubeplex/middleware/citations/counter.py` | New — CitationCounter + ContextVar |
| `cubeplex/middleware/citations/chunker.py` | New — chunk_text() |
| `cubeplex/middleware/citations/config.py` | New — CitationConfig model |
| `cubeplex/prompts/citations.py` | New — CITATION_PROMPT |
| `cubeplex/agents/schemas.py` | Modify — add CitationEvent |
| `cubeplex/agents/graph.py` | Modify — add CitationMiddleware to stack |
| `cubeplex/agents/stream.py` | Modify — use original_content for tool_result SSE |
| `cubeplex/api/routes/v1/conversations.py` | Modify — counter init + stream buffering |
| `cubeplex/middleware/subagents.py` | Modify — pass CitationMiddleware to subagents |
| `config.yaml` | Modify — add citation config example |
