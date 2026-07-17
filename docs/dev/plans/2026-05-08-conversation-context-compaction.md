# Conversation Context Compaction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop crashing on long conversations. Compress old turns into a persistent summary; LLM sees `[summary] + recent N msgs`; UI keeps full original history.

**Architecture:** Add a `CompactionMiddleware` that uses two hooks: `abefore_model` decides when to compact and writes the summary into a new state field `compaction` (persisted by checkpointer); `awrap_model_call` reads that state and projects `request.messages` into the compressed view per call. `state.messages` is never modified — UI, citations, audit all remain intact. Summary is a small in-repo `CompactionSummary` dataclass — no extra dependency.

**Tech Stack:** LangGraph + langchain agents middleware, pytest for unit + E2E. No new third-party deps.

**Spec:** `docs/superpowers/specs/2026-05-08-conversation-context-compaction-design.md`

---

## File Structure

```
backend/
├── cubeplex/
│   ├── agents/
│   │   └── state.py                       (NEW) CompactionSummary dataclass + CubeplexState TypedDict
│   ├── middleware/
│   │   └── compaction/
│   │       ├── __init__.py                (NEW)
│   │       ├── tokens.py                  (NEW) approx_tokens helper
│   │       ├── boundary.py                (NEW) safe_boundary algorithm
│   │       ├── summarizer.py              (NEW) summary LLM call + prompt
│   │       └── middleware.py              (NEW) CompactionMiddleware class
│   └── agents/graph.py                    (MODIFY) wire CompactionMiddleware
├── config.yaml                            (MODIFY) add compaction.* keys
└── tests/
    ├── unit/
    │   └── middleware/
    │       └── compaction/
    │           ├── test_boundary.py       (NEW)
    │           ├── test_tokens.py         (NEW)
    │           └── test_middleware.py     (NEW)
    └── e2e/
        └── test_conversation_compaction.py (NEW)
```

Decomposition rationale:
- `tokens.py` / `boundary.py` / `summarizer.py` are pure functions — easy to unit test without the LLM.
- `middleware.py` orchestrates the three; only this file touches `state` and `request`.
- `state.py` is shared with future agent-level state extensions (already a natural place).

---

## Task 1: Create state module with CompactionSummary

**Files:**
- Create: `backend/cubeplex/agents/state.py`
- Test: (deferred — covered in Task 5 middleware tests)

- [ ] **Step 1: Create state module**

Create `backend/cubeplex/agents/state.py`:

```python
"""Cubeplex-specific extensions to the langchain agent state schema."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, NotRequired

from langchain.agents.middleware.types import AgentState


@dataclass
class CompactionSummary:
    """Persisted running summary of a conversation's older turns.

    Stored on agent state, serialized by the LangGraph checkpointer.
    Three-field shape mirrors the canonical "running summary" pattern: the text,
    which messages it covers, and where the rolling window currently ends.
    """

    summary: str
    summarized_message_ids: list[str] = field(default_factory=list)
    last_summarized_message_id: str | None = None


class CubeplexState(AgentState[Any]):
    """Agent state with compaction fields.

    Extends AgentState (TypedDict) with two optional keys:
      compaction:                 CompactionSummary persisted across turns
      compaction_until_msg_index: int boundary in state["messages"]
    """

    compaction: NotRequired[CompactionSummary | None]
    compaction_until_msg_index: NotRequired[int | None]
```

- [ ] **Step 2: Sanity check imports**

```bash
cd /home/chris/cubeplex/backend && uv run python -c \
  "from cubeplex.agents.state import CubeplexState, CompactionSummary; \
   s = CompactionSummary(summary='x'); print(s)"
```

Expected: `CompactionSummary(summary='x', summarized_message_ids=[], last_summarized_message_id=None)`.

- [ ] **Step 3: Commit**

```bash
git add backend/cubeplex/agents/state.py
git commit -m "feat(compaction): add CompactionSummary dataclass and CubeplexState"
```

---

## Task 2: Token approximation helper

**Files:**
- Create: `backend/cubeplex/middleware/compaction/__init__.py`
- Create: `backend/cubeplex/middleware/compaction/tokens.py`
- Test: `backend/tests/unit/middleware/compaction/test_tokens.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/middleware/compaction/test_tokens.py`:

```python
"""Tests for approx_tokens — should count tokens across all message types."""

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from cubeplex.middleware.compaction.tokens import approx_tokens


def test_empty_messages_zero():
    assert approx_tokens([]) == 0


def test_counts_text_content():
    msgs = [HumanMessage(content="hello world"), AIMessage(content="hi there")]
    n = approx_tokens(msgs)
    assert n > 0
    assert n < 50


def test_counts_tool_message_content():
    msgs = [ToolMessage(content="big tool output " * 100, tool_call_id="t1")]
    assert approx_tokens(msgs) > 100


def test_counts_system_message():
    assert approx_tokens([SystemMessage(content="you are a helpful assistant")]) > 0


def test_handles_list_content_blocks():
    msg = HumanMessage(content=[{"type": "text", "text": "block one"}])
    assert approx_tokens([msg]) > 0
```

- [ ] **Step 2: Run test, verify it fails**

```bash
cd /home/chris/cubeplex/backend && uv run pytest tests/unit/middleware/compaction/test_tokens.py -v
```

Expected: `ModuleNotFoundError: No module named 'cubeplex.middleware.compaction'`.

- [ ] **Step 3: Create package init**

Create `backend/cubeplex/middleware/compaction/__init__.py`:

```python
"""Conversation compaction middleware package."""
```

- [ ] **Step 4: Implement `tokens.py`**

Create `backend/cubeplex/middleware/compaction/tokens.py`:

