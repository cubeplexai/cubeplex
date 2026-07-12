# Agent Citation System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a CitationMiddleware that chunks tool results, assigns session-level unique IDs, and enables inline `【N-M】` citation markers in agent output.

**Architecture:** A new `CitationMiddleware` intercepts tool results via `awrap_tool_call`, chunks text, assigns IDs from a shared `CitationCounter` (ContextVar), rewrites ToolMessage for LLM consumption, and pushes `citation` SSE events to the frontend via the existing unified event queue. System prompt injection via `awrap_model_call` instructs the LLM to use `【N-M】` markers.

**Tech Stack:** Python 3.12, FastAPI, LangGraph, LangChain middleware, Pydantic, pytest

**Spec:** `docs/superpowers/specs/2026-04-10-agent-citation-design.md`

---

## File Structure

### New Files

| File | Responsibility |
|------|----------------|
| `cubeplex/middleware/citations/__init__.py` | Export `CitationMiddleware`, `CitationCounter`, `citation_counter_var`, `citation_event_queue` |
| `cubeplex/middleware/citations/config.py` | `CitationConfig` Pydantic model parsed from config.yaml per-tool citation blocks |
| `cubeplex/middleware/citations/counter.py` | `CitationCounter` (async lock + incrementing int) and two ContextVars |
| `cubeplex/middleware/citations/chunker.py` | `chunk_text()` pure function: paragraph → sentence → fixed-char splitting |
| `cubeplex/middleware/citations/middleware.py` | `CitationMiddleware` class with `awrap_tool_call` and `awrap_model_call` |
| `cubeplex/prompts/citations.py` | `CITATION_PROMPT` constant |
| `tests/unit/test_chunker.py` | Unit tests for `chunk_text()` |
| `tests/unit/test_citation_config.py` | Unit tests for `CitationConfig` parsing and metadata extraction |
| `tests/unit/test_citation_counter.py` | Unit tests for `CitationCounter` |
| `tests/unit/test_citation_middleware.py` | Unit tests for `CitationMiddleware` |

### Modified Files

| File | Change |
|------|--------|
| `cubeplex/agents/schemas.py` | Add `CitationEvent` class |
| `cubeplex/agents/stream.py:152-170` | Use `original_content` from `additional_kwargs` for `tool_result` SSE |
| `cubeplex/agents/graph.py:23-88` | Add `CitationMiddleware` to middleware stack, accept `citation_configs` param |
| `cubeplex/api/routes/v1/conversations.py:238-452` | Init counter ContextVar, handle `"citation"` queue items, add stream buffering |
| `config.yaml:124-127` | Add example citation config under `mcp.servers` |

---

### Task 1: Text Chunker

**Files:**
- Create: `cubeplex/middleware/citations/__init__.py`
- Create: `cubeplex/middleware/citations/chunker.py`
- Test: `tests/unit/test_chunker.py`

- [ ] **Step 1: Write failing tests for chunk_text()**

```python
# tests/unit/test_chunker.py
import pytest

from cubeplex.middleware.citations.chunker import chunk_text


class TestChunkText:
    def test_empty_string_returns_empty_list(self):
        assert chunk_text("") == []

    def test_whitespace_only_returns_empty_list(self):
        assert chunk_text("   \n\n  ") == []

    def test_short_text_returns_single_chunk(self):
        text = "This is a short sentence."
        result = chunk_text(text)
        assert len(result) == 1
        assert result[0] == text

    def test_text_under_min_size_returns_single_chunk(self):
        text = "A" * 150
        result = chunk_text(text, min_size=200, max_size=300)
        assert len(result) == 1

    def test_splits_by_paragraph(self):
        para1 = "A" * 250
        para2 = "B" * 250
        text = f"{para1}\n\n{para2}"
        result = chunk_text(text, min_size=200, max_size=300)
        assert len(result) == 2
        assert result[0] == para1
        assert result[1] == para2

    def test_long_paragraph_splits_by_sentence_chinese(self):
        # 3 Chinese sentences, each ~120 chars → first two merge, third is separate
        s1 = "这是第一个句子" + "内容" * 55 + "。"
        s2 = "这是第二个句子" + "内容" * 55 + "。"
        s3 = "这是第三个句子" + "内容" * 55 + "。"
        text = s1 + s2 + s3
        result = chunk_text(text, min_size=200, max_size=300)
        assert len(result) >= 2
        for chunk in result:
            assert len(chunk) <= 300

    def test_long_paragraph_splits_by_sentence_english(self):
        s1 = "First sentence. "
        s2 = "Second sentence. "
        # Build text > 300 chars
        text = s1 * 10 + s2 * 10
        result = chunk_text(text, min_size=200, max_size=300)
        assert len(result) >= 2
        for chunk in result:
            assert len(chunk) <= 300

    def test_very_long_sentence_hard_splits(self):
        # Single sentence with no punctuation, > 300 chars
        text = "A" * 700
        result = chunk_text(text, min_size=200, max_size=300)
        assert len(result) >= 3
        for chunk in result:
            assert len(chunk) <= 300

    def test_short_chunks_merged(self):
        # Three short paragraphs that should merge
        text = "Short one.\n\nShort two.\n\nShort three."
        result = chunk_text(text, min_size=200, max_size=300)
        # All are short, should merge into one chunk
        assert len(result) == 1
        assert "Short one." in result[0]
        assert "Short three." in result[0]

    def test_mixed_paragraphs(self):
        short = "Short paragraph."
        long = "X" * 280
        text = f"{short}\n\n{long}"
        result = chunk_text(text, min_size=200, max_size=300)
        # short merges with long if combined ≤ max_size, else separate
        total = len(short) + 1 + len(long)  # +1 for join separator
        if total <= 300:
            assert len(result) == 1
        else:
            assert len(result) == 2

    def test_respects_custom_sizes(self):
        text = "Word. " * 100  # ~600 chars
        result = chunk_text(text, min_size=100, max_size=150)
        for chunk in result:
            assert len(chunk) <= 150

    def test_sentence_boundaries_include_all_punctuation(self):
        text = "Sentence one。Sentence two！Sentence three？Sentence four.Sentence five!"
        # Each ~14 chars, all should be reachable as split points
        result = chunk_text(text, min_size=10, max_size=30)
        assert len(result) >= 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_chunker.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cubeplex.middleware.citations'`

- [ ] **Step 3: Create package init and implement chunk_text()**

```python
# cubeplex/middleware/citations/__init__.py
"""Citation middleware for inline reference tracking."""
```

```python
# cubeplex/middleware/citations/chunker.py
"""Text chunking for citation references.

Splits text into 200-300 character chunks using a three-level fallback:
paragraph boundaries → sentence boundaries → fixed character limit.
"""

import re

# Sentence-ending punctuation (Chinese + English)
_SENTENCE_RE = re.compile(r"(?<=[。！？.!?\n])")


def _split_sentences(text: str) -> list[str]:
    """Split text by sentence boundaries, keeping delimiters attached."""
    parts = _SENTENCE_RE.split(text)
    return [p for p in parts if p.strip()]


def _hard_split(text: str, max_size: int) -> list[str]:
    """Split text into chunks of at most max_size characters."""
    return [text[i : i + max_size] for i in range(0, len(text), max_size)]


def chunk_text(
    text: str,
    *,
    min_size: int = 200,
    max_size: int = 300,
) -> list[str]:
    """Split text into chunks targeting min_size..max_size characters.

    Strategy:
    1. Split by paragraph (\\n\\n)
    2. Oversized paragraphs split by sentence boundaries
    3. Oversized sentences split by fixed character limit
    4. Undersized chunks merged with the next chunk

    Args:
        text: Input text to chunk.
        min_size: Minimum desired chunk size in characters.
        max_size: Maximum chunk size in characters.

    Returns:
        List of text chunks. Empty list if text is empty/whitespace.
    """
    text = text.strip()
    if not text:
        return []

    # Step 1: split by paragraph
    paragraphs = [p.strip() for p in re.split(r"\n\n+", text) if p.strip()]

    # Step 2: split oversized paragraphs by sentence, then hard-split
    raw_chunks: list[str] = []
    for para in paragraphs:
        if len(para) <= max_size:
            raw_chunks.append(para)
            continue

        # Split by sentence boundaries
        sentences = _split_sentences(para)
        if len(sentences) <= 1:
            # No sentence boundaries found — hard split
            raw_chunks.extend(_hard_split(para, max_size))
            continue

        # Accumulate sentences into chunks
        current = ""
        for sentence in sentences:
            if len(sentence) > max_size:
                # Flush current accumulator
                if current:
                    raw_chunks.append(current)
                    current = ""
                # Hard-split the oversized sentence
                raw_chunks.extend(_hard_split(sentence, max_size))
            elif len(current) + len(sentence) > max_size:
                # Adding this sentence would exceed max — flush
                if current:
                    raw_chunks.append(current)
                current = sentence
            else:
                current += sentence
        if current:
            raw_chunks.append(current)

    # Step 3: merge undersized chunks
    merged: list[str] = []
    for chunk in raw_chunks:
        if merged and len(merged[-1]) < min_size:
            merged[-1] = merged[-1] + "\n" + chunk
        else:
            merged.append(chunk)

    # Final pass: if the last chunk is too small, merge it back
    if len(merged) > 1 and len(merged[-1]) < min_size:
        merged[-2] = merged[-2] + "\n" + merged[-1]
        merged.pop()

    return merged
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_chunker.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add cubeplex/middleware/citations/__init__.py cubeplex/middleware/citations/chunker.py tests/unit/test_chunker.py
git commit -m "feat(citations): add text chunker with paragraph/sentence/fixed splitting"
```

---

### Task 2: CitationConfig Model

**Files:**
- Create: `cubeplex/middleware/citations/config.py`
- Test: `tests/unit/test_citation_config.py`

- [ ] **Step 1: Write failing tests for CitationConfig**