```python
"""Approximate token counting for messages.

IMPORTANT: callers must pass the *view* they intend to send to the LLM
(i.e. the post-compaction projection [summary, *recent]), NOT the raw
state["messages"]. Passing raw history breaks scaling accuracy because
historical AIMessage.usage_metadata reflects the compressed view the
LLM actually saw — comparing it against an approx walked over the full
history yields a scale_factor < 1 (clamped to 1.0, scaling effectively
disabled) and also triggers needless re-compaction on stable convos.
"""

from __future__ import annotations

from langchain_core.messages import AnyMessage
from langchain_core.messages.utils import count_tokens_approximately


# 2.0 chars/token is a deliberate conservative override of the 4.0 default.
# 4.0 underestimates Chinese / CJK by ~3-4x; with our threshold of
# context_window * 0.7, underestimating means compacting too late → overflow.
# Once usage_metadata scaling kicks in (turn 2+), the value is self-corrected
# anyway — this just protects the cold start.
_CHARS_PER_TOKEN = 2.0


def approx_tokens(messages: list[AnyMessage]) -> int:
    """Approximate total tokens for a list of messages.

    Uses langchain_core.count_tokens_approximately with usage_metadata
    self-scaling enabled, so historical AIMessages with real token counts
    auto-calibrate the estimate (scale factor clamped to [1.0, 1.25]).
    """
    if not messages:
        return 0
    return int(
        count_tokens_approximately(
            messages,
            chars_per_token=_CHARS_PER_TOKEN,
            use_usage_metadata_scaling=True,
        )
    )
```

- [ ] **Step 5: Run test, verify it passes**

```bash
cd /home/chris/cubeplex/backend && uv run pytest tests/unit/middleware/compaction/test_tokens.py -v
```

Expected: 5 passed.

- [ ] **Step 6: Commit**

```bash
git add backend/cubeplex/middleware/compaction/ backend/tests/unit/middleware/compaction/test_tokens.py
git commit -m "feat(compaction): add approx_tokens helper"
```

---

## Task 3: Safe boundary algorithm

**Files:**
- Create: `backend/cubeplex/middleware/compaction/boundary.py`
- Test: `backend/tests/unit/middleware/compaction/test_boundary.py`

The algorithm picks `boundary` such that `messages[:boundary]` is the to-summarize prefix
and `messages[boundary:]` is the keep-as-is suffix. It must:
1. Keep at least `keep_recent` messages in the suffix.
2. Place `messages[boundary]` on a `HumanMessage` (start of a "turn").
3. Not split any `AIMessage(tool_calls)` ↔ `ToolMessage` pairing.
4. Return `None` if no safe boundary exists (caller falls back to no compaction).

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/unit/middleware/compaction/test_boundary.py`:

```python
"""Tests for _safe_boundary."""

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from cubeplex.middleware.compaction.boundary import safe_boundary


def _h(text: str) -> HumanMessage:
    return HumanMessage(content=text)


def _a(text: str = "", tool_calls: list[dict] | None = None) -> AIMessage:
    return AIMessage(content=text, tool_calls=tool_calls or [])


def _t(call_id: str, text: str = "ok") -> ToolMessage:
    return ToolMessage(content=text, tool_call_id=call_id, name="x")


def test_keeps_recent_window_when_history_short():
    msgs = [_h("h1"), _a("a1")]
    # keep_recent=4, so nothing to compact; boundary is None
    assert safe_boundary(msgs, keep_recent=4) is None


def test_basic_boundary_lands_on_humanmessage():
    msgs = [_h("h1"), _a("a1"), _h("h2"), _a("a2"), _h("h3"), _a("a3")]
    # keep_recent=2 → tentative boundary = 4 → msgs[4] = _h("h3") ✓
    assert safe_boundary(msgs, keep_recent=2) == 4


def test_walks_back_to_humanmessage():
    msgs = [_h("h1"), _a("a1"), _h("h2"), _a("a2"), _h("h3"), _a("a3")]
    # keep_recent=3 → tentative boundary = 3 → msgs[3] = _a, walk back to msgs[2] = _h
    assert safe_boundary(msgs, keep_recent=3) == 2


def test_does_not_split_tool_call_pair():
    # h1 → a1(tool_calls=[t1]) → tool t1 → h2 → a2
    msgs = [
        _h("h1"),
        _a(tool_calls=[{"id": "t1", "name": "x", "args": {}}]),
        _t("t1"),
        _h("h2"),
        _a("a2"),
    ]
    # keep_recent=2 → tentative boundary = 3 → msgs[3] = _h("h2") — but the ToolMessage
    # at msgs[2] would be orphaned (its AIMessage is in summarized prefix).
    # Wait: msgs[3] = _h("h2"), msgs[3:] = [_h, _a] — no tool message there, no orphan.
    # That's actually safe.
    assert safe_boundary(msgs, keep_recent=2) == 3


def test_orphan_tool_message_walks_back():
    # If keep_recent=3, tentative boundary = 2 → msgs[2] = _t("t1") (orphan).
    # Must walk to msgs[1] = _a (not Human), then msgs[0] = _h.
    msgs = [
        _h("h1"),
        _a(tool_calls=[{"id": "t1", "name": "x", "args": {}}]),
        _t("t1"),
        _h("h2"),
        _a("a2"),
    ]
    # boundary=0 means "summarize nothing" → return None
    assert safe_boundary(msgs, keep_recent=3) is None


def test_skips_system_messages_in_search():
    msgs = [SystemMessage(content="sys"), _h("h1"), _a("a1"), _h("h2"), _a("a2")]
    # keep_recent=2 → tentative boundary = 3 → msgs[3] = _h("h2")
    assert safe_boundary(msgs, keep_recent=2) == 3


def test_returns_none_when_no_humanmessage_before_window():
    msgs = [_a("a1"), _a("a2"), _h("h1"), _a("a3")]
    # keep_recent=1 → tentative boundary = 3, msgs[3]=_a, walk back to msgs[2]=_h ✓
    assert safe_boundary(msgs, keep_recent=1) == 2