```python
# tests/unit/test_citation_config.py
import pytest

from cubeplex.middleware.citations.config import CitationConfig, load_citation_configs


class TestCitationConfig:
    def test_basic_config(self):
        cfg = CitationConfig(
            source_type="web",
            content_field="results",
            mapping={"url": "link", "title": "title", "snippet": "snippet"},
        )
        assert cfg.source_type == "web"
        assert cfg.content_field == "results"
        assert cfg.mapping["url"] == "link"

    def test_content_field_none(self):
        cfg = CitationConfig(
            source_type="web",
            content_field=None,
            mapping={"url": "url", "title": "title"},
        )
        assert cfg.content_field is None

    def test_extract_metadata_from_item(self):
        cfg = CitationConfig(
            source_type="web",
            content_field="results",
            mapping={"url": "link", "title": "name", "snippet": "body"},
        )
        item = {"link": "https://example.com", "name": "Example", "body": "Content here"}
        metadata = cfg.extract_metadata(item)
        assert metadata["source_type"] == "web"
        assert metadata["url"] == "https://example.com"
        assert metadata["title"] == "Example"
        # snippet is not included in metadata — it's used for chunking only
        assert "snippet" not in metadata

    def test_extract_metadata_missing_fields(self):
        cfg = CitationConfig(
            source_type="web",
            content_field=None,
            mapping={"url": "link", "title": "name"},
        )
        item = {"link": "https://example.com"}  # no "name" field
        metadata = cfg.extract_metadata(item)
        assert metadata["url"] == "https://example.com"
        assert metadata.get("title") is None

    def test_extract_text_from_snippet_field(self):
        cfg = CitationConfig(
            source_type="web",
            content_field="results",
            mapping={"url": "link", "snippet": "body"},
        )
        item = {"link": "https://example.com", "body": "The actual text content"}
        text = cfg.extract_text(item)
        assert text == "The actual text content"

    def test_extract_text_no_snippet_uses_str(self):
        cfg = CitationConfig(
            source_type="web",
            content_field=None,
            mapping={"url": "link"},
        )
        item = {"link": "https://example.com", "content": "Fallback text"}
        text = cfg.extract_text(item)
        # Without snippet mapping, uses str(item)
        assert "Fallback text" in text

    def test_extract_items_from_array_field(self):
        cfg = CitationConfig(
            source_type="web",
            content_field="results",
            mapping={"url": "link"},
        )
        data = {"results": [{"link": "a"}, {"link": "b"}]}
        items = cfg.extract_items(data)
        assert len(items) == 2

    def test_extract_items_null_content_field(self):
        cfg = CitationConfig(
            source_type="web",
            content_field=None,
            mapping={"url": "link"},
        )
        data = {"link": "https://example.com", "text": "content"}
        items = cfg.extract_items(data)
        assert len(items) == 1
        assert items[0] is data


class TestLoadCitationConfigs:
    def test_load_from_mcp_tool_configs(self):
        tool_defs = [
            {
                "name": "web_search",
                "citation": {
                    "source_type": "web",
                    "content_field": "results",
                    "mapping": {"url": "link", "title": "title"},
                },
            },
            {
                "name": "calculator",
                # no citation block
            },
        ]
        configs = load_citation_configs(tool_defs)
        assert "web_search" in configs
        assert "calculator" not in configs
        assert configs["web_search"].source_type == "web"

    def test_load_empty_returns_empty(self):
        assert load_citation_configs([]) == {}
        assert load_citation_configs(None) == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_citation_config.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement CitationConfig**

```python
# cubeplex/middleware/citations/config.py
"""Citation configuration model.

Parses per-tool citation config from config.yaml and provides
methods to extract metadata and text from tool output.
"""

from typing import Any

from pydantic import BaseModel

# Metadata fields that map from tool output to citation metadata.
# "snippet" is special — it identifies the text field to chunk,
# and is NOT included in the citation metadata sent to frontend.
_SNIPPET_KEY = "snippet"


class CitationConfig(BaseModel):
    """Per-tool citation configuration.

    Attributes:
        source_type: Citation source type (e.g., "web", "file").
        content_field: JSON path to result array in tool output.
                       None means the entire output is a single result.
        mapping: Maps citation metadata field names to tool output field names.
                 The special key "snippet" identifies the text field to chunk.
    """

    source_type: str
    content_field: str | None
    mapping: dict[str, str]

    def extract_metadata(self, item: dict[str, Any]) -> dict[str, Any]:
        """Extract citation metadata from a single result item.

        Applies the mapping to pull values from the item dict.
        The "snippet" key is excluded — it's used for chunking only.

        Returns:
            Dict with source_type plus mapped metadata fields.
        """
        metadata: dict[str, Any] = {"source_type": self.source_type}
        for meta_key, item_key in self.mapping.items():
            if meta_key == _SNIPPET_KEY:
                continue
            value = item.get(item_key)
            if value is not None:
                metadata[meta_key] = value
        return metadata

    def extract_text(self, item: dict[str, Any]) -> str:
        """Extract the text content to be chunked from a result item.

        Uses the "snippet" mapping key if present, otherwise str(item).
        """
        snippet_field = self.mapping.get(_SNIPPET_KEY)
        if snippet_field:
            return str(item.get(snippet_field, ""))
        return str(item)

    def extract_items(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        """Extract the list of result items from parsed tool output.

        If content_field is set, looks up that key in data (expects a list).
        If content_field is None, wraps the entire data dict as a single item.
        """
        if self.content_field is None:
            return [data]
        items = data.get(self.content_field, [])
        if not isinstance(items, list):
            return [items] if items else []
        return items


def load_citation_configs(
    tool_defs: list[dict[str, Any]] | None,
) -> dict[str, CitationConfig]:
    """Build tool_name → CitationConfig mapping from MCP tool definitions.

    Only tools with a "citation" block are included.

    Args:
        tool_defs: List of tool config dicts from config.yaml mcp.servers.*.tools

    Returns:
        Dict mapping tool name to CitationConfig.
    """
    if not tool_defs:
        return {}
    configs: dict[str, CitationConfig] = {}
    for td in tool_defs:
        if not isinstance(td, dict):
            continue
        name = td.get("name")
        citation = td.get("citation")
        if name and isinstance(citation, dict):
            configs[str(name)] = CitationConfig(**citation)
    return configs
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_citation_config.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add cubeplex/middleware/citations/config.py tests/unit/test_citation_config.py
git commit -m "feat(citations): add CitationConfig model with metadata extraction"
```

---

### Task 3: CitationCounter and ContextVars

**Files:**
- Create: `cubeplex/middleware/citations/counter.py`
- Test: `tests/unit/test_citation_counter.py`

- [ ] **Step 1: Write failing tests for CitationCounter**

```python
# tests/unit/test_citation_counter.py
import asyncio

import pytest

from cubeplex.middleware.citations.counter import (
    CitationCounter,
    citation_counter_var,
    citation_event_queue,
)


class TestCitationCounter:
    async def test_starts_at_1_by_default(self):
        counter = CitationCounter()
        assert await counter.next() == 1

    async def test_increments(self):
        counter = CitationCounter()
        assert await counter.next() == 1
        assert await counter.next() == 2
        assert await counter.next() == 3

    async def test_custom_start(self):
        counter = CitationCounter(start=10)
        assert await counter.next() == 10
        assert await counter.next() == 11

    async def test_concurrent_safety(self):
        counter = CitationCounter()
        results: list[int] = []

        async def grab():
            val = await counter.next()
            results.append(val)

        await asyncio.gather(*[grab() for _ in range(100)])
        assert sorted(results) == list(range(1, 101))


class TestContextVars:
    def test_citation_counter_var_default_none(self):
        assert citation_counter_var.get() is None

    def test_citation_event_queue_default_none(self):
        assert citation_event_queue.get() is None

    async def test_counter_var_set_and_get(self):
        counter = CitationCounter(start=5)
        token = citation_counter_var.set(counter)
        try:
            retrieved = citation_counter_var.get()
            assert retrieved is counter
            assert await retrieved.next() == 5
        finally:
            citation_counter_var.reset(token)

    async def test_event_queue_set_and_get(self):
        queue: asyncio.Queue[tuple[str, ...]] = asyncio.Queue()
        token = citation_event_queue.set(queue)
        try:
            assert citation_event_queue.get() is queue
        finally:
            citation_event_queue.reset(token)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_citation_counter.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement CitationCounter**

```python
# cubeplex/middleware/citations/counter.py
"""Session-level citation ID counter with ContextVar sharing.

The counter is created per-request in the SSE event generator and shared
between the main agent and any subagents via ContextVar inheritance.
"""

import asyncio
from contextvars import ContextVar
from typing import Any


class CitationCounter:
    """Thread-safe incrementing citation ID counter.

    Uses asyncio.Lock to ensure safe concurrent access from
    the main agent and subagents within the same event loop.
    """

    def __init__(self, start: int = 1) -> None:
        self._next = start
        self._lock = asyncio.Lock()

    async def next(self) -> int:
        """Return the next citation ID and increment the counter."""
        async with self._lock:
            val = self._next
            self._next += 1
            return val


# Set per-request in the SSE event generator. Read by CitationMiddleware
# in both main agent and subagent contexts.
citation_counter_var: ContextVar[CitationCounter | None] = ContextVar(
    "citation_counter", default=None
)

# Queue for pushing citation events to the SSE generator.
# Reuses the same pattern as subagent_event_queue.
citation_event_queue: ContextVar[asyncio.Queue[Any] | None] = ContextVar(
    "citation_event_queue", default=None
)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_citation_counter.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add cubeplex/middleware/citations/counter.py tests/unit/test_citation_counter.py
git commit -m "feat(citations): add CitationCounter with ContextVar sharing"
```

---

### Task 4: CitationEvent Schema

**Files:**
- Modify: `cubeplex/agents/schemas.py:105-116`

- [ ] **Step 1: Add CitationEvent to schemas.py**

Add after `StatusEvent` class (line 116):

```python
class CitationEvent(AgentEvent):
    """Citation reference event.

    Emitted when CitationMiddleware processes a tool result that has
    citation configuration. Contains source metadata and text chunks
    for frontend rendering of inline 【N-M】 references.
    """

    type: Literal["citation"] = "citation"
    data: dict[str, Any] = Field(
        description=(
            "Citation data: citation_id, chunks [{chunk_index, content}], "
            "metadata {source_type, url, title, ...}, tool_call_id"
        )
    )
```

- [ ] **Step 2: Run type check**

Run: `uv run mypy cubeplex/agents/schemas.py`
Expected: Success

- [ ] **Step 3: Commit**

```bash
git add cubeplex/agents/schemas.py
git commit -m "feat(citations): add CitationEvent SSE schema"
```

---

### Task 5: Citation Prompt

**Files:**
- Create: `cubeplex/prompts/citations.py`

- [ ] **Step 1: Create the citation prompt module**

```python
# cubeplex/prompts/citations.py
"""System prompt for citation behavior."""

CITATION_PROMPT = """## Citation Rules

When your response uses information from tool results that contain citation markers like 【N-M】, you MUST follow these rules:

1. **Citation syntax**: Use 【N-M】 format only. N is the source number, M is the chunk index. Example: 【3-0】, 【3-1】. Do NOT use other formats like [1], (source 1), markdown links, or footnotes.

2. **Inline placement**: Place citations immediately after the fact they support. Example: "The revenue grew 15% in Q3 【2-0】 while costs decreased 【2-1】【3-0】."

3. **Preserve original IDs**: Never renumber citations. If the tool result says 【5-2】, use 【5-2】 exactly. Renumbering breaks frontend reference linking.

4. **Multiple sources**: When a fact is supported by multiple chunks, list them consecutively: 【1-0】【2-1】【3-0】

5. **No citation needed**: For your own reasoning, general knowledge, or conversation context, do NOT add citations. Only cite tool results that contain 【N-M】 markers.

6. **No separate references section**: Do NOT add a "References" or "Sources" list at the end. Citations are inline only."""  # noqa: E501
```

- [ ] **Step 2: Run lint and type check**

Run: `uv run ruff check cubeplex/prompts/citations.py && uv run mypy cubeplex/prompts/citations.py`
Expected: Success

- [ ] **Step 3: Commit**

```bash
git add cubeplex/prompts/citations.py
git commit -m "feat(citations): add citation system prompt"
```

---

### Task 6: CitationMiddleware

**Files:**
- Create: `cubeplex/middleware/citations/middleware.py`
- Modify: `cubeplex/middleware/citations/__init__.py`
- Test: `tests/unit/test_citation_middleware.py`

- [ ] **Step 1: Write failing tests for CitationMiddleware**

```python
# tests/unit/test_citation_middleware.py
import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock

import pytest
from langchain.agents.middleware.types import ModelRequest, ModelResponse, ToolCallRequest
from langchain.tools import ToolRuntime
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage

from cubeplex.middleware.citations.config import CitationConfig
from cubeplex.middleware.citations.counter import (
    CitationCounter,
    citation_counter_var,
    citation_event_queue,
)
from cubeplex.middleware.citations.middleware import CitationMiddleware


def _make_tool_call_request(
    tool_name: str,
    tool_call_id: str = "call_123",
) -> ToolCallRequest:
    """Build a minimal ToolCallRequest for testing."""
    return ToolCallRequest(
        tool_call={"name": tool_name, "args": {}, "id": tool_call_id},
        tool=None,
        state={"messages": []},
        runtime=ToolRuntime(
            config={"configurable": {}},
            store=None,
        ),
    )


@pytest.fixture()
def web_search_config() -> dict[str, CitationConfig]:
    return {
        "web_search": CitationConfig(
            source_type="web",
            content_field="results",
            mapping={
                "url": "link",
                "title": "title",
                "snippet": "snippet",
            },
        ),
    }


@pytest.fixture()
def _setup_counter_and_queue():
    """Set up CitationCounter and event queue ContextVars for testing."""
    counter = CitationCounter(start=1)
    queue: asyncio.Queue[Any] = asyncio.Queue()
    ct = citation_counter_var.set(counter)
    qt = citation_event_queue.set(queue)
    yield queue
    citation_counter_var.reset(ct)
    citation_event_queue.reset(qt)


class TestCitationMiddlewareToolCall:
    async def test_no_config_passes_through(self, _setup_counter_and_queue):
        mw = CitationMiddleware(citation_configs={})
        original = ToolMessage(content="raw output", tool_call_id="call_1", name="calculator")
        handler = AsyncMock(return_value=original)
        request = _make_tool_call_request("calculator")

        result = await mw.awrap_tool_call(request, handler)
        assert result.content == "raw output"

    async def test_unconfigured_tool_passes_through(
        self, web_search_config, _setup_counter_and_queue
    ):
        mw = CitationMiddleware(citation_configs=web_search_config)
        original = ToolMessage(content="42", tool_call_id="call_1", name="calculator")
        handler = AsyncMock(return_value=original)
        request = _make_tool_call_request("calculator")

        result = await mw.awrap_tool_call(request, handler)
        assert result.content == "42"

    async def test_configured_tool_rewrites_content(
        self, web_search_config, _setup_counter_and_queue
    ):
        tool_output = json.dumps({
            "results": [
                {"link": "https://a.com", "title": "A", "snippet": "Content about A."},
            ]
        })
        original = ToolMessage(content=tool_output, tool_call_id="call_1", name="web_search")
        handler = AsyncMock(return_value=original)
        request = _make_tool_call_request("web_search")

        mw = CitationMiddleware(citation_configs=web_search_config)
        result = await mw.awrap_tool_call(request, handler)

        # Content should be rewritten with citation markers
        assert "【1-0】" in result.content
        assert "Content about A." in result.content

    async def test_original_content_preserved(
        self, web_search_config, _setup_counter_and_queue
    ):
        tool_output = json.dumps({
            "results": [
                {"link": "https://a.com", "title": "A", "snippet": "Content."},
            ]
        })
        original = ToolMessage(content=tool_output, tool_call_id="call_1", name="web_search")
        handler = AsyncMock(return_value=original)
        request = _make_tool_call_request("web_search")

        mw = CitationMiddleware(citation_configs=web_search_config)
        result = await mw.awrap_tool_call(request, handler)

        assert result.additional_kwargs["original_content"] == tool_output

    async def test_citation_event_pushed_to_queue(
        self, web_search_config, _setup_counter_and_queue
    ):
        queue = _setup_counter_and_queue
        tool_output = json.dumps({
            "results": [
                {"link": "https://a.com", "title": "A", "snippet": "Some content."},
            ]
        })
        original = ToolMessage(content=tool_output, tool_call_id="call_1", name="web_search")
        handler = AsyncMock(return_value=original)
        request = _make_tool_call_request("web_search", tool_call_id="call_1")

        mw = CitationMiddleware(citation_configs=web_search_config)
        await mw.awrap_tool_call(request, handler)

        assert not queue.empty()
        item = queue.get_nowait()
        assert item[0] == "citation"
        citation_data = item[2]
        assert citation_data["citation_id"] == 1
        assert citation_data["metadata"]["source_type"] == "web"
        assert citation_data["metadata"]["url"] == "https://a.com"
        assert citation_data["tool_call_id"] == "call_1"

    async def test_multiple_results_get_different_ids(
        self, web_search_config, _setup_counter_and_queue
    ):
        queue = _setup_counter_and_queue
        tool_output = json.dumps({
            "results": [
                {"link": "https://a.com", "title": "A", "snippet": "Content A."},
                {"link": "https://b.com", "title": "B", "snippet": "Content B."},
            ]
        })
        original = ToolMessage(content=tool_output, tool_call_id="call_1", name="web_search")
        handler = AsyncMock(return_value=original)
        request = _make_tool_call_request("web_search")

        mw = CitationMiddleware(citation_configs=web_search_config)
        await mw.awrap_tool_call(request, handler)

        ids = []
        while not queue.empty():
            item = queue.get_nowait()
            ids.append(item[2]["citation_id"])
        assert ids == [1, 2]

    async def test_counter_continues_across_calls(
        self, web_search_config, _setup_counter_and_queue
    ):
        queue = _setup_counter_and_queue
        for i in range(2):
            tool_output = json.dumps({
                "results": [
                    {"link": f"https://{i}.com", "title": f"T{i}", "snippet": f"Content {i}."},
                ]
            })
            original = ToolMessage(
                content=tool_output, tool_call_id=f"call_{i}", name="web_search"
            )
            handler = AsyncMock(return_value=original)
            request = _make_tool_call_request("web_search", tool_call_id=f"call_{i}")

            mw = CitationMiddleware(citation_configs=web_search_config)
            await mw.awrap_tool_call(request, handler)

        ids = []
        while not queue.empty():
            item = queue.get_nowait()
            ids.append(item[2]["citation_id"])
        assert ids == [1, 2]


class TestCitationMiddlewareModelCall:
    async def test_injects_prompt_when_configs_exist(self, web_search_config):
        mw = CitationMiddleware(citation_configs=web_search_config)
        request = ModelRequest(
            messages=[],
            tools=[],
            system_message=SystemMessage(content="Base prompt"),
        )
        handler = AsyncMock(return_value=ModelResponse(result=[AIMessage(content="hi")]))
        await mw.awrap_model_call(request, handler)

        called_request = handler.call_args[0][0]
        system_content = called_request.system_message.content
        assert "Citation Rules" in system_content
        assert "【N-M】" in system_content

    async def test_no_injection_when_no_configs(self):
        mw = CitationMiddleware(citation_configs={})
        request = ModelRequest(
            messages=[],
            tools=[],
            system_message=SystemMessage(content="Base prompt"),
        )
        handler = AsyncMock(return_value=ModelResponse(result=[AIMessage(content="hi")]))
        await mw.awrap_model_call(request, handler)

        called_request = handler.call_args[0][0]
        system_content = called_request.system_message.content
        assert "Citation Rules" not in system_content

    async def test_no_tools_property(self, web_search_config):
        mw = CitationMiddleware(citation_configs=web_search_config)
        assert len(mw.tools) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_citation_middleware.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement CitationMiddleware**

```python
# cubeplex/middleware/citations/middleware.py
"""CitationMiddleware — chunks tool results and assigns citation IDs."""

import json
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from langchain.agents.middleware.types import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
    ToolCallRequest,
)
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.types import Command
from loguru import logger