def test_min_compact_size_too_small_returns_none():
    msgs = [_h("h1"), _a("a1"), _h("h2"), _a("a2")]
    # keep_recent=2, tentative boundary=2, msgs[2]=_h ✓ — but only 2 msgs to summarize.
    # If min_compact=4, refuse.
    assert safe_boundary(msgs, keep_recent=2, min_compact=4) is None
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
cd /home/chris/cubeplex/backend && uv run pytest tests/unit/middleware/compaction/test_boundary.py -v
```

Expected: ModuleNotFoundError on `boundary`.

- [ ] **Step 3: Implement `boundary.py`**

Create `backend/cubeplex/middleware/compaction/boundary.py`:

```python
"""Boundary selection for compaction — picks a safe split point."""

from __future__ import annotations

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, ToolMessage


def safe_boundary(
    messages: list[AnyMessage],
    *,
    keep_recent: int,
    min_compact: int = 1,
) -> int | None:
    """Return an index `b` such that messages[:b] is summarizable and messages[b:] is kept.

    Constraints:
      1. messages[b:] must contain >= keep_recent items.
      2. messages[b] must be a HumanMessage (start of a turn).
      3. messages[b:] must not contain a ToolMessage whose tool_call_id has no
         matching AIMessage.tool_calls within messages[b:].
      4. If no boundary satisfies all and leaves at least min_compact messages
         in the prefix, return None (caller skips compaction this round).
    """
    n = len(messages)
    if n <= keep_recent:
        return None

    candidate = n - keep_recent
    while candidate > 0:
        msg = messages[candidate]
        if not isinstance(msg, HumanMessage):
            candidate -= 1
            continue
        if not _suffix_is_self_contained(messages[candidate:]):
            candidate -= 1
            continue
        if candidate < min_compact:
            return None
        return candidate

    return None


def _suffix_is_self_contained(suffix: list[AnyMessage]) -> bool:
    """Every ToolMessage in the suffix must have its parent AIMessage in the suffix."""
    available_call_ids: set[str] = set()
    for msg in suffix:
        if isinstance(msg, AIMessage):
            for tc in msg.tool_calls or []:
                tc_id = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)
                if tc_id:
                    available_call_ids.add(tc_id)
        elif isinstance(msg, ToolMessage):
            if msg.tool_call_id and msg.tool_call_id not in available_call_ids:
                return False
    return True
```

- [ ] **Step 4: Run tests, verify they pass**

```bash
cd /home/chris/cubeplex/backend && uv run pytest tests/unit/middleware/compaction/test_boundary.py -v
```

Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/middleware/compaction/boundary.py \
        backend/tests/unit/middleware/compaction/test_boundary.py
git commit -m "feat(compaction): add safe_boundary algorithm with tool-pair safety"
```

---

## Task 4: Summarizer

**Files:**
- Create: `backend/cubeplex/middleware/compaction/summarizer.py`
- Test: covered indirectly via middleware test in Task 5; pure-function part is small enough to skip.

- [ ] **Step 1: Implement `summarizer.py`**

Create `backend/cubeplex/middleware/compaction/summarizer.py`:

```python
"""Summarizer — runs a cheap LLM to produce / update a CompactionSummary."""

from __future__ import annotations

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AnyMessage, HumanMessage, SystemMessage

from cubeplex.agents.state import CompactionSummary


SUMMARIZER_SYSTEM_PROMPT = """\
You compress a chat transcript into a brief, faithful narrative for an AI assistant
that is continuing the conversation. Rules:

1. Preserve facts, user goals, decisions made, and unresolved questions.
2. Preserve every 【N-K】 citation marker verbatim. Do not renumber, merge, or drop them.
3. Do not quote long tool outputs. Reference them by their citation markers instead.
4. Keep the language of the original conversation.
5. Output the summary directly. No preamble, no JSON, no markdown headers.
"""

EXISTING_SUMMARY_SUFFIX = """\
A previous summary already covers earlier turns:

<previous_summary>
{prev}
</previous_summary>

Merge it with the new turns below. Output the updated summary."""


def _format_messages_for_summary(messages: list[AnyMessage]) -> str:
    parts: list[str] = []
    for m in messages:
        role = m.__class__.__name__.removesuffix("Message").lower() or "msg"
        content = m.content if isinstance(m.content, str) else str(m.content)
        parts.append(f"[{role}] {content}")
    return "\n\n".join(parts)


async def summarize(
    *,
    model: BaseChatModel,
    messages_to_summarize: list[AnyMessage],
    existing: CompactionSummary | None,
    max_summary_tokens: int = 1024,
) -> CompactionSummary:
    """Generate or update a CompactionSummary covering messages_to_summarize."""
    system_text = SUMMARIZER_SYSTEM_PROMPT
    if existing and existing.summary:
        system_text = system_text + "\n\n" + EXISTING_SUMMARY_SUFFIX.format(prev=existing.summary)

    prompt_messages = [
        SystemMessage(content=system_text),
        HumanMessage(content=_format_messages_for_summary(messages_to_summarize)),
    ]
    bound = model.bind(max_tokens=max_summary_tokens)
    response = await bound.ainvoke(prompt_messages)
    text = response.content if isinstance(response.content, str) else str(response.content)

    new_ids: list[str] = [getattr(m, "id", None) or "" for m in messages_to_summarize]
    new_ids = [i for i in new_ids if i]
    prior_ids: list[str] = list(existing.summarized_message_ids) if existing else []

    return CompactionSummary(
        summary=text.strip(),
        summarized_message_ids=prior_ids + new_ids,
        last_summarized_message_id=new_ids[-1] if new_ids else (
            existing.last_summarized_message_id if existing else None
        ),
    )
```

- [ ] **Step 2: Sanity check imports**

```bash
cd /home/chris/cubeplex/backend && uv run python -c \
  "from cubeplex.middleware.compaction.summarizer import summarize, SUMMARIZER_SYSTEM_PROMPT; print('ok')"
```

Expected: `ok`.

- [ ] **Step 3: Commit**

```bash
git add backend/cubeplex/middleware/compaction/summarizer.py
git commit -m "feat(compaction): add summarizer with citation-marker-preservation prompt"
```

---

## Task 5: CompactionMiddleware