from cubeplex.middleware._utils import append_to_system_message
from cubeplex.middleware.citations.chunker import chunk_text
from cubeplex.middleware.citations.config import CitationConfig
from cubeplex.middleware.citations.counter import citation_counter_var, citation_event_queue
from cubeplex.prompts.citations import CITATION_PROMPT


class CitationMiddleware(AgentMiddleware[Any, Any, Any]):
    """Intercepts tool results to chunk text and assign citation IDs.

    For tools with citation configuration:
    - Parses tool output and extracts result items
    - Chunks text into ~200-300 char segments
    - Assigns session-level incrementing citation IDs
    - Rewrites ToolMessage.content with 【N-M】 markers for LLM
    - Preserves original content in additional_kwargs for frontend
    - Pushes citation events to the SSE event queue

    For tools without citation configuration: passes through unchanged.
    """

    tools: Sequence[BaseTool] = []

    def __init__(self, *, citation_configs: dict[str, CitationConfig]) -> None:
        self._configs = citation_configs

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        result = await handler(request)

        if not isinstance(result, ToolMessage):
            return result

        tool_name = request.tool_call.get("name", "")
        config = self._configs.get(tool_name)
        if config is None:
            return result

        counter = citation_counter_var.get()
        if counter is None:
            logger.warning("CitationMiddleware: no CitationCounter in context, skipping")
            return result

        queue = citation_event_queue.get()
        tool_call_id = request.tool_call.get("id", "")

        try:
            raw_content = result.content if isinstance(result.content, str) else str(result.content)
            parsed = json.loads(raw_content)
            items = config.extract_items(parsed)
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning(
                "CitationMiddleware: failed to parse output for tool '{}': {}", tool_name, e
            )
            return result

        chunks_for_llm: list[str] = []

        for item in items:
            citation_id = await counter.next()
            metadata = config.extract_metadata(item)
            text = config.extract_text(item)
            chunks = chunk_text(text)

            if not chunks:
                continue

            # Push citation event to queue
            citation_data: dict[str, Any] = {
                "citation_id": citation_id,
                "chunks": [
                    {"chunk_index": i, "content": c} for i, c in enumerate(chunks)
                ],
                "metadata": metadata,
                "tool_call_id": tool_call_id,
            }

            if queue is not None:
                await queue.put(("citation", None, citation_data))

            # Build LLM-facing content with markers
            for i, c in enumerate(chunks):
                chunks_for_llm.append(f"【{citation_id}-{i}】 {c}")

        if chunks_for_llm:
            # Preserve original content for frontend display
            result.additional_kwargs["original_content"] = raw_content
            result.content = "\n\n".join(chunks_for_llm)

        return result

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any] | AIMessage]],
    ) -> ModelResponse[Any] | AIMessage:
        if not self._configs:
            return await handler(request)
        new_system = append_to_system_message(request.system_message, CITATION_PROMPT)
        return await handler(request.override(system_message=new_system))
```

- [ ] **Step 4: Update __init__.py exports**

```python
# cubeplex/middleware/citations/__init__.py
"""Citation middleware for inline reference tracking."""

from cubeplex.middleware.citations.config import CitationConfig, load_citation_configs
from cubeplex.middleware.citations.counter import (
    CitationCounter,
    citation_counter_var,
    citation_event_queue,
)
from cubeplex.middleware.citations.middleware import CitationMiddleware

__all__ = [
    "CitationConfig",
    "CitationCounter",
    "CitationMiddleware",
    "citation_counter_var",
    "citation_event_queue",
    "load_citation_configs",
]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_citation_middleware.py -v`
Expected: All PASS

- [ ] **Step 6: Run full type check**

Run: `uv run mypy cubeplex/middleware/citations/`
Expected: Success

- [ ] **Step 7: Commit**

```bash
git add cubeplex/middleware/citations/ tests/unit/test_citation_middleware.py
git commit -m "feat(citations): add CitationMiddleware with tool interception and prompt injection"
```

---

### Task 7: Preserve Original Content in stream.py

**Files:**
- Modify: `cubeplex/agents/stream.py:152-170`

- [ ] **Step 1: Modify _extract_tool_events to use original_content**

In `cubeplex/agents/stream.py`, in the `_extract_tool_events` function, after extracting `content` and `additional_kwargs` from the message (around line 130), the tool_result event should prefer `original_content` from `additional_kwargs`.

Find the block that builds the `tool_result` data dict (lines 166-171):

```python
        data: dict[str, Any] = {
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            "content": result_str,
        }