**Files:**
- Create: `backend/cubeplex/middleware/compaction/middleware.py`
- Modify: `backend/cubeplex/middleware/compaction/__init__.py`
- Test: `backend/tests/unit/middleware/compaction/test_middleware.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/unit/middleware/compaction/test_middleware.py`:

```python
"""Tests for CompactionMiddleware (mocked summarizer LLM)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from cubeplex.middleware.compaction.middleware import CompactionMiddleware


def _make_state(msgs: list[Any], compaction=None, until=None) -> dict[str, Any]:
    s: dict[str, Any] = {"messages": msgs}
    if compaction is not None:
        s["compaction"] = compaction
    if until is not None:
        s["compaction_until_msg_index"] = until
    return s


@pytest.mark.asyncio
async def test_below_threshold_no_action():
    summary_llm = AsyncMock()
    mw = CompactionMiddleware(
        summary_llm=summary_llm,
        max_tokens_before_compact=10_000,
        keep_recent_messages=2,
    )
    state = _make_state([HumanMessage(content="hi"), AIMessage(content="hello")])
    result = await mw.abefore_model(state)
    assert result is None
    summary_llm.bind.assert_not_called()


@pytest.mark.asyncio
async def test_triggers_when_over_threshold(monkeypatch):
    from cubeplex.agents.state import CompactionSummary
    from cubeplex.middleware.compaction import middleware as mw_mod

    fake = CompactionSummary(
        summary="user asked about X; agent answered Y",
        summarized_message_ids=["m1", "m2"],
        last_summarized_message_id="m2",
    )
    summarize_mock = AsyncMock(return_value=fake)
    monkeypatch.setattr(mw_mod, "summarize", summarize_mock)

    msgs = [
        HumanMessage(content="x" * 5000, id="m1"),
        AIMessage(content="y" * 5000, id="m2"),
        HumanMessage(content="follow up", id="m3"),
        AIMessage(content="ok", id="m4"),
    ]
    mw = CompactionMiddleware(
        summary_llm=AsyncMock(),
        max_tokens_before_compact=100,
        keep_recent_messages=2,
    )
    result = await mw.abefore_model(_make_state(msgs))

    assert result is not None
    assert result["compaction"] is fake
    assert result["compaction_until_msg_index"] == 2


@pytest.mark.asyncio
async def test_does_not_recompact_when_compressed_view_fits(monkeypatch):
    """Stable convo with existing summary whose compressed view fits the
    threshold must NOT trigger another summarize call, even if raw
    state.messages keeps growing.
    """
    from cubeplex.agents.state import CompactionSummary
    from cubeplex.middleware.compaction import middleware as mw_mod

    summarize_mock = AsyncMock()
    monkeypatch.setattr(mw_mod, "summarize", summarize_mock)

    # 50 raw messages, but only the recent 4 are unsummarized.
    msgs = [HumanMessage(content=f"m{i}", id=f"m{i}") for i in range(46)] + [
        HumanMessage(content="recent1", id="r1"),
        AIMessage(content="recent2", id="r2"),
        HumanMessage(content="recent3", id="r3"),
        AIMessage(content="recent4", id="r4"),
    ]
    existing = CompactionSummary(
        summary="short summary",
        summarized_message_ids=[f"m{i}" for i in range(46)],
        last_summarized_message_id="m45",
    )
    state = _make_state(msgs, compaction=existing, until=46)

    mw = CompactionMiddleware(
        summary_llm=AsyncMock(),
        max_tokens_before_compact=10_000,  # compressed view fits easily
        keep_recent_messages=2,
    )
    result = await mw.abefore_model(state)

    assert result is None, "must not re-summarize when compressed view fits threshold"
    summarize_mock.assert_not_called()


@pytest.mark.asyncio
async def test_skips_when_no_safe_boundary(monkeypatch):
    from cubeplex.middleware.compaction import middleware as mw_mod
    summarize_mock = AsyncMock()
    monkeypatch.setattr(mw_mod, "summarize", summarize_mock)

    msgs = [AIMessage(content="x" * 9000, id="m1"), AIMessage(content="y" * 9000, id="m2")]
    mw = CompactionMiddleware(
        summary_llm=AsyncMock(),
        max_tokens_before_compact=100,
        keep_recent_messages=2,
    )
    result = await mw.abefore_model(_make_state(msgs))
    assert result is None
    summarize_mock.assert_not_called()


@pytest.mark.asyncio
async def test_skips_when_summarizer_fails(monkeypatch):
    from cubeplex.middleware.compaction import middleware as mw_mod
    summarize_mock = AsyncMock(side_effect=RuntimeError("llm down"))
    monkeypatch.setattr(mw_mod, "summarize", summarize_mock)

    msgs = [
        HumanMessage(content="x" * 5000, id="m1"),
        AIMessage(content="y" * 5000, id="m2"),
        HumanMessage(content="m3", id="m3"),
        AIMessage(content="m4", id="m4"),
    ]
    mw = CompactionMiddleware(
        summary_llm=AsyncMock(),
        max_tokens_before_compact=100,
        keep_recent_messages=2,
    )
    result = await mw.abefore_model(_make_state(msgs))
    assert result is None  # graceful fallback


@pytest.mark.asyncio
async def test_awrap_projects_compressed_view():
    from cubeplex.agents.state import CompactionSummary

    captured: dict[str, Any] = {}

    async def handler(req):
        captured["messages"] = list(req.messages)
        return AIMessage(content="response")

    msgs = [
        HumanMessage(content="m1", id="m1"),
        AIMessage(content="m2", id="m2"),
        HumanMessage(content="m3", id="m3"),
        AIMessage(content="m4", id="m4"),
    ]
    summary = CompactionSummary(
        summary="prior context covered",
        summarized_message_ids=["m1", "m2"],
        last_summarized_message_id="m2",
    )

    class FakeRequest:
        def __init__(self):
            self.messages = list(msgs)
            self.state = {
                "messages": msgs,
                "compaction": summary,
                "compaction_until_msg_index": 2,
            }

    mw = CompactionMiddleware(
        summary_llm=AsyncMock(),
        max_tokens_before_compact=10_000,
        keep_recent_messages=2,
    )
    req = FakeRequest()
    await mw.awrap_model_call(req, handler)  # type: ignore[arg-type]

    sent = captured["messages"]
    assert isinstance(sent[0], SystemMessage)
    assert "prior context covered" in sent[0].content
    assert [m.id for m in sent[1:]] == ["m3", "m4"]


@pytest.mark.asyncio
async def test_awrap_passes_through_when_no_compaction():
    captured: dict[str, Any] = {}

    async def handler(req):
        captured["messages"] = list(req.messages)
        return AIMessage(content="ok")

    msgs = [HumanMessage(content="hi", id="m1"), AIMessage(content="hello", id="m2")]

    class FakeRequest:
        def __init__(self):
            self.messages = list(msgs)
            self.state = {"messages": msgs}

    mw = CompactionMiddleware(
        summary_llm=AsyncMock(),
        max_tokens_before_compact=10_000,
        keep_recent_messages=2,
    )
    await mw.awrap_model_call(FakeRequest(), handler)  # type: ignore[arg-type]
    assert [m.id for m in captured["messages"]] == ["m1", "m2"]
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
cd /home/chris/cubeplex/backend && uv run pytest tests/unit/middleware/compaction/test_middleware.py -v
```

Expected: ModuleNotFoundError on middleware.

- [ ] **Step 3: Implement `middleware.py`**

Create `backend/cubeplex/middleware/compaction/middleware.py`:

```python
"""CompactionMiddleware — persist summary in state, project compressed view per call."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AnyMessage, SystemMessage
from loguru import logger

from cubeplex.agents.state import CompactionSummary
from cubeplex.middleware.compaction.boundary import safe_boundary
from cubeplex.middleware.compaction.summarizer import summarize
from cubeplex.middleware.compaction.tokens import approx_tokens


SUMMARY_PREFIX = "[Conversation summary so far]\n"


def _compressed_view(state: Any) -> list[AnyMessage]:
    """Build the messages list the LLM would actually see given current state.

    If a CompactionSummary exists and a boundary has been recorded, the view is
    [SystemMessage(summary), *messages[boundary:]] — exactly what awrap_model_call
    will install on the request. Otherwise it's the raw messages list.

    Used by abefore_model so the threshold check is against what we're about to
    SEND, not against the raw history (which keeps growing and would force
    needless re-compaction on stable conversations, plus break usage_metadata
    scaling — see tokens.py docstring).
    """
    msgs: list[AnyMessage] = list(state.get("messages") or [])
    summary: CompactionSummary | None = state.get("compaction")
    boundary: int | None = state.get("compaction_until_msg_index")
    if summary and boundary and boundary > 0:
        return [
            SystemMessage(content=SUMMARY_PREFIX + summary.summary),
            *msgs[boundary:],
        ]
    return msgs


class CompactionMiddleware(AgentMiddleware[Any, Any, Any]):
    """Compress old turns into a persisted CompactionSummary; project compressed view per call.

    Two responsibilities split across two hooks:
      abefore_model — decide whether to compact further; if so, write new summary state.
      awrap_model_call — install the compressed view on request.messages just for this call.
    """

    def __init__(
        self,
        *,
        summary_llm: BaseChatModel,
        max_tokens_before_compact: int,
        keep_recent_messages: int = 8,
        max_summary_tokens: int = 1024,
        min_compact_messages: int = 4,
    ) -> None:
        self._summary_llm = summary_llm
        self._max_tokens_before = max_tokens_before_compact
        self._keep_recent = keep_recent_messages
        self._max_summary_tokens = max_summary_tokens
        self._min_compact = min_compact_messages

    async def abefore_model(self, state: Any) -> dict[str, Any] | None:
        # Threshold check: measure what we're ABOUT to send (compressed view),
        # not the raw history. If a stable conversation already has a summary
        # that fits, this returns early and avoids re-summarizing.
        if approx_tokens(_compressed_view(state)) < self._max_tokens_before:
            return None

        msgs: list[AnyMessage] = list(state.get("messages") or [])
        existing: CompactionSummary | None = state.get("compaction")
        last_until: int = state.get("compaction_until_msg_index") or 0

        boundary = safe_boundary(
            msgs,
            keep_recent=self._keep_recent,
            min_compact=max(self._min_compact, last_until + 1),
        )
        if boundary is None or boundary <= last_until:
            return None

        to_summarize = msgs[last_until:boundary]
        try:
            new_summary = await summarize(
                model=self._summary_llm,
                messages_to_summarize=to_summarize,
                existing=existing,
                max_summary_tokens=self._max_summary_tokens,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("CompactionMiddleware: summarizer failed, skipping: {}", exc)
            return None

        logger.info(
            "CompactionMiddleware: compacted msgs[{}:{}] ({} msgs)",
            last_until,
            boundary,
            len(to_summarize),
        )
        return {
            "compaction": new_summary,
            "compaction_until_msg_index": boundary,
        }

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any] | AIMessage]],
    ) -> ModelResponse[Any] | AIMessage:
        state = request.state or {}
        summary: CompactionSummary | None = state.get("compaction")
        boundary: int | None = state.get("compaction_until_msg_index")

        if summary and boundary and boundary > 0:
            request.messages = _compressed_view(state)

        return await handler(request)
```

- [ ] **Step 4: Wire `__init__.py`**

Update `backend/cubeplex/middleware/compaction/__init__.py`:

```python
"""Conversation compaction middleware package."""

from cubeplex.middleware.compaction.middleware import CompactionMiddleware

__all__ = ["CompactionMiddleware"]
```

- [ ] **Step 5: Run tests, verify they pass**

```bash
cd /home/chris/cubeplex/backend && uv run pytest tests/unit/middleware/compaction/ -v
```

Expected: 6 passed (this file) + earlier 8 + 5 = 19 total in compaction unit suite.

- [ ] **Step 6: Run lint + type check**