```

Replace with:

```python
        # Use original content for frontend display if CitationMiddleware rewrote it
        original_content = (additional_kwargs or {}).get("original_content")
        display_content = original_content if original_content else result_str

        data: dict[str, Any] = {
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            "content": display_content,
        }
```

- [ ] **Step 2: Run existing stream tests**

Run: `uv run pytest tests/unit/test_convert_messages.py -v`
Expected: All existing tests still PASS

- [ ] **Step 3: Run type check**

Run: `uv run mypy cubeplex/agents/stream.py`
Expected: Success

- [ ] **Step 4: Commit**

```bash
git add cubeplex/agents/stream.py
git commit -m "feat(citations): use original_content for tool_result SSE display"
```

---

### Task 8: Wire CitationMiddleware into Agent Graph

**Files:**
- Modify: `cubeplex/agents/graph.py`

- [ ] **Step 1: Add citation_configs parameter and middleware registration**

In `cubeplex/agents/graph.py`, add the import and parameter:

Add import at top (after line 12):

```python
from cubeplex.middleware.citations import CitationMiddleware, load_citation_configs
```

Add import for `CitationConfig` at top:

```python
from cubeplex.middleware.citations import CitationConfig, CitationMiddleware
```

Add `citation_configs` parameter to `create_cubeplex_agent` signature (after `checkpointer` param, line 31):

```python
    citation_configs: dict[str, CitationConfig] | None = None,
```

Add CitationMiddleware registration after `TimestampMiddleware()` (after line 49):

```python
    # Citation middleware — must be before SubAgentMiddleware so subagents inherit it
    _citation_configs = citation_configs or {}
    if _citation_configs:
        middleware.append(CitationMiddleware(citation_configs=_citation_configs))
```

- [ ] **Step 2: Run existing graph tests**

Run: `uv run pytest tests/unit/test_graph.py -v`
Expected: All existing tests still PASS

- [ ] **Step 3: Run type check**

Run: `uv run mypy cubeplex/agents/graph.py`
Expected: Success

- [ ] **Step 4: Commit**

```bash
git add cubeplex/agents/graph.py
git commit -m "feat(citations): wire CitationMiddleware into agent graph factory"
```

---

### Task 9: SSE Event Generator — Counter Init, Citation Events, Stream Buffering

**Files:**
- Modify: `cubeplex/api/routes/v1/conversations.py`

This is the largest integration task. Three changes in the `event_generator`:

- [ ] **Step 1: Add citation counter and event queue initialization**

In `conversations.py`, inside `event_generator()`, after the line that sets `subagent_event_queue` (line 251):

```python
        cv_token = subagent_event_queue.set(event_q)
```

Add citation counter and queue setup:

```python
        from cubeplex.middleware.citations.counter import (
            CitationCounter,
            citation_counter_var,
            citation_event_queue,
        )

        # Citation counter: recover max ID from thread history for cross-turn continuity
        citation_counter = CitationCounter(start=1)  # default, updated below after checkpointer
        cc_token = citation_counter_var.set(citation_counter)
        ce_token = citation_event_queue.set(event_q)
```

After the checkpointer is created and config_dict is defined (after line 327), add the cross-turn recovery scan:

```python
            # Recover citation counter from conversation history
            import re

            try:
                state = await agent.aget_state(config_dict)
                if state and state.values:
                    history_messages = state.values.get("messages", [])
                    max_citation_id = 0
                    citation_pattern = re.compile(r"【(\d+)-\d+】")
                    for msg in history_messages:
                        msg_content = getattr(msg, "content", "") or ""
                        if isinstance(msg_content, str):
                            for match in citation_pattern.finditer(msg_content):
                                cid = int(match.group(1))
                                if cid > max_citation_id:
                                    max_citation_id = cid
                    if max_citation_id > 0:
                        citation_counter._next = max_citation_id + 1
                        logger.debug(
                            "Recovered citation counter: next_id={}",
                            max_citation_id + 1,
                        )
            except Exception as e:
                logger.debug("Could not recover citation counter: {}", e)
```

- [ ] **Step 2: Handle "citation" kind in the event loop**

In the event loop (around line 404), after the `elif kind == "subagent":` block, add:

```python
                elif kind == "citation":
                    from cubeplex.agents.schemas import CitationEvent

                    citation_data = item[2]
                    agent_id = item[1]  # None for main agent
                    citation_event = CitationEvent(
                        timestamp=datetime.now(UTC).isoformat(),
                        data=citation_data,
                        agent_id=agent_id,
                    )
                    yield f"data: {citation_event.model_dump_json()}\n\n"
```

- [ ] **Step 3: Add stream buffering for 【】 markers**

Add a helper function before `event_generator` (or at module level):

```python
import re

_CITATION_MARKER_START = "【"
_CITATION_MARKER_END = "】"
```

In the event loop, where `text_delta` events are yielded via `_dicts_to_sse_events`, wrap the yield logic with buffering. The simplest approach is to add a `_citation_buffer` variable and process text_delta events before yielding.

Add a buffer variable at the top of the event loop (before `while True:`):

```python
            _citation_buffer = ""
```

Then replace the two blocks that yield SSE events for `mode == "messages"` (both the `kind == "main"` and `kind == "subagent"` blocks). For each block, where it currently does:

```python
                        for sse_event in _dicts_to_sse_events(evts):
                            yield f"data: {sse_event.model_dump_json()}\n\n"
```

Replace with:

```python
                        for sse_event in _dicts_to_sse_events(evts):
                            if sse_event.type == "text_delta":
                                content = sse_event.data.get("content", "")
                                content = _citation_buffer + content
                                _citation_buffer = ""
                                # Check for unclosed 【 that might be citation marker
                                last_open = content.rfind(_CITATION_MARKER_START)
                                if last_open != -1 and _CITATION_MARKER_END not in content[last_open:]:
                                    _citation_buffer = content[last_open:]
                                    content = content[:last_open]
                                if content:
                                    sse_event.data["content"] = content
                                    yield f"data: {sse_event.model_dump_json()}\n\n"
                            else:
                                # Flush buffer before non-text events
                                if _citation_buffer:
                                    from cubeplex.agents.schemas import TextDeltaEvent
                                    flush_event = TextDeltaEvent(
                                        timestamp=datetime.now(UTC).isoformat(),
                                        data={"content": _citation_buffer, "usage": {"input_tokens": 0, "output_tokens": 0}},
                                        agent_id=sse_event.agent_id,
                                    )
                                    _citation_buffer = ""
                                    yield f"data: {flush_event.model_dump_json()}\n\n"
                                yield f"data: {sse_event.model_dump_json()}\n\n"
```

After the `while True` loop ends (before the `except` block), flush any remaining buffer:

```python
            # Flush remaining citation buffer
            if _citation_buffer:
                from cubeplex.agents.schemas import TextDeltaEvent

                flush_event = TextDeltaEvent(
                    timestamp=datetime.now(UTC).isoformat(),
                    data={"content": _citation_buffer, "usage": {"input_tokens": 0, "output_tokens": 0}},
                )
                yield f"data: {flush_event.model_dump_json()}\n\n"
```

- [ ] **Step 4: Clean up ContextVars in finally block**

In the `finally` block (after `subagent_event_queue.reset`), add cleanup:

```python
            try:
                citation_counter_var.reset(cc_token)
            except ValueError:
                citation_counter_var.set(None)
            try:
                citation_event_queue.reset(ce_token)
            except ValueError:
                citation_event_queue.set(None)
```

- [ ] **Step 5: Load citation configs and pass to create_cubeplex_agent**

In the `event_generator`, where `create_cubeplex_agent` is called (around line 318), load citation configs from MCP server config and pass them:

Before the `create_cubeplex_agent` call, add:

```python
            # Load citation configs from MCP tool definitions
            from cubeplex.middleware.citations import load_citation_configs

            from cubeplex.middleware.citations import CitationConfig

            all_citation_configs: dict[str, CitationConfig] = {}
            try:
                from cubeplex.config import config as app_config

                mcp_servers = app_config.get("mcp.servers", {})
                for _server_name, server_cfg in (mcp_servers or {}).items():
                    tool_defs = server_cfg.get("tools", [])
                    if tool_defs:
                        all_citation_configs.update(load_citation_configs(tool_defs))
            except Exception as e:
                logger.debug("Failed to load citation configs: {}", e)
```

Then pass to `create_cubeplex_agent`:

```python
            agent = create_cubeplex_agent(
                llm=llm,
                tools=tools,
                sandbox=sandbox,
                conversation_id=conversation_id,
                skills=raw_request.app.state.skills,
                checkpointer=checkpointer,
                citation_configs=all_citation_configs,  # <-- add this
            )
```

- [ ] **Step 6: Run lint and type check**

Run: `uv run ruff check cubeplex/api/routes/v1/conversations.py && uv run mypy cubeplex/api/routes/v1/conversations.py`
Expected: Success (may need minor fixes)

- [ ] **Step 7: Commit**

```bash
git add cubeplex/api/routes/v1/conversations.py
git commit -m "feat(citations): integrate counter init, citation events, and stream buffering into SSE generator"
```

---

### Task 10: Config Example

**Files:**
- Modify: `config.yaml:124-127`

- [ ] **Step 1: Add example citation config**

In `config.yaml`, replace the empty MCP section:

```yaml
  # MCP Configuration
  mcp:
    enabled: false
    servers: {}
```

With a commented example:

```yaml
  # MCP Configuration
  mcp:
    enabled: false
    servers: {}
    # Example with citation support:
    # servers:
    #   web-tools:
    #     transport: streamable_http
    #     url: "http://localhost:3001/mcp"
    #     tools:
    #       - name: web_search
    #         content_type: text
    #         citation:
    #           source_type: web
    #           content_field: "results"
    #           mapping:
    #             url: "link"
    #             title: "title"
    #             domain: "domain"
    #             snippet: "snippet"
    #             published_at: "date"
    #       - name: web_fetch
    #         content_type: text
    #         citation:
    #           source_type: web
    #           content_field: null
    #           mapping:
    #             url: "url"
    #             title: "title"
    #             snippet: "content"
```

- [ ] **Step 2: Commit**

```bash
git add config.yaml
git commit -m "docs: add citation config example to config.yaml"
```

---

### Task 11: Full Verification

- [ ] **Step 1: Run all unit tests**

Run: `uv run pytest tests/unit/ -v`
Expected: All PASS

- [ ] **Step 2: Run format, lint, type check**

Run: `make check`
Expected: All pass

- [ ] **Step 3: Final commit if any formatting changes**

```bash
git add -u
git commit -m "style: formatting fixes from make check"
```