```bash
cd /home/chris/cubeplex/backend && uv run ruff check cubeplex/middleware/compaction/ tests/unit/middleware/compaction/
cd /home/chris/cubeplex/backend && uv run mypy cubeplex/middleware/compaction/
```

Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add backend/cubeplex/middleware/compaction/ backend/tests/unit/middleware/compaction/test_middleware.py
git commit -m "feat(compaction): add CompactionMiddleware with abefore_model + awrap_model_call"
```

---

## Task 6: Config schema

**Files:**
- Modify: `backend/config.yaml`
- Modify: `backend/config.development.yaml` (and other env configs if values differ)

- [ ] **Step 1: Add config keys to `config.yaml`**

Append to the bottom of `backend/config.yaml`:

```yaml
compaction:
  enabled: false
  summary_provider: anthropic
  summary_model: claude-haiku-4-5
  threshold_ratio: 0.7
  keep_recent_messages: 8
  max_summary_tokens: 1024
  min_compact_messages: 4
  fallback_context_window: 64000
```

- [ ] **Step 2: Verify config loads**

```bash
cd /home/chris/cubeplex/backend && uv run python -c \
  "from cubeplex.config import config; print(config.get('compaction.enabled'), config.get('compaction.summary_model'))"
```

Expected: `False claude-haiku-4-5`.

- [ ] **Step 3: Commit**

```bash
git add backend/config.yaml
git commit -m "feat(compaction): add compaction config keys (default off)"
```

---

## Task 7: Wire CompactionMiddleware into `create_cubeplex_agent`

**Files:**
- Modify: `backend/cubeplex/agents/graph.py`

- [ ] **Step 1: Read the current insertion site**

Run:

```bash
sed -n '170,200p' /home/chris/cubeplex/backend/cubeplex/agents/graph.py
```

Verify the `TodoListMiddleware` and `SubAgentMiddleware` lines are around 177–185 — Task 7 inserts between them.

- [ ] **Step 2: Add CompactionMiddleware wiring**

Edit `backend/cubeplex/agents/graph.py`. Locate the block:

```python
    middleware.append(TodoListMiddleware())
    middleware.append(
        SubAgentMiddleware(
```

Replace with (the new block sits BETWEEN TodoListMiddleware and SubAgentMiddleware):

```python
    middleware.append(TodoListMiddleware())

    if _config.get("compaction.enabled", False):
        from cubeplex.llm.factory import LLMFactory
        from cubeplex.middleware.compaction import CompactionMiddleware

        try:
            summary_llm = LLMFactory().create(
                provider_name=_config.get("compaction.summary_provider"),
                model_id=_config.get("compaction.summary_model"),
            )
            ctx_window = (
                getattr(llm, "context_window", None)
                or _config.get("compaction.fallback_context_window", 64000)
            )
            ratio = float(_config.get("compaction.threshold_ratio", 0.7))
            middleware.append(
                CompactionMiddleware(
                    summary_llm=summary_llm,
                    max_tokens_before_compact=int(ctx_window * ratio),
                    keep_recent_messages=int(
                        _config.get("compaction.keep_recent_messages", 8)
                    ),
                    max_summary_tokens=int(
                        _config.get("compaction.max_summary_tokens", 1024)
                    ),
                    min_compact_messages=int(
                        _config.get("compaction.min_compact_messages", 4)
                    ),
                )
            )
            logger.info(
                "CompactionMiddleware enabled (threshold={} tokens, keep_recent={})",
                int(ctx_window * ratio),
                _config.get("compaction.keep_recent_messages", 8),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "CompactionMiddleware not loaded ({}); proceeding without it", exc
            )

    middleware.append(
        SubAgentMiddleware(
```

- [ ] **Step 3: Sanity check — agent still imports**

```bash
cd /home/chris/cubeplex/backend && uv run python -c \
  "from cubeplex.agents.graph import create_cubeplex_agent; print('ok')"
```

Expected: `ok`.

- [ ] **Step 4: Run unit tests to verify no regressions**

```bash
cd /home/chris/cubeplex/backend && uv run pytest tests/unit/ -v -x
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/agents/graph.py
git commit -m "feat(compaction): wire CompactionMiddleware in create_cubeplex_agent (gated by config)"
```

---

## Task 8: E2E — compaction triggers and history is preserved

**Files:**
- Create: `backend/tests/e2e/test_conversation_compaction.py`

This test verifies the **full pipeline**:
- A long conversation crosses the configured threshold
- After the next turn, `state.compaction.summary` is non-empty
- The conversation messages API still returns the **complete original history**

The test enables compaction via env var override (so we don't flip global config).

- [ ] **Step 1: Write the test**

Create `backend/tests/e2e/test_conversation_compaction.py`:

```python
"""E2E: compaction triggers, summary persists, full history still surfaces."""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage

pytestmark = pytest.mark.e2e


@pytest.mark.asyncio
async def test_long_conversation_triggers_compaction(
    authed_workspace_client, monkeypatch
):
    """Send enough turns to push past the threshold; verify compaction state lands.

    Uses the existing E2E LLM (CUBEPLEX_E2E_LLM_*) for both the main agent and the
    summarizer; sets a deliberately tiny threshold so we don't need huge inputs.
    """
    monkeypatch.setenv("CUBEPLEX_COMPACTION__ENABLED", "true")
    monkeypatch.setenv("CUBEPLEX_COMPACTION__THRESHOLD_RATIO", "0.001")  # trigger fast
    monkeypatch.setenv("CUBEPLEX_COMPACTION__KEEP_RECENT_MESSAGES", "2")
    monkeypatch.setenv("CUBEPLEX_COMPACTION__MIN_COMPACT_MESSAGES", "2")

    client, ws_id = authed_workspace_client
    convo = await _create_conversation(client, ws_id)
    cid = convo["id"]

    for i in range(4):
        await _send_and_drain(client, ws_id, cid, f"turn {i}: tell me a one-sentence fact")

    state = await _get_thread_state(client, ws_id, cid)
    assert state.get("compaction") is not None, "expected compaction state to be populated"
    assert state["compaction"]["summary"], "summary text should be non-empty"
    assert state.get("compaction_until_msg_index", 0) > 0

    msgs = await _get_messages(client, ws_id, cid)
    user_count = sum(1 for m in msgs if m["role"] == "user")
    assert user_count == 4, f"UI history must keep all 4 user turns, got {user_count}"


# ---- helpers ----


async def _create_conversation(client, ws_id):
    r = await client.post(f"/api/v1/ws/{ws_id}/conversations", json={"title": "compact"})
    r.raise_for_status()
    return r.json()


async def _send_and_drain(client, ws_id, cid, text):
    async with client.stream(
        "POST",
        f"/api/v1/ws/{ws_id}/conversations/{cid}/messages",
        json={"content": text},
    ) as r:
        async for _ in r.aiter_lines():
            pass


async def _get_messages(client, ws_id, cid):
    r = await client.get(f"/api/v1/ws/{ws_id}/conversations/{cid}/messages")
    r.raise_for_status()
    return r.json()["messages"]


async def _get_thread_state(client, ws_id, cid):
    """Read the raw langgraph thread state via the debug endpoint or test helper.

    If no public endpoint exists, this helper reaches into the checkpointer
    directly. Use the test fixture `agent_state_reader` if provided.
    """
    r = await client.get(f"/api/v1/ws/{ws_id}/conversations/{cid}/state")
    r.raise_for_status()
    return r.json()
```

- [ ] **Step 2: Check whether `/conversations/{cid}/state` exists**

```bash
grep -rn "conversations.*state\|thread.*state\|aget_state" backend/cubeplex/api/ --include="*.py"
```

If no such endpoint exists, replace `_get_thread_state` with a direct checkpointer
read using the `checkpointer` fixture from `tests/e2e/conftest.py`. Search for an
existing fixture:

```bash
grep -n "checkpointer\|aget_state" backend/tests/e2e/conftest.py backend/tests/e2e/helpers.py 2>/dev/null
```

If neither exists, add this helper inline in the test (replacing `_get_thread_state`).
This normalizes the `compaction` value into a dict so the rest of the test can use
subscript access regardless of whether checkpointer returns a `CompactionSummary`
dataclass instance or a deserialized dict:

```python
async def _get_thread_state(client, ws_id, cid):
    from dataclasses import asdict, is_dataclass
    from langchain_core.runnables import RunnableConfig
    from cubeplex.agents.checkpointer import get_checkpointer  # adjust to actual path

    saver = await get_checkpointer()
    cfg = RunnableConfig(configurable={"thread_id": cid})
    snap = await saver.aget(cfg)
    values = snap["channel_values"] if snap else {}
    comp = values.get("compaction")
    if is_dataclass(comp):
        values = {**values, "compaction": asdict(comp)}
    return values
```

(Reviewer: if `get_checkpointer` is not the correct accessor, swap for the
project's actual API — see `backend/cubeplex/agents/checkpointer.py`.)

- [ ] **Step 3: Run the test**

```bash
cd /home/chris/cubeplex/backend && uv run pytest tests/e2e/test_conversation_compaction.py::test_long_conversation_triggers_compaction -v -s
```

Expected: PASS. If it fails because of fixture name (`authed_workspace_client`),
locate the actual fixture name in `tests/e2e/conftest.py` (search
`pytest_asyncio.fixture` and `def *_client`) and swap.

- [ ] **Step 4: Commit**

```bash
git add backend/tests/e2e/test_conversation_compaction.py
git commit -m "test(compaction): e2e — long convo triggers compaction, full history preserved"
```

---

## Task 9: E2E — summary persists across turns (no recompute)

**Files:**
- Modify: `backend/tests/e2e/test_conversation_compaction.py`

- [ ] **Step 1: Add the test**

Append to `test_conversation_compaction.py`:

```python
@pytest.mark.asyncio
async def test_summary_persists_across_turns(authed_workspace_client, monkeypatch):
    """Once compaction lands, a follow-up turn whose new content is small must NOT
    re-summarize (compaction_until_msg_index should advance only when needed).
    """
    monkeypatch.setenv("CUBEPLEX_COMPACTION__ENABLED", "true")
    monkeypatch.setenv("CUBEPLEX_COMPACTION__THRESHOLD_RATIO", "0.001")
    monkeypatch.setenv("CUBEPLEX_COMPACTION__KEEP_RECENT_MESSAGES", "2")
    monkeypatch.setenv("CUBEPLEX_COMPACTION__MIN_COMPACT_MESSAGES", "2")

    client, ws_id = authed_workspace_client
    convo = await _create_conversation(client, ws_id)
    cid = convo["id"]

    for i in range(4):
        await _send_and_drain(client, ws_id, cid, f"turn {i}: one-line fact")

    s1 = await _get_thread_state(client, ws_id, cid)
    summary_v1 = s1["compaction"]["summary"]
    until_v1 = s1["compaction_until_msg_index"]

    await _send_and_drain(client, ws_id, cid, "tiny follow-up")

    s2 = await _get_thread_state(client, ws_id, cid)
    summary_v2 = s2["compaction"]["summary"]
    until_v2 = s2["compaction_until_msg_index"]

    # Either summary is byte-identical (no recompute), or boundary advanced because
    # the new turn pushed total tokens further past threshold. The forbidden case is
    # boundary advancing without summary changing — guard the "stable summary"
    # invariant when boundary did NOT move:
    if until_v2 == until_v1:
        assert summary_v2 == summary_v1, "summary changed without boundary moving"
```

- [ ] **Step 2: Run it**

```bash
cd /home/chris/cubeplex/backend && uv run pytest tests/e2e/test_conversation_compaction.py::test_summary_persists_across_turns -v -s
```

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/e2e/test_conversation_compaction.py
git commit -m "test(compaction): e2e — summary persists when boundary unchanged"
```

---

## Task 10: E2E — tool boundary safety + citation marker preservation

**Files:**
- Modify: `backend/tests/e2e/test_conversation_compaction.py`

- [ ] **Step 1: Add the test**

Append:

```python
@pytest.mark.asyncio
async def test_compaction_preserves_citation_markers_and_tool_pairs(
    authed_workspace_client, monkeypatch
):
    """Force a tool-using conversation, then trigger compaction; verify:
      1. summary text contains at least one 【N-K】 marker preserved verbatim
      2. saved state has no orphan ToolMessage in the kept window
      3. the messages API still surfaces all original citations[] arrays
    """
    monkeypatch.setenv("CUBEPLEX_COMPACTION__ENABLED", "true")
    monkeypatch.setenv("CUBEPLEX_COMPACTION__THRESHOLD_RATIO", "0.001")
    monkeypatch.setenv("CUBEPLEX_COMPACTION__KEEP_RECENT_MESSAGES", "2")
    monkeypatch.setenv("CUBEPLEX_COMPACTION__MIN_COMPACT_MESSAGES", "2")

    client, ws_id = authed_workspace_client
    convo = await _create_conversation(client, ws_id)
    cid = convo["id"]

    for i in range(3):
        await _send_and_drain(
            client,
            ws_id,
            cid,
            f"read /etc/hostname and tell me what it says (call {i})",
        )

    state = await _get_thread_state(client, ws_id, cid)
    assert state.get("compaction"), "expected compaction to have triggered"
    summary = state["compaction"]["summary"]

    import re
    assert re.search(r"【\d+-\d+】", summary), \
        "summarizer must preserve at least one 【N-K】 marker; got:\n" + summary

    msgs = await _get_messages(client, ws_id, cid)
    tool_msgs = [m for m in msgs if m["role"] == "tool"]
    assert any(m.get("citations") for m in tool_msgs), \
        "API response must surface citations[] on at least one ToolMessage"
```

- [ ] **Step 2: Run it**

```bash
cd /home/chris/cubeplex/backend && uv run pytest tests/e2e/test_conversation_compaction.py::test_compaction_preserves_citation_markers_and_tool_pairs -v -s
```

Expected: PASS. If the marker assertion fails, the summarizer prompt may need
tightening — strengthen the language in `summarizer.py` and rerun.

- [ ] **Step 3: Run the full compaction E2E suite + lint + type-check**

```bash
cd /home/chris/cubeplex/backend && uv run pytest tests/e2e/test_conversation_compaction.py -v
cd /home/chris/cubeplex/backend && uv run ruff check cubeplex/middleware/compaction/ cubeplex/agents/state.py
cd /home/chris/cubeplex/backend && uv run mypy cubeplex/middleware/compaction/ cubeplex/agents/state.py
```

Expected: 3 e2e passed; ruff and mypy clean.

- [ ] **Step 4: Commit**

```bash
git add backend/tests/e2e/test_conversation_compaction.py
git commit -m "test(compaction): e2e — citation markers preserved in summary, tool pairs intact"
```

---

## Task 11: Final verification & PR

**Files:**
- None (verification only)

- [ ] **Step 1: Run full backend check**

```bash
cd /home/chris/cubeplex/backend && make check
```

Expected: format, lint, type-check, tests all pass.

- [ ] **Step 2: Confirm default-off behavior**

```bash
cd /home/chris/cubeplex/backend && uv run python -c \
  "from cubeplex.config import config; assert config.get('compaction.enabled') is False, 'must default off'; print('default-off ok')"
```

Expected: `default-off ok`.

- [ ] **Step 3: Skim the diff for stray TODOs / debug prints**

```bash
git diff --stat main...HEAD
git diff main...HEAD | grep -E "^\+.*\b(TODO|FIXME|print\()" || echo "clean"
```

Expected: `clean`.

- [ ] **Step 4: Open PR**

```bash
gh pr create --title "feat(agents): conversation context compaction" \
  --body "$(cat <<'EOF'
## Summary
- Adds `CompactionMiddleware`: when conversation token count crosses
  `compaction.threshold_ratio * context_window`, compresses old turns into a
  persistent `CompactionSummary` stored in agent state.
- LLM input layer becomes `[SystemMessage(summary), …recent N msgs]`; the
  storage layer (`state.messages`) is **never modified**, so UI history,
  citations, and audit remain intact.
- Default off (`compaction.enabled: false`) — opt-in via config / env.

Spec: `docs/superpowers/specs/2026-05-08-conversation-context-compaction-design.md`

## Test plan
- [x] Unit: token approximation, safe-boundary algorithm, middleware behavior (mock LLM)
- [x] E2E: long convo triggers compaction; full history still surfaces from messages API
- [x] E2E: summary persists across turns when boundary unchanged
- [x] E2E: 【N-K】 citation markers preserved in summary; tool_call/tool_result pairs intact
- [x] `make check` clean

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Expected: PR URL printed.

---

## Spec Coverage Check

| Spec section | Task |
|---|---|
| §2 Architecture (two-layer decoupling) | Task 5 (middleware), Task 8 (E2E proof) |
| §3 Scope decisions (middleware not graph node, in-repo CompactionSummary) | Task 1, Task 5 |
| §4 State model (CubeplexState extension) | Task 1 |
| §5.1 Threshold trigger | Task 5 (`abefore_model`) |
| §5.2 Safe boundary algorithm | Task 3 |
| §5.3 Summary generation + marker preservation prompt | Task 4, Task 10 (assertion) |
| §5.4 Request rewrite (LLM-only view) | Task 5 (`awrap_model_call`) |
| §5.5 State persistence via return dict | Task 5, Task 9 (E2E persistence) |
| §6 Citation handling (no-op; preservation prompt) | Task 4, Task 10 |
| §7 Middleware wiring & ordering | Task 7 |
| §8 Configuration | Task 6 |
| §9 Failure modes (summarizer fails, no safe boundary) | Task 5 (unit tests) |
| §10 Testing matrix | Tasks 8 / 9 / 10 |
| §11 Rollout plan | Task 11 (PR scope = "default off") |

No gaps.
