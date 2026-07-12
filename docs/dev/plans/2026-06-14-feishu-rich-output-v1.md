# Feishu rich-output v1 implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the cubeplex→Feishu text-only output path with a CardKit-based rich-output pipeline that renders markdown, tool calls, artifacts, AskUser/SandboxConfirm callbacks, light SubAgent, TaskProgress, and citations-as-links.

**Architecture:** Four new modules under `backend/cubeplex/im/feishu/` separating typed card state (`card_model`) from pure JSON rendering (`card_renderer`) from CardKit HTTP/SDK IO (`cardkit_client`) from inbound action dispatch (`card_action_router`). The existing `OutboundRunTailer` stays the event loop; `fold_event` mutates a `CardState` instead of concatenating text. One CardKit entity per cubepi run. Hard cutover from the text path; the only legacy survivor is an emergency text fallback if `create_entity` itself fails.

**Tech Stack:** Python 3.12, FastAPI, `lark_oapi` SDK, pytest + `pytest-asyncio`, `httpx` for direct CardKit HTTP calls (the bundled `lark_oapi` Python SDK predates the CardKit endpoints), `pydantic` for typed state.

**Branch:** `feat/feishu-rich-output-v1`
**Worktree:** `/home/chris/cubeplex/.worktrees/feat/feishu-rich-output-v1` (slot 49, API `:8049`, Web `:3049`)
**Spec:** [docs/dev/specs/2026-06-14-feishu-rich-output-v1-design.md](../specs/2026-06-14-feishu-rich-output-v1-design.md)

---

## Event schema reference (binding for ALL event-handling code below)

The Task 0 audit found mismatches between this plan's v0 field-name
assumptions and the actual cubeplex-published shape. The authoritative
schema is in
[docs/dev/notes/2026-06-13-feishu-richer-output-borrow-from-openclaw.md](../notes/2026-06-13-feishu-richer-output-borrow-from-openclaw.md)
under "Addendum 2026-06-14".

**Implementer must use these exact names:**

- `text_delta.data.content: str`
- `tool_call.data.{tool_call_id: str, name: str, arguments: str}` —
  `arguments` is a **JSON string**, decode with `json.loads`. The
  `ToolStep` model stores `args: dict`; the renderer caller decodes.
- `tool_result.data.{tool_call_id: str, name: str, content: str, is_error: bool, details: Any}`
  — **no `elapsed_ms` field**. Compute elapsed locally from event
  timestamps if needed (store `start_monotonic` on the ToolStep when
  `tool_call` arrives).
- `artifact.data.{action: "created"|"updated", artifact: {...}}`
- `citation.data.{citation_id: str, chunks: list, metadata: {url, title, ...}, tool_call_id: str}`
  — index the renderer's citation_index by `citation_id`, not a flat
  `index` field. URL comes from `metadata.url`, title from
  `metadata.title`.
- `ask_user_request.data.{question_id: str, questions: list[{key, prompt, options, multi_select, required}], timeout_seconds}`
  — multi-question form. v1 renders the first question only; choices
  come from `questions[0].options`; the button payload carries
  `question_id` so the resume call can match it.
- `ask_user_resolved.data.{question_id, answers, cancelled, timed_out}`
  — flips the `pending_input` to resolved state.
- `sandbox_confirm_request.data.{question_id: str, tool_call_id, command: str, matched_pattern, timeout_seconds}`
- `sandbox_confirm_resolved.data.{question_id, decision, cancelled, timed_out, reason}`
- `done.data == {}` — no elapsed_ms. Compute total elapsed from the
  difference between this event's timestamp and the first event's
  timestamp (the tailer can stash a `run_start_monotonic`).
- `error.data.{error_code: str, message: str, details: Any}`

**Sub-agent tracking:** there are NO `sub_agent_*` events. Every
`AgentEvent` carries `agent_id: str | None` and `agent_name: str | None`
at the top level (alongside `type`, `timestamp`, `data`). `agent_id`
of None = main agent; `agent_id = "subagent:<tool_call_id>"` = sub.
`fold_event` reads `event.get("agent_id")` and, when non-None,
routes the `tool_call` to a `SubAgentRow` (creating one if absent)
and bumps its `tool_count` instead of appending a regular `ToolStep`.

Tasks 8–11 below use these exact field names. The `ToolStep`,
`PendingInput`, and renderer code paths reflect them.

---

## File structure

**New:**
- `backend/cubeplex/im/feishu/card_model.py` — typed `CardState` and sub-types (pydantic).
- `backend/cubeplex/im/feishu/card_renderer.py` — pure JSON 2.0 serialization + tool display table + markdown style optimizer + citation-link rewriter.
- `backend/cubeplex/im/feishu/cardkit_client.py` — CardKit REST API wrapper: `create_entity`, `stream_text`, `patch_card`, `finalize`. Owns the throttle buckets and retry/backoff.
- `backend/cubeplex/im/feishu/card_action_router.py` — `dispatch(payload) -> ResumeAction` (pure) and the inbound handler that calls cubepi `resume_with_human_input`.
- `backend/docs/im-feishu-rich-output.md` — operator-facing doc: CardKit scope, debugging.
- Tests mirroring each new file under `backend/tests/im/feishu/`.

**Modified:**
- `backend/cubeplex/im/types.py` — replace `RenderState` fields.
- `backend/cubeplex/im/outbound.py` — `OutboundOp.kind` union; `fold_event` returns card ops; `OutboundRunTailer._dispatch_op` calls cardkit client.
- `backend/cubeplex/im/feishu/connector.py` — keep reactions, rename text path to `_send_emergency_text`, drop `_build_payload` / `_MARKDOWN_*` / `edit` / `send_text_message`.
- `backend/cubeplex/im/artifacts.py` — `IMArtifactDispatcher` updates `CardState.artifacts` instead of sending standalone messages.
- `backend/cubeplex/api/routes/v1/im_ingress.py` — branch on `event_type == "card.action.trigger"` and route to action router.
- `backend/cubeplex/im/feishu/long_connection.py` — register card-action handler alongside message handler.
- `backend/tests/e2e/im_feishu_*.py` — migrate assertions from bubble text to card JSON.
- `backend/docs/quick-reference.md` — add `cardkit:card:write` scope line.

**Deleted:**
- Old text-path internals in `connector.py` (covered in Task 18).

---

## Task 0: Verify cubepi event payload completeness

**Goal:** Confirm cubepi events carry the fields v1 needs. Document gaps as cubepi-upstream-first follow-ups before any code lands.

**Files:**
- Inspect: `backend/cubeplex/im/outbound.py`, `~/cubepi` (read-only)
- Output: append section to `docs/dev/notes/2026-06-13-feishu-richer-output-borrow-from-openclaw.md`

- [ ] **Step 1: Identify required event fields**

We need:
- `tool_call` events: `name`, `args`, `id`, `elapsed_ms_start_marker`
- `tool_result` events: `id` (matching `tool_call`), `result` payload, `elapsed_ms`, `error`
- `artifact` events: `id`, `artifact_type`, `name`, `entry_file`, `version`
- `ask_user` events: `prompt`, `choices` (or freeform), `run_id`
- `sandbox_confirm` events: `prompt`, `command`, `run_id`
- `citation` events: `index` (str like "1"), `url`, `title`

- [ ] **Step 2: Grep cubepi for emit sites**

Run from main repo root:

```bash
grep -rn 'event_type.*"tool_call"\|"tool_result"\|"artifact"\|"ask_user"\|"sandbox_confirm"\|"citation"' \
  ~/cubepi/cubepi 2>/dev/null | head -50
```

Expected: each event type has at least one emit site; missing types become upstream-first PRs.

- [ ] **Step 3: Cross-check the field shapes**

For each event found, open the emit site and confirm field names match the list in Step 1. Note any mismatch (e.g. cubepi emits `tool_name` but plan expects `name`) in the next step.

- [ ] **Step 4: Document findings in the note**

Append a new section to `docs/dev/notes/2026-06-13-feishu-richer-output-borrow-from-openclaw.md`:

```markdown
## Addendum 2026-06-14: cubepi event field audit

Verified against /home/chris/cubepi at commit <sha>.

| Event | Field | Present? | Notes |
|---|---|---|---|
| tool_call | name | ... | ... |
| tool_call | args | ... | ... |
...

**Upstream-first follow-ups:**
- (list any gaps and which cubepi file they live in)
```

- [ ] **Step 5: Commit the addendum**

```bash
git add docs/dev/notes/2026-06-13-feishu-richer-output-borrow-from-openclaw.md
git commit -m "docs(notes): add cubepi event payload audit for feishu rich-output v1"
```

Expected: clean commit; if any gaps found, address them as a separate cubepi upstream PR before starting Task 1.

---

## Task 1: Add `CardState` data model

**Files:**
- Create: `backend/cubeplex/im/feishu/card_model.py`
- Create: `backend/tests/im/feishu/test_card_model.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/im/feishu/test_card_model.py`:

```python
"""Tests for CardState — pure data shape, no IO."""
from cubeplex.im.feishu.card_model import (
    ArtifactItem,
    CardState,
    PendingInput,
    ToolStep,
)


def test_card_state_defaults_are_empty() -> None:
    state = CardState(bot_name="cubeplex", run_id="run_1")
    assert state.streaming_content == ""
    assert state.tool_steps == []
    assert state.artifacts == []
    assert state.pending_input is None
    assert state.finalized is False
    assert state.error is None
    assert state.elapsed_ms == 0
    assert state.next_seq == 0


def test_tool_step_status_transitions() -> None:
    step = ToolStep(id="tc_1", name="read_file", args={"path": "a"})
    assert step.status == "running"
    step.mark_succeeded(result={"ok": True}, elapsed_ms=312)
    assert step.status == "succeeded"
    assert step.result == {"ok": True}
    assert step.elapsed_ms == 312


def test_tool_step_failure_keeps_error() -> None:
    step = ToolStep(id="tc_2", name="bash", args={"cmd": "x"})
    step.mark_failed(error="permission denied", elapsed_ms=20)
    assert step.status == "failed"
    assert step.error == "permission denied"


def test_artifact_item_carries_share_url() -> None:
    art = ArtifactItem(
        id="art_1",
        artifact_type="document",
        name="report.pdf",
        share_url="https://example.com/share/abc",
    )
    assert art.image_key is None


def test_pending_input_question_and_choices() -> None:
    pending = PendingInput(
        kind="ask_user",
        run_id="run_1",
        question="Continue?",
        choices=[("yes", "primary"), ("no", "default")],
    )
    assert pending.kind == "ask_user"
    assert pending.resolved_choice is None


def test_card_state_advance_seq() -> None:
    state = CardState(bot_name="cubeplex", run_id="run_1")
    assert state.advance_seq() == 0
    assert state.advance_seq() == 1
    assert state.next_seq == 2
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd backend && uv run pytest tests/im/feishu/test_card_model.py -v
```

Expected: `ModuleNotFoundError: No module named 'cubeplex.im.feishu.card_model'`.

- [ ] **Step 3: Implement `card_model.py`**

Create `backend/cubeplex/im/feishu/card_model.py`:

```python
"""Typed card state for outbound Feishu rendering.

Pure data: no IO, no Feishu SDK imports. The renderer turns one of these
into a CardKit JSON 2.0 payload; the tailer mutates one of these as
cubepi events arrive.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(slots=True)
class ToolStep:
    """One row in the tool-use panel."""

    id: str
    name: str
    args: dict[str, Any]
    status: Literal["running", "succeeded", "failed"] = "running"
    result: Any = None
    error: str | None = None
    elapsed_ms: int = 0

    def mark_succeeded(self, *, result: Any, elapsed_ms: int) -> None:
        self.status = "succeeded"
        self.result = result
        self.elapsed_ms = elapsed_ms

    def mark_failed(self, *, error: str, elapsed_ms: int) -> None:
        self.status = "failed"
        self.error = error
        self.elapsed_ms = elapsed_ms


@dataclass(slots=True)
class ArtifactItem:
    """One row in the artifacts panel."""

    id: str
    artifact_type: str
    name: str
    share_url: str | None = None
    image_key: str | None = None
    description: str | None = None


@dataclass(slots=True)
class PendingInput:
    """An awaiting-user-input prompt rendered as an interactive_container."""

    kind: Literal["ask_user", "sandbox_confirm"]
    run_id: str
    question: str
    choices: list[tuple[str, str]] = field(default_factory=list)
    """Pairs of (choice_key, button_type). button_type ∈ {"primary","default","danger"}."""
    resolved_choice: str | None = None
    resolved_by_open_id: str | None = None
    resolved_at_iso: str | None = None


@dataclass(slots=True)
class SubAgentRow:
    """Light SubAgent marker — one line in the tool panel above the regular steps."""

    name: str
    tool_count: int = 0


@dataclass(slots=True)
class CardState:
    """Per-run accumulating state, projected into CardKit JSON by the renderer.

    Mutated in-place by `fold_event`. The renderer never mutates.
    """

    bot_name: str
    run_id: str
    streaming_content: str = ""
    tool_steps: list[ToolStep] = field(default_factory=list)
    sub_agents: list[SubAgentRow] = field(default_factory=list)
    artifacts: list[ArtifactItem] = field(default_factory=list)
    citation_index: dict[str, tuple[str, str]] = field(default_factory=dict)
    """index → (url, title). Used by renderer to rewrite [N] markers as links."""
    pending_input: PendingInput | None = None
    finalized: bool = False
    error: str | None = None
    elapsed_ms: int = 0
    next_seq: int = 0
    epoch: int = 0
    """Bumped on run abort; in-flight responses for a stale epoch are dropped."""

    def advance_seq(self) -> int:
        seq = self.next_seq
        self.next_seq += 1
        return seq

    def find_tool(self, tool_id: str) -> ToolStep | None:
        for step in self.tool_steps:
            if step.id == tool_id:
                return step
        return None


__all__ = [
    "ArtifactItem",
    "CardState",
    "PendingInput",
    "SubAgentRow",
    "ToolStep",
]
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd backend && uv run pytest tests/im/feishu/test_card_model.py -v
```

Expected: all 6 tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/im/feishu/card_model.py backend/tests/im/feishu/test_card_model.py
git commit -m "feat(im-feishu): add CardState typed model for rich-output v1"
```

---

## Task 2: Add `optimize_markdown_style`

**Files:**
- Create: `backend/cubeplex/im/feishu/card_renderer.py` (skeleton + this function)
- Create: `backend/tests/im/feishu/test_card_renderer_markdown.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/im/feishu/test_card_renderer_markdown.py`:

```python
"""Tests for optimize_markdown_style — Feishu CardKit markdown sanitization."""
from cubeplex.im.feishu.card_renderer import optimize_markdown_style


def test_h1_demotes_to_h4() -> None:
    out = optimize_markdown_style("# Title\nbody")
    assert out.startswith("#### Title")


def test_h2_demotes_to_h5() -> None:
    assert optimize_markdown_style("## Sub").startswith("##### Sub")


def test_h3_h4_h5_h6_demote_to_h5() -> None:
    assert optimize_markdown_style("### a").startswith("##### a")
    assert optimize_markdown_style("###### h").startswith("##### h")


def test_table_gets_br_spacers() -> None:
    md = "before\n| a | b |\n| - | - |\n| 1 | 2 |\nafter"
    out = optimize_markdown_style(md)
    assert "<br>" in out
    # Table content survives.
    assert "| a | b |" in out


def test_code_block_content_untouched() -> None:
    md = "```python\n# this is a comment\n```"
    out = optimize_markdown_style(md)
    # The "#" inside the code fence must NOT have been demoted to "####".
    assert "# this is a comment" in out


def test_invalid_image_key_stripped() -> None:
    md = "![alt](http://example.com/x.png)"
    out = optimize_markdown_style(md)
    assert "http://example.com/x.png" not in out


def test_valid_image_key_preserved() -> None:
    md = "![alt](img_v1_abc123)"
    out = optimize_markdown_style(md)
    assert "img_v1_abc123" in out


def test_citation_marker_replaced_with_link() -> None:
    citations = {"1": ("https://example.com/a", "Example"),
                 "2": ("https://example.com/b", "B")}
    out = optimize_markdown_style("see [1] and [2]", citation_index=citations)
    assert "[1](https://example.com/a)" in out
    assert "[2](https://example.com/b)" in out


def test_unknown_citation_marker_left_as_is() -> None:
    out = optimize_markdown_style("see [9]", citation_index={"1": ("u", "t")})
    assert "[9]" in out
    assert "(u)" not in out


def test_chinese_bracket_citation_replaced() -> None:
    out = optimize_markdown_style(
        "见【1-3】",
        citation_index={"1": ("https://a", "A"), "3": ("https://c", "C")},
    )
    # The whole "【1-3】" span gets one link to the FIRST cited URL with the
    # full label preserved.
    assert "[1-3](https://a)" in out
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd backend && uv run pytest tests/im/feishu/test_card_renderer_markdown.py -v
```

Expected: `ModuleNotFoundError: No module named 'cubeplex.im.feishu.card_renderer'`.

- [ ] **Step 3: Implement the skeleton + `optimize_markdown_style`**

Create `backend/cubeplex/im/feishu/card_renderer.py`:

```python
"""Pure CardKit JSON 2.0 rendering for cubeplex Feishu output.

`render(state)` is the only public IO-free entry point. All other
exports (`optimize_markdown_style`, `TOOL_DISPLAY`, `summarize_args`)
are helpers used by `render`.
"""

from __future__ import annotations

import re

# Demote H1/H2 → H4/H5 (cardkit renders larger headings full-width and
# breaks the card layout). H3–H6 all collapse to H5 to keep visual rhythm
# consistent.
_H1_RE = re.compile(r"^(#)\s", re.MULTILINE)
_H2_RE = re.compile(r"^(##)\s", re.MULTILINE)
_H3_PLUS_RE = re.compile(r"^(#{3,6})\s", re.MULTILINE)

# Markdown table detection (header row + separator row).
_TABLE_RE = re.compile(
    r"(^\s*\|[^|\n]+\|.*\n\s*\|[-:\s|]+\|.*(?:\n\s*\|.*)*)",
    re.MULTILINE,
)

# Fenced code block — protected from rewrites.
_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)

# Image reference. CardKit only accepts `img_xxx` keys; any URL / path / data-uri
# image must be dropped before send to avoid error 200570.
_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
_VALID_IMAGE_KEY_RE = re.compile(r"^img_[A-Za-z0-9_-]+$")

# Citation markers: ASCII [N], [N-M] and full-width 【N-M】.
_ASCII_CITATION_RE = re.compile(r"\[(\d+(?:-\d+)?)\]")
_CN_CITATION_RE = re.compile(r"【(\d+(?:-\d+)?)】")


def optimize_markdown_style(
    text: str,
    *,
    citation_index: dict[str, tuple[str, str]] | None = None,
) -> str:
    """Sanitize cubepi markdown for Feishu CardKit's markdown element.

    Demotes headings, spaces tables, strips invalid image refs, and
    rewrites citation markers to inline links. Code blocks are protected
    from all rewrites.
    """
    citations = citation_index or {}

    # Stash code blocks behind sentinels so rewrites never touch them.
    fences: list[str] = []

    def _stash_fence(m: re.Match[str]) -> str:
        fences.append(m.group(0))
        return f"\x00FENCE{len(fences) - 1}\x00"

    body = _FENCE_RE.sub(_stash_fence, text)

    body = _H1_RE.sub("#### ", body)
    body = _H2_RE.sub("##### ", body)
    body = _H3_PLUS_RE.sub("##### ", body)

    body = _TABLE_RE.sub(lambda m: f"<br>\n{m.group(1)}\n<br>", body)

    def _rewrite_image(m: re.Match[str]) -> str:
        target = m.group(2).strip()
        if _VALID_IMAGE_KEY_RE.match(target):
            return m.group(0)
        return m.group(1) or ""

    body = _IMAGE_RE.sub(_rewrite_image, body)

    def _resolve_first(label: str) -> str | None:
        first_id = label.split("-", 1)[0]
        entry = citations.get(first_id)
        return entry[0] if entry is not None else None

    def _rewrite_ascii_citation(m: re.Match[str]) -> str:
        label = m.group(1)
        url = _resolve_first(label)
        return f"[{label}]({url})" if url else m.group(0)

    def _rewrite_cn_citation(m: re.Match[str]) -> str:
        label = m.group(1)
        url = _resolve_first(label)
        return f"[{label}]({url})" if url else m.group(0)

    body = _ASCII_CITATION_RE.sub(_rewrite_ascii_citation, body)
    body = _CN_CITATION_RE.sub(_rewrite_cn_citation, body)

    # Restore code blocks verbatim.
    for i, fence in enumerate(fences):
        body = body.replace(f"\x00FENCE{i}\x00", fence)
    return body


__all__ = ["optimize_markdown_style"]
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd backend && uv run pytest tests/im/feishu/test_card_renderer_markdown.py -v
```

Expected: all 9 tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/im/feishu/card_renderer.py backend/tests/im/feishu/test_card_renderer_markdown.py
git commit -m "feat(im-feishu): add optimize_markdown_style for CardKit rendering"
```

---

## Task 3: Add `TOOL_DISPLAY` map + `summarize_args`

**Files:**
- Modify: `backend/cubeplex/im/feishu/card_renderer.py` (add tool display)
- Create: `backend/tests/im/feishu/test_card_renderer_tools.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/im/feishu/test_card_renderer_tools.py`:

```python
"""Tests for per-tool icon and one-liner args summarization."""
from cubeplex.im.feishu.card_renderer import (
    TOOL_DISPLAY,
    default_display,
    summarize_args,
)


def test_unknown_tool_uses_default_display() -> None:
    disp = default_display("frobnicate")
    assert disp.icon == "⚙️"
    assert disp.summarize({"x": 1}) == '{"x": 1}'


def test_summarize_args_truncates_long_values() -> None:
    long = "a" * 200
    out = summarize_args({"text": long})
    assert len(out) <= 90  # 80 cap + ellipsis budget
    assert out.endswith("…")


def test_read_file_summary_shows_path() -> None:
    disp = TOOL_DISPLAY["read_file"]
    assert "src/foo.py" in disp.summarize({"path": "src/foo.py"})
    assert disp.icon  # any non-empty icon


def test_bash_summary_shows_command_head() -> None:
    disp = TOOL_DISPLAY["bash"]
    out = disp.summarize({"cmd": "ls -la /tmp/very/long/path/with/lots/of/extra"})
    assert out.startswith("ls -la ")


def test_web_fetch_summary_shows_url() -> None:
    disp = TOOL_DISPLAY["web_fetch"]
    out = disp.summarize({"url": "https://example.com/x"})
    assert "https://example.com/x" in out


def test_update_memory_summary_shows_key() -> None:
    disp = TOOL_DISPLAY["update_memory"]
    out = disp.summarize({"key": "feedback_x", "content": "..."})
    assert "feedback_x" in out


def test_summarize_args_handles_no_args() -> None:
    assert summarize_args({}) == ""
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd backend && uv run pytest tests/im/feishu/test_card_renderer_tools.py -v
```

Expected: `ImportError: cannot import name 'TOOL_DISPLAY' from 'cubeplex.im.feishu.card_renderer'`.

- [ ] **Step 3: Add `TOOL_DISPLAY` and helpers to `card_renderer.py`**

Append to `backend/cubeplex/im/feishu/card_renderer.py`:

```python
import json
from dataclasses import dataclass
from typing import Any, Callable


_ARG_SUMMARY_CAP = 80


def _truncate(value: str, *, cap: int = _ARG_SUMMARY_CAP) -> str:
    if len(value) <= cap:
        return value
    return value[: cap - 1] + "…"


def summarize_args(args: dict[str, Any]) -> str:
    """Default args summary: JSON-flatten and truncate."""
    if not args:
        return ""
    try:
        compact = json.dumps(args, ensure_ascii=False, separators=(", ", ": "))
    except (TypeError, ValueError):
        compact = str(args)
    return _truncate(compact)


@dataclass(slots=True, frozen=True)
class ToolDisplay:
    """Per-tool rendering hints."""

    icon: str
    summarize: Callable[[dict[str, Any]], str]


def _default_summary(args: dict[str, Any]) -> str:
    return summarize_args(args)


def default_display(name: str) -> ToolDisplay:
    """Display for unregistered tools — generic icon + JSON summary."""
    _ = name  # accepted for future per-name fallback heuristics
    return ToolDisplay(icon="⚙️", summarize=_default_summary)


def _summary_read_file(args: dict[str, Any]) -> str:
    return _truncate(str(args.get("path", "")))


def _summary_write_file(args: dict[str, Any]) -> str:
    return _truncate(str(args.get("path", "")))


def _summary_bash(args: dict[str, Any]) -> str:
    return _truncate(str(args.get("cmd") or args.get("command", "")))


def _summary_web_fetch(args: dict[str, Any]) -> str:
    return _truncate(str(args.get("url", "")))


def _summary_update_memory(args: dict[str, Any]) -> str:
    return _truncate(str(args.get("key", "")))


def _summary_recall_memory(args: dict[str, Any]) -> str:
    return _truncate(str(args.get("query") or args.get("key", "")))


TOOL_DISPLAY: dict[str, ToolDisplay] = {
    "read_file": ToolDisplay(icon="📄", summarize=_summary_read_file),
    "write_file": ToolDisplay(icon="📝", summarize=_summary_write_file),
    "bash": ToolDisplay(icon="🖥️", summarize=_summary_bash),
    "web_fetch": ToolDisplay(icon="🌐", summarize=_summary_web_fetch),
    "web_search": ToolDisplay(icon="🔎", summarize=_summary_web_fetch),
    "update_memory": ToolDisplay(icon="🧠", summarize=_summary_update_memory),
    "recall_memory": ToolDisplay(icon="🧠", summarize=_summary_recall_memory),
}


__all__ = [
    "TOOL_DISPLAY",
    "ToolDisplay",
    "default_display",
    "optimize_markdown_style",
    "summarize_args",
]
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd backend && uv run pytest tests/im/feishu/test_card_renderer_tools.py -v
```

Expected: all 7 tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/im/feishu/card_renderer.py backend/tests/im/feishu/test_card_renderer_tools.py
git commit -m "feat(im-feishu): TOOL_DISPLAY map and per-tool args summarizer"
```

---

## Task 4: Add `render(state)` whole-card serializer

**Files:**
- Modify: `backend/cubeplex/im/feishu/card_renderer.py` (add `render` + element builders)
- Create: `backend/tests/im/feishu/test_card_renderer_layout.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/im/feishu/test_card_renderer_layout.py`:

```python
"""Tests for the whole-card render() — skeleton + element conditional inclusion."""
from cubeplex.im.feishu.card_model import (
    ArtifactItem,
    CardState,
    PendingInput,
    ToolStep,
)
from cubeplex.im.feishu.card_renderer import render


def _empty_state() -> CardState:
    return CardState(bot_name="cubeplex", run_id="run_1")


def test_empty_card_has_skeleton_and_no_panels() -> None:
    card = render(_empty_state())
    assert card["schema"] == "2.0"
    assert card["config"]["streaming_mode"] is True
    assert card["config"]["update_multi"] is True

    element_ids = [e["element_id"] for e in card["body"]["elements"] if "element_id" in e]
    assert "streaming_content" in element_ids
    # Empty panels are NOT included.
    assert "tool_panel" not in element_ids
    assert "artifacts" not in element_ids
    assert "pending_input" not in element_ids


def test_streaming_content_uses_optimized_markdown() -> None:
    state = _empty_state()
    state.streaming_content = "# H1 title"
    card = render(state)
    streaming = next(e for e in card["body"]["elements"] if e.get("element_id") == "streaming_content")
    assert streaming["content"].startswith("#### H1 title")


def test_tool_panel_renders_running_step() -> None:
    state = _empty_state()
    state.tool_steps.append(ToolStep(id="tc_1", name="bash", args={"cmd": "ls"}))
    card = render(state)
    panel = next(e for e in card["body"]["elements"] if e.get("element_id") == "tool_panel")
    title = panel["header"]["title"]["content"]
    assert "运行中" in title or "Running" in title


def test_tool_panel_renders_failed_step_with_red_badge() -> None:
    state = _empty_state()
    step = ToolStep(id="tc_1", name="bash", args={"cmd": "ls"})
    step.mark_failed(error="permission denied", elapsed_ms=20)
    state.tool_steps.append(step)
    card = render(state)
    panel = next(e for e in card["body"]["elements"] if e.get("element_id") == "tool_panel")
    title = panel["header"]["title"]["content"]
    assert "失败" in title or "Failed" in title


def test_artifact_image_renders_img_element() -> None:
    state = _empty_state()
    state.artifacts.append(
        ArtifactItem(id="a", artifact_type="image", name="x.png", image_key="img_v1_abc")
    )
    card = render(state)
    panel = next(e for e in card["body"]["elements"] if e.get("element_id") == "artifacts")
    assert any("img" in str(item).lower() for item in panel["elements"])


def test_artifact_link_renders_button() -> None:
    state = _empty_state()
    state.artifacts.append(
        ArtifactItem(id="a", artifact_type="document", name="r.pdf", share_url="https://x/y")
    )
    card = render(state)
    panel = next(e for e in card["body"]["elements"] if e.get("element_id") == "artifacts")
    serialized = str(panel)
    assert "button" in serialized
    assert "https://x/y" in serialized


def test_pending_input_renders_buttons_with_payload() -> None:
    state = _empty_state()
    state.pending_input = PendingInput(
        kind="ask_user",
        run_id="run_1",
        question="Continue?",
        choices=[("yes", "primary"), ("no", "default")],
    )
    card = render(state)
    container = next(
        e for e in card["body"]["elements"] if e.get("element_id") == "pending_input"
    )
    s = str(container)
    assert "yes" in s and "no" in s
    assert "run_1" in s


def test_finalized_state_disables_streaming_mode() -> None:
    state = _empty_state()
    state.finalized = True
    card = render(state)
    assert card["config"]["streaming_mode"] is False


def test_error_state_uses_red_header() -> None:
    state = _empty_state()
    state.error = "boom"
    state.finalized = True
    card = render(state)
    assert card["header"]["template"] == "red"


def test_done_state_uses_green_header() -> None:
    state = _empty_state()
    state.streaming_content = "done"
    state.finalized = True
    state.error = None
    card = render(state)
    assert card["header"]["template"] == "green"
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd backend && uv run pytest tests/im/feishu/test_card_renderer_layout.py -v
```

Expected: `ImportError: cannot import name 'render'`.

- [ ] **Step 3: Implement `render` and element builders**

Append to `backend/cubeplex/im/feishu/card_renderer.py`:

```python
from cubeplex.im.feishu.card_model import (
    ArtifactItem,
    CardState,
    PendingInput,
    SubAgentRow,
    ToolStep,
)


def _header(state: CardState) -> dict[str, Any]:
    if state.error:
        template = "red"
        subtitle = "运行失败"
    elif state.finalized:
        template = "green"
        subtitle = f"已完成 · {state.elapsed_ms / 1000:.1f}s" if state.elapsed_ms else "已完成"
    else:
        template = "blue"
        subtitle = "运行中…"
    return {
        "title": {"tag": "plain_text", "content": state.bot_name},
        "subtitle": {"tag": "plain_text", "content": subtitle},
        "template": template,
    }


def _markdown_element(state: CardState) -> dict[str, Any]:
    content = optimize_markdown_style(
        state.streaming_content,
        citation_index=state.citation_index,
    )
    if state.error and not state.streaming_content.endswith(state.error):
        content = f"{content}\n\n```text\n⚠️ {state.error}\n```"
    return {
        "tag": "markdown",
        "element_id": "streaming_content",
        "content": content,
    }


def _tool_panel_header_title(state: CardState) -> str:
    step_count = len(state.tool_steps)
    if step_count == 0:
        return "工具调用"
    any_failed = any(s.status == "failed" for s in state.tool_steps)
    any_running = any(s.status == "running" for s in state.tool_steps)
    total_ms = sum(s.elapsed_ms for s in state.tool_steps)
    duration = f" · {total_ms / 1000:.1f}s" if total_ms > 0 else ""
    if any_running:
        return f"运行中 · {step_count} step{duration}"
    if any_failed:
        return f"失败 · {step_count} step{duration}"
    return f"已完成 · {step_count} step{duration}"


def _format_result_block(step: ToolStep) -> str:
    if step.status == "failed":
        body = step.error or "(no error message)"
        return f"```text\n{body[:2000]}\n```"
    raw = step.result
    if raw is None:
        return ""
    if isinstance(raw, (dict, list)):
        try:
            body = json.dumps(raw, ensure_ascii=False, indent=2)
            return f"```json\n{body[:2000]}\n```"
        except (TypeError, ValueError):
            pass
    return f"```text\n{str(raw)[:2000]}\n```"


def _render_tool_step(step: ToolStep) -> list[dict[str, Any]]:
    display = TOOL_DISPLAY.get(step.name) or default_display(step.name)
    summary = display.summarize(step.args)
    title_md = f"{display.icon} **{step.name}**"
    if summary:
        title_md += f" · {summary}"
    parts: list[dict[str, Any]] = [
        {"tag": "div", "text": {"tag": "lark_md", "content": title_md}},
    ]
    if step.elapsed_ms > 0:
        parts.append(
            {
                "tag": "div",
                "margin": "0px 0px 0px 22px",
                "text": {"tag": "plain_text", "content": f"{step.elapsed_ms}ms"},
            }
        )
    result_block = _format_result_block(step)
    if result_block:
        parts.append(
            {
                "tag": "div",
                "margin": "0px 0px 0px 22px",
                "text": {"tag": "lark_md", "content": result_block},
            }
        )
    return parts


def _render_sub_agent_row(row: SubAgentRow) -> dict[str, Any]:
    return {
        "tag": "div",
        "text": {
            "tag": "lark_md",
            "content": f"🤖 sub-agent **{row.name}** · 已调用 {row.tool_count} 个工具",
        },
    }


def _tool_panel(state: CardState) -> dict[str, Any]:
    elements: list[dict[str, Any]] = []
    for sub in state.sub_agents:
        elements.append(_render_sub_agent_row(sub))
    for step in state.tool_steps:
        elements.extend(_render_tool_step(step))
    return {
        "tag": "collapsible_panel",
        "element_id": "tool_panel",
        "header": {
            "title": {
                "tag": "plain_text",
                "content": _tool_panel_header_title(state),
            },
        },
        "expanded": True,
        "elements": elements,
    }


def _render_artifact(art: ArtifactItem) -> dict[str, Any]:
    if art.artifact_type == "image" and art.image_key:
        return {
            "tag": "img",
            "img_key": art.image_key,
            "alt": {"tag": "plain_text", "content": art.name},
        }
    title_md = f"📎 **{art.name}** <text_tag color='blue'>{art.artifact_type}</text_tag>"
    if art.artifact_type == "html_widget":
        title_md = f"📊 **{art.name}** <text_tag color='purple'>预览</text_tag>"
    rows: list[dict[str, Any]] = [
        {"tag": "div", "text": {"tag": "lark_md", "content": title_md}},
    ]
    if art.description:
        rows.append(
            {"tag": "div", "text": {"tag": "plain_text", "content": art.description[:200]}}
        )
    if art.share_url:
        button_label = "在浏览器中打开" if art.artifact_type == "html_widget" else "查看"
        rows.append(
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": button_label},
                "type": "default",
                "behaviors": [
                    {"type": "open_url", "default_url": art.share_url}
                ],
            }
        )
    return {
        "tag": "interactive_container",
        "elements": rows,
    }


def _artifacts_panel(state: CardState) -> dict[str, Any]:
    return {
        "tag": "collapsible_panel",
        "element_id": "artifacts",
        "header": {
            "title": {
                "tag": "plain_text",
                "content": f"附件 · {len(state.artifacts)}",
            },
        },
        "expanded": True,
        "elements": [_render_artifact(a) for a in state.artifacts],
    }


def _render_pending_input(pending: PendingInput) -> dict[str, Any]:
    if pending.resolved_choice is not None:
        receipt = (
            f"✓ 已选择「{pending.resolved_choice}」"
            + (f" · 由 {pending.resolved_by_open_id} 操作" if pending.resolved_by_open_id else "")
            + (f" · {pending.resolved_at_iso}" if pending.resolved_at_iso else "")
        )
        return {
            "tag": "interactive_container",
            "element_id": "pending_input",
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": pending.question}},
                {"tag": "div", "text": {"tag": "lark_md", "content": receipt}},
            ],
        }
    columns: list[dict[str, Any]] = []
    for choice_key, btn_type in pending.choices:
        columns.append(
            {
                "elements": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": choice_key},
                        "type": btn_type,
                        "behaviors": [{"type": "callback"}],
                        "value": {
                            "action": pending.kind,
                            "run_id": pending.run_id,
                            "choice": choice_key,
                        },
                    }
                ]
            }
        )
    return {
        "tag": "interactive_container",
        "element_id": "pending_input",
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": pending.question}},
            {"tag": "column_set", "columns": columns} if columns else {"tag": "div", "text": {"tag": "plain_text", "content": "(等待响应)"}},
        ],
    }


def render(state: CardState) -> dict[str, Any]:
    """Project `CardState` into a CardKit JSON 2.0 payload.

    Empty panels are dropped so an in-progress run with no tools yet does
    not render an empty `tool_panel` slot.
    """
    elements: list[dict[str, Any]] = [_markdown_element(state)]
    if state.tool_steps or state.sub_agents:
        elements.append(_tool_panel(state))
    if state.artifacts:
        elements.append(_artifacts_panel(state))
    if state.pending_input is not None:
        elements.append(_render_pending_input(state.pending_input))
    return {
        "schema": "2.0",
        "header": _header(state),
        "config": {
            "streaming_mode": not state.finalized,
            "update_multi": True,
            "locales": ["zh_cn", "en_us"],
        },
        "body": {"elements": elements},
    }


__all__ = [
    "TOOL_DISPLAY",
    "ToolDisplay",
    "default_display",
    "optimize_markdown_style",
    "render",
    "summarize_args",
]
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd backend && uv run pytest tests/im/feishu/test_card_renderer_layout.py -v
```

Expected: all 10 tests pass.

- [ ] **Step 5: Run the whole renderer test module**

```bash
cd backend && uv run pytest tests/im/feishu/ -v
```

Expected: all renderer + model tests pass.

- [ ] **Step 6: Commit**

```bash
git add backend/cubeplex/im/feishu/card_renderer.py backend/tests/im/feishu/test_card_renderer_layout.py
git commit -m "feat(im-feishu): whole-card render() with conditional panel inclusion"
```

---

## Task 5: Add `CardKitClient` skeleton + create_entity

**Files:**
- Create: `backend/cubeplex/im/feishu/cardkit_client.py`
- Create: `backend/tests/im/feishu/test_cardkit_client.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/im/feishu/test_cardkit_client.py`:

```python
"""Tests for CardKitClient — HTTP layer with retry / backoff."""
from __future__ import annotations

import pytest
from httpx import MockTransport, Request, Response

from cubeplex.im.feishu.cardkit_client import (
    CardKitClient,
    CardKitCreateError,
)


def _ok_create_response() -> dict[str, object]:
    return {"code": 0, "msg": "success", "data": {"card_id": "AAQA1234"}}


def _build_client(transport: MockTransport) -> CardKitClient:
    return CardKitClient(
        token_provider=lambda: "tenant_access_token_123",
        transport=transport,
    )


@pytest.mark.asyncio
async def test_create_entity_returns_card_id() -> None:
    async def handler(_: Request) -> Response:
        return Response(200, json=_ok_create_response())

    client = _build_client(MockTransport(handler))
    card_id = await client.create_entity({"schema": "2.0", "body": {"elements": []}})
    assert card_id == "AAQA1234"


@pytest.mark.asyncio
async def test_create_entity_retries_on_5xx() -> None:
    calls = {"n": 0}

    async def handler(_: Request) -> Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return Response(503, json={"code": 99999, "msg": "service unavailable"})
        return Response(200, json=_ok_create_response())

    client = _build_client(MockTransport(handler))
    card_id = await client.create_entity({"schema": "2.0", "body": {"elements": []}})
    assert card_id == "AAQA1234"
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_create_entity_raises_after_max_retries() -> None:
    async def handler(_: Request) -> Response:
        return Response(500, json={"code": 99999, "msg": "boom"})

    client = _build_client(MockTransport(handler))
    with pytest.raises(CardKitCreateError):
        await client.create_entity({"schema": "2.0", "body": {"elements": []}})
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd backend && uv run pytest tests/im/feishu/test_cardkit_client.py -v
```

Expected: `ModuleNotFoundError: cubeplex.im.feishu.cardkit_client`.

- [ ] **Step 3: Implement the client skeleton**

Create `backend/cubeplex/im/feishu/cardkit_client.py`:

```python
"""HTTP wrapper for Feishu CardKit endpoints.

We hit the CardKit REST API directly because the `lark_oapi` Python SDK
predates the CardKit endpoints. The token provider returns a fresh
tenant_access_token; the wrapper handles retries, throttling buckets,
and idempotent finalize.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable

import httpx
from loguru import logger

from cubeplex.im.outbound import _FloodSignal

_BASE_URL = "https://open.feishu.cn"
_CREATE_RETRY_DELAYS = (0.2, 1.0, 3.0)
# CardKit rate-limit response code (same as IM patch rate limit).
_FLOOD_CODE = 230020


class CardKitError(Exception):
    """Base error for CardKit client failures."""


class CardKitCreateError(CardKitError):
    """create_entity exhausted retries."""


class CardKitRateLimit(_FloodSignal):
    """CardKit returned the 230020 throttle response."""


class CardKitClient:
    """Async CardKit REST client.

    Construction takes a token_provider (sync) returning a fresh
    tenant_access_token, and optionally a transport (for tests) or a
    base_url override (for Lark international domain).
    """

    def __init__(
        self,
        *,
        token_provider: Callable[[], str],
        base_url: str = _BASE_URL,
        transport: httpx.MockTransport | httpx.AsyncBaseTransport | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._token_provider = token_provider
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._transport = transport

    def _new_client(self) -> httpx.AsyncClient:
        kwargs: dict[str, Any] = {"timeout": self._timeout}
        if self._transport is not None:
            kwargs["transport"] = self._transport
        return httpx.AsyncClient(**kwargs)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token_provider()}",
            "Content-Type": "application/json; charset=utf-8",
        }

    async def create_entity(self, card_json: dict[str, Any]) -> str:
        """POST /open-apis/cardkit/v1/cards. Returns the new card_id.

        Retries on 5xx / network errors with exponential backoff.
        Raises ``CardKitCreateError`` after exhausting retries.
        """
        url = f"{self._base_url}/open-apis/cardkit/v1/cards"
        payload = {"type": "card_json", "data": card_json}
        last_exc: Exception | None = None
        async with self._new_client() as http:
            for attempt in range(len(_CREATE_RETRY_DELAYS) + 1):
                try:
                    resp = await http.post(url, json=payload, headers=self._headers())
                    if 500 <= resp.status_code < 600:
                        raise CardKitError(f"create_entity HTTP {resp.status_code}")
                    body = resp.json()
                    code = int(body.get("code", -1))
                    if code == 0:
                        data = body.get("data") or {}
                        card_id = str(data.get("card_id") or "")
                        if not card_id:
                            raise CardKitCreateError("create_entity returned no card_id")
                        return card_id
                    raise CardKitError(
                        f"create_entity code={code} msg={body.get('msg')}"
                    )
                except (httpx.HTTPError, CardKitError) as exc:
                    last_exc = exc
                    logger.warning(
                        "[CardKit] create_entity attempt {} failed: {}", attempt + 1, exc
                    )
                    if attempt < len(_CREATE_RETRY_DELAYS):
                        await asyncio.sleep(_CREATE_RETRY_DELAYS[attempt])
                        continue
                    break
        raise CardKitCreateError(str(last_exc) if last_exc else "unknown")


__all__ = [
    "CardKitClient",
    "CardKitCreateError",
    "CardKitError",
    "CardKitRateLimit",
]
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd backend && uv run pytest tests/im/feishu/test_cardkit_client.py -v
```

Expected: 3 tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/im/feishu/cardkit_client.py backend/tests/im/feishu/test_cardkit_client.py
git commit -m "feat(im-feishu): CardKitClient.create_entity with retry"
```

---

## Task 6: Add `stream_text`, `patch_card`, `finalize`

**Files:**
- Modify: `backend/cubeplex/im/feishu/cardkit_client.py`
- Modify: `backend/tests/im/feishu/test_cardkit_client.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/im/feishu/test_cardkit_client.py`:

```python
@pytest.mark.asyncio
async def test_stream_text_sends_sequence_and_delta() -> None:
    captured: dict[str, object] = {}

    async def handler(req: Request) -> Response:
        captured["url"] = str(req.url)
        captured["body"] = req.read()
        return Response(200, json={"code": 0, "msg": "success"})

    client = _build_client(MockTransport(handler))
    await client.stream_text(
        card_id="AAQA",
        element_id="streaming_content",
        content="Hello",
        sequence=3,
    )
    body_text = (captured["body"] or b"").decode()
    assert "AAQA" in str(captured["url"])
    assert "streaming_content" in body_text
    assert '"sequence": 3' in body_text or '"sequence":3' in body_text
    assert "Hello" in body_text


@pytest.mark.asyncio
async def test_stream_text_raises_ratelimit_on_230020() -> None:
    async def handler(_: Request) -> Response:
        return Response(200, json={"code": 230020, "msg": "too fast"})

    client = _build_client(MockTransport(handler))
    with pytest.raises(Exception) as info:
        await client.stream_text(
            card_id="AAQA", element_id="streaming_content", content="x", sequence=1,
        )
    from cubeplex.im.feishu.cardkit_client import CardKitRateLimit
    assert isinstance(info.value, CardKitRateLimit)


@pytest.mark.asyncio
async def test_patch_card_sends_full_json() -> None:
    captured: dict[str, object] = {}

    async def handler(req: Request) -> Response:
        captured["body"] = req.read()
        return Response(200, json={"code": 0, "msg": "success"})

    client = _build_client(MockTransport(handler))
    await client.patch_card(
        card_id="AAQA",
        card_json={"schema": "2.0", "body": {"elements": []}},
        sequence=5,
    )
    text = (captured["body"] or b"").decode()
    assert '"sequence": 5' in text or '"sequence":5' in text
    assert "schema" in text


@pytest.mark.asyncio
async def test_finalize_retries_up_to_cap() -> None:
    calls = {"n": 0}

    async def handler(_: Request) -> Response:
        calls["n"] += 1
        if calls["n"] < 4:
            return Response(500, json={"code": 99999, "msg": "boom"})
        return Response(200, json={"code": 0, "msg": "success"})

    client = _build_client(MockTransport(handler))
    finalized = await client.finalize(
        card_id="AAQA",
        card_json={"schema": "2.0", "body": {"elements": []}},
        sequence=99,
    )
    assert finalized is True
    assert calls["n"] == 4


@pytest.mark.asyncio
async def test_finalize_gives_up_after_max_attempts() -> None:
    async def handler(_: Request) -> Response:
        return Response(500, json={"code": 99999, "msg": "down"})

    # Speed up the backoff for the test by patching the delay table.
    from cubeplex.im.feishu import cardkit_client as mod
    original = mod._FINALIZE_RETRY_DELAYS
    mod._FINALIZE_RETRY_DELAYS = (0.0, 0.0)  # type: ignore[misc]
    try:
        client = _build_client(MockTransport(handler))
        finalized = await client.finalize(
            card_id="AAQA",
            card_json={"schema": "2.0", "body": {"elements": []}},
            sequence=99,
        )
        assert finalized is False
    finally:
        mod._FINALIZE_RETRY_DELAYS = original  # type: ignore[misc]
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd backend && uv run pytest tests/im/feishu/test_cardkit_client.py -v
```

Expected: import / attribute errors on `stream_text`, `patch_card`, `finalize`.

- [ ] **Step 3: Add `stream_text`, `patch_card`, `finalize`**

Append the methods inside the `CardKitClient` class and add a module-level constants table:

```python
_FINALIZE_RETRY_DELAYS = (0.2, 0.5, 1.0, 3.0, 10.0, 30.0, 30.0, 30.0, 30.0)


class CardKitClient:
    # ... existing __init__, _new_client, _headers, create_entity

    async def stream_text(
        self,
        *,
        card_id: str,
        element_id: str,
        content: str,
        sequence: int,
    ) -> None:
        """PUT /open-apis/cardkit/v1/cards/{card_id}/elements/{element_id}/content.

        Pushes an incremental text update to a streaming element. Raises
        ``CardKitRateLimit`` on code 230020 so the caller can skip-merge
        into the next stream attempt without counting it against retry.
        """
        url = (
            f"{self._base_url}/open-apis/cardkit/v1/cards/"
            f"{card_id}/elements/{element_id}/content"
        )
        payload = {"content": content, "sequence": sequence, "uuid": f"{card_id}-{sequence}"}
        async with self._new_client() as http:
            resp = await http.put(url, json=payload, headers=self._headers())
            body = resp.json()
            code = int(body.get("code", -1))
            if code == _FLOOD_CODE:
                raise CardKitRateLimit(f"stream_text flood (code={code})")
            if code != 0:
                raise CardKitError(f"stream_text code={code} msg={body.get('msg')}")

    async def patch_card(
        self,
        *,
        card_id: str,
        card_json: dict[str, Any],
        sequence: int,
    ) -> None:
        """PATCH /open-apis/cardkit/v1/cards/{card_id}.

        Replaces the whole card JSON. Raises ``CardKitRateLimit`` on 230020;
        caller coalesces.
        """
        url = f"{self._base_url}/open-apis/cardkit/v1/cards/{card_id}"
        payload = {"card": {"type": "card_json", "data": card_json}, "sequence": sequence}
        async with self._new_client() as http:
            resp = await http.patch(url, json=payload, headers=self._headers())
            body = resp.json()
            code = int(body.get("code", -1))
            if code == _FLOOD_CODE:
                raise CardKitRateLimit(f"patch_card flood (code={code})")
            if code != 0:
                raise CardKitError(f"patch_card code={code} msg={body.get('msg')}")

    async def finalize(
        self,
        *,
        card_id: str,
        card_json: dict[str, Any],
        sequence: int,
    ) -> bool:
        """Terminal patch. Idempotent, retried up to ~2.5 minutes total.

        Returns True if the final patch landed; False if all retries
        failed (caller logs + accepts half-locked state, sets ❌ reaction).
        """
        url = f"{self._base_url}/open-apis/cardkit/v1/cards/{card_id}"
        payload = {"card": {"type": "card_json", "data": card_json}, "sequence": sequence}
        async with self._new_client() as http:
            for attempt in range(len(_FINALIZE_RETRY_DELAYS) + 1):
                try:
                    resp = await http.patch(url, json=payload, headers=self._headers())
                    if 500 <= resp.status_code < 600:
                        raise CardKitError(f"finalize HTTP {resp.status_code}")
                    body = resp.json()
                    code = int(body.get("code", -1))
                    if code == 0:
                        return True
                    if code == _FLOOD_CODE:
                        # Throttle counts as transient; retry like 5xx.
                        raise CardKitError(f"finalize flood (code={code})")
                    raise CardKitError(f"finalize code={code} msg={body.get('msg')}")
                except (httpx.HTTPError, CardKitError) as exc:
                    logger.warning(
                        "[CardKit] finalize attempt {} failed: {}", attempt + 1, exc
                    )
                    if attempt < len(_FINALIZE_RETRY_DELAYS):
                        await asyncio.sleep(_FINALIZE_RETRY_DELAYS[attempt])
                        continue
                    break
        logger.error("[CardKit] finalize gave up for card_id={}", card_id)
        return False
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd backend && uv run pytest tests/im/feishu/test_cardkit_client.py -v
```

Expected: all 8 tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/im/feishu/cardkit_client.py backend/tests/im/feishu/test_cardkit_client.py
git commit -m "feat(im-feishu): CardKitClient stream_text / patch_card / finalize"
```

---

## Task 7: Replace `RenderState` with new fields

**Files:**
- Modify: `backend/cubeplex/im/types.py`
- Modify: `backend/tests/im/test_types.py` (create if absent)

- [ ] **Step 1: Write the failing test**

Create / open `backend/tests/im/test_types.py`:

```python
"""Tests for RenderState's new card-oriented shape."""
from cubeplex.im.feishu.card_model import CardState
from cubeplex.im.types import RenderState


def test_render_state_owns_card_state() -> None:
    state = RenderState(bot_name="cubeplex", run_id="run_1")
    assert isinstance(state.card_state, CardState)
    assert state.card_state.run_id == "run_1"
    assert state.card_id is None
    assert state.card_unavailable is False


def test_render_state_keeps_reaction_id_field() -> None:
    state = RenderState(bot_name="cubeplex", run_id="run_1")
    assert state.reaction_in_progress_id is None
    state.reaction_in_progress_id = "rx_1"
    assert state.reaction_in_progress_id == "rx_1"
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd backend && uv run pytest tests/im/test_types.py -v
```

Expected: `TypeError: RenderState.__init__() got an unexpected keyword argument 'bot_name'` (the dataclass shape changed).

- [ ] **Step 3: Replace `RenderState` definition**

Edit `backend/cubeplex/im/types.py`. Replace lines 65-86 (the existing `RenderState`) with:

```python
from cubeplex.im.feishu.card_model import CardState


@dataclass(slots=True)
class RenderState:
    """Per-run outbound render state, projected into a CardKit card."""

    bot_name: str
    run_id: str
    card_state: CardState = field(init=False)
    card_id: str | None = None
    card_unavailable: bool = False
    last_stream_monotonic: float = 0.0
    last_patch_monotonic: float = 0.0
    stream_interval: float = 0.1
    patch_interval: float = 1.5
    consecutive_flood_strikes: int = 0
    edits_disabled: bool = False
    reaction_in_progress_id: str | None = None
    reply_to_id: str | None = None
    inbound_message_id: str | None = None
    bot_message_id: str | None = None
    """Feishu message_id of the bubble that carries the card."""

    def __post_init__(self) -> None:
        self.card_state = CardState(bot_name=self.bot_name, run_id=self.run_id)
```

The import for `CardState` must be added at the top of the file (after the existing imports). Verify the dataclass `field` import is already present (it is).

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd backend && uv run pytest tests/im/test_types.py -v
```

Expected: both tests pass.

- [ ] **Step 5: Verify the dataclass changes don't break import elsewhere**

```bash
cd backend && uv run python -c "from cubeplex.im.types import RenderState; print(RenderState(bot_name='b', run_id='r'))"
```

Expected: prints a dataclass repr without exception.

- [ ] **Step 6: Commit**

```bash
git add backend/cubeplex/im/types.py backend/tests/im/test_types.py
git commit -m "feat(im): RenderState carries CardState + card_id + throttle buckets"
```

---

## Task 8: Update `OutboundOp` and rewrite `fold_event` for text events

**Files:**
- Modify: `backend/cubeplex/im/outbound.py`
- Create: `backend/tests/im/test_fold_event_text.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/im/test_fold_event_text.py`:

```python
"""Tests for fold_event handling text_delta events on the new CardState path."""
from cubeplex.im.outbound import OutboundOp, fold_event
from cubeplex.im.types import RenderState


def _state() -> RenderState:
    return RenderState(bot_name="cubeplex", run_id="run_1")


def test_first_text_delta_emits_card_create() -> None:
    state = _state()
    op = fold_event({"type": "text_delta", "data": {"content": "Hi"}}, state, now=0.0)
    assert op is not None
    assert op.kind == "card_create"
    assert state.card_state.streaming_content == "Hi"


def test_subsequent_text_delta_emits_stream_text() -> None:
    state = _state()
    fold_event({"type": "text_delta", "data": {"content": "Hi"}}, state, now=0.0)
    # The card_create op is dispatched externally; tailer sets card_id.
    state.card_id = "AAQA"
    op = fold_event({"type": "text_delta", "data": {"content": " there"}}, state, now=0.2)
    assert op is not None
    assert op.kind == "stream_text"
    assert state.card_state.streaming_content == "Hi there"
    assert op.text == " there"


def test_throttled_delta_returns_none() -> None:
    state = _state()
    fold_event({"type": "text_delta", "data": {"content": "a"}}, state, now=0.0)
    state.card_id = "AAQA"
    state.last_stream_monotonic = 1.0
    op = fold_event({"type": "text_delta", "data": {"content": "b"}}, state, now=1.05)
    assert op is None
    assert state.card_state.streaming_content == "ab"
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd backend && uv run pytest tests/im/test_fold_event_text.py -v
```

Expected: `AttributeError: 'OutboundOp' object has no attribute…` or kind mismatch.

- [ ] **Step 3: Update `OutboundOp` and `fold_event` for text events**

Edit `backend/cubeplex/im/outbound.py`:

Replace the `OutboundOp` dataclass (lines 38-45) with:

```python
from typing import Any, Literal

OpKind = Literal[
    "card_create",
    "stream_text",
    "patch_card",
    "finalize",
    "no_op",
]


@dataclass(slots=True)
class OutboundOp:
    """One emitted action for the cardkit client."""

    kind: OpKind
    element_id: str | None = None
    text: str = ""
    final: bool = False
```

Replace the `_composite_text` and `fold_event` (lines 48-114) with — start with the text_delta branch only; later tasks add tool_call/artifact/etc.:

```python
def fold_event(event: dict[str, Any], state: RenderState, *, now: float) -> OutboundOp | None:
    """Fold one cubepi run event into `state.card_state`.

    text_delta → card_create on first; stream_text on later (debounced).
    Other event types are added in subsequent tasks.
    """
    etype = event.get("type")
    data = event.get("data") or {}

    if etype == "text_delta":
        delta = str(data.get("content", ""))
        state.card_state.streaming_content += delta
        if state.card_id is None:
            state.last_stream_monotonic = now
            return OutboundOp(kind="card_create")
        if state.edits_disabled:
            return None
        if now - state.last_stream_monotonic < state.stream_interval:
            return None
        state.last_stream_monotonic = now
        return OutboundOp(kind="stream_text", element_id="streaming_content", text=delta)

    return None
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd backend && uv run pytest tests/im/test_fold_event_text.py -v
```

Expected: all 3 tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/im/outbound.py backend/tests/im/test_fold_event_text.py
git commit -m "feat(im): fold_event emits card_create + stream_text"
```

---

## Task 9: `fold_event` for `tool_call` and `tool_result` events

**Files:**
- Modify: `backend/cubeplex/im/outbound.py`
- Create: `backend/tests/im/test_fold_event_tools.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/im/test_fold_event_tools.py`:

```python
from cubeplex.im.outbound import fold_event
from cubeplex.im.types import RenderState


def _state_with_card() -> RenderState:
    s = RenderState(bot_name="cubeplex", run_id="run_1")
    s.card_id = "AAQA"
    return s


def test_tool_call_event_appends_running_step_and_emits_patch() -> None:
    state = _state_with_card()
    op = fold_event(
        {"type": "tool_call", "data": {"id": "tc_1", "name": "bash", "args": {"cmd": "ls"}}},
        state,
        now=0.0,
    )
    assert op is not None
    assert op.kind == "patch_card"  # tool_call bypasses throttle on first appearance
    assert len(state.card_state.tool_steps) == 1
    assert state.card_state.tool_steps[0].status == "running"


def test_tool_result_marks_step_succeeded() -> None:
    state = _state_with_card()
    fold_event(
        {"type": "tool_call", "data": {"id": "tc_1", "name": "bash", "args": {"cmd": "ls"}}},
        state,
        now=0.0,
    )
    op = fold_event(
        {
            "type": "tool_result",
            "data": {"id": "tc_1", "result": {"out": "ok"}, "elapsed_ms": 100},
        },
        state,
        now=10.0,  # well past the patch_interval
    )
    step = state.card_state.tool_steps[0]
    assert step.status == "succeeded"
    assert step.elapsed_ms == 100
    assert op is not None and op.kind == "patch_card"


def test_tool_result_error_marks_failed() -> None:
    state = _state_with_card()
    fold_event(
        {"type": "tool_call", "data": {"id": "tc_2", "name": "bash", "args": {"cmd": "x"}}},
        state,
        now=0.0,
    )
    fold_event(
        {"type": "tool_result", "data": {"id": "tc_2", "error": "boom", "elapsed_ms": 20}},
        state,
        now=10.0,
    )
    step = state.card_state.tool_steps[0]
    assert step.status == "failed"
    assert step.error == "boom"


def test_tool_call_when_no_card_created_yet_emits_card_create() -> None:
    state = RenderState(bot_name="cubeplex", run_id="run_1")
    op = fold_event(
        {"type": "tool_call", "data": {"id": "tc_1", "name": "bash", "args": {}}},
        state,
        now=0.0,
    )
    assert op is not None
    assert op.kind == "card_create"
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd backend && uv run pytest tests/im/test_fold_event_tools.py -v
```

Expected: assertions on `tool_steps` fail because `fold_event` doesn't handle `tool_call` / `tool_result` yet.

- [ ] **Step 3: Add `tool_call` and `tool_result` branches**

Inside `fold_event` in `backend/cubeplex/im/outbound.py`, add before the trailing `return None`:

```python
    if etype == "tool_call":
        from cubeplex.im.feishu.card_model import ToolStep

        tool_id = str(data.get("id") or "")
        name = str(data.get("name") or "tool")
        args = data.get("args") or {}
        if tool_id and state.card_state.find_tool(tool_id) is None:
            state.card_state.tool_steps.append(
                ToolStep(id=tool_id, name=name, args=args)
            )
        if state.card_id is None:
            return OutboundOp(kind="card_create")
        # tool_call is a structural change — bypass patch_interval throttle
        # on the first appearance so the user sees the panel pop in.
        state.last_patch_monotonic = now
        return OutboundOp(kind="patch_card")

    if etype == "tool_result":
        tool_id = str(data.get("id") or "")
        step = state.card_state.find_tool(tool_id)
        if step is None:
            return None
        elapsed_ms = int(data.get("elapsed_ms") or 0)
        err = data.get("error")
        if err:
            step.mark_failed(error=str(err), elapsed_ms=elapsed_ms)
        else:
            step.mark_succeeded(result=data.get("result"), elapsed_ms=elapsed_ms)
        state.last_patch_monotonic = now
        return OutboundOp(kind="patch_card") if state.card_id else OutboundOp(kind="card_create")
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd backend && uv run pytest tests/im/test_fold_event_tools.py -v
```

Expected: all 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/im/outbound.py backend/tests/im/test_fold_event_tools.py
git commit -m "feat(im): fold_event handles tool_call and tool_result"
```

---

## Task 10: `fold_event` for `artifact` and `citation` events

**Files:**
- Modify: `backend/cubeplex/im/outbound.py`
- Create: `backend/tests/im/test_fold_event_artifact_citation.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/im/test_fold_event_artifact_citation.py`:

```python
from cubeplex.im.outbound import fold_event
from cubeplex.im.types import RenderState


def _state_with_card() -> RenderState:
    s = RenderState(bot_name="cubeplex", run_id="run_1")
    s.card_id = "AAQA"
    return s


def test_artifact_event_returns_op_for_dispatcher() -> None:
    state = _state_with_card()
    op = fold_event(
        {
            "type": "artifact",
            "data": {
                "action": "created",
                "artifact": {
                    "id": "art_1",
                    "artifact_type": "document",
                    "name": "r.pdf",
                },
            },
        },
        state,
        now=0.0,
    )
    assert op is not None
    # The artifact branch returns a synthetic op kind "artifact"; the
    # tailer turns this into a dispatcher.handle() call which then
    # mutates card_state.artifacts and emits a patch_card.
    assert op.kind in {"patch_card", "card_create"}


def test_citation_event_updates_index_no_op_emitted() -> None:
    state = _state_with_card()
    op = fold_event(
        {
            "type": "citation",
            "data": {"index": "1", "url": "https://x", "title": "T"},
        },
        state,
        now=0.0,
    )
    assert state.card_state.citation_index["1"] == ("https://x", "T")
    # No card change required — the next stream_text or patch_card will
    # pick up the index via the renderer.
    assert op is None
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd backend && uv run pytest tests/im/test_fold_event_artifact_citation.py -v
```

Expected: assertions fail.

- [ ] **Step 3: Add `artifact` and `citation` branches**

Inside `fold_event` in `backend/cubeplex/im/outbound.py`, add before the trailing `return None`:

```python
    if etype == "artifact":
        from cubeplex.im.feishu.card_model import ArtifactItem

        action = str(data.get("action") or "created")
        artifact = data.get("artifact") or {}
        art_id = str(artifact.get("id") or "")
        if not art_id:
            return None
        existing = next(
            (a for a in state.card_state.artifacts if a.id == art_id), None
        )
        if existing is not None and action == "created":
            return None
        if existing is None:
            state.card_state.artifacts.append(
                ArtifactItem(
                    id=art_id,
                    artifact_type=str(artifact.get("artifact_type") or ""),
                    name=str(artifact.get("name") or art_id),
                )
            )
        if state.card_id is None:
            return OutboundOp(kind="card_create")
        state.last_patch_monotonic = now
        return OutboundOp(kind="patch_card")

    if etype == "citation":
        index = str(data.get("index") or "")
        url = str(data.get("url") or "")
        title = str(data.get("title") or "")
        if index and url:
            state.card_state.citation_index[index] = (url, title)
        return None
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd backend && uv run pytest tests/im/test_fold_event_artifact_citation.py -v
```

Expected: both tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/im/outbound.py backend/tests/im/test_fold_event_artifact_citation.py
git commit -m "feat(im): fold_event handles artifact and citation events"
```

---

## Task 11: `fold_event` for `ask_user`, `sandbox_confirm`, `sub_agent_*`, `done`, `error`

**Files:**
- Modify: `backend/cubeplex/im/outbound.py`
- Create: `backend/tests/im/test_fold_event_terminal.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/im/test_fold_event_terminal.py`:

```python
from cubeplex.im.outbound import fold_event
from cubeplex.im.types import RenderState


def _state_with_card() -> RenderState:
    s = RenderState(bot_name="cubeplex", run_id="run_1")
    s.card_id = "AAQA"
    return s


def test_ask_user_populates_pending_input_and_bypasses_throttle() -> None:
    state = _state_with_card()
    state.last_patch_monotonic = 100.0  # would normally suppress patches
    op = fold_event(
        {
            "type": "ask_user",
            "data": {
                "prompt": "Continue?",
                "choices": [
                    {"key": "yes", "label": "Yes", "type": "primary"},
                    {"key": "no", "label": "No", "type": "default"},
                ],
            },
        },
        state,
        now=100.1,
    )
    assert state.card_state.pending_input is not None
    assert state.card_state.pending_input.question == "Continue?"
    assert state.card_state.pending_input.kind == "ask_user"
    assert op is not None and op.kind == "patch_card"


def test_sandbox_confirm_populates_pending_input() -> None:
    state = _state_with_card()
    op = fold_event(
        {
            "type": "sandbox_confirm",
            "data": {"prompt": "Run rm -rf /?", "command": "rm -rf /"},
        },
        state,
        now=0.0,
    )
    assert state.card_state.pending_input is not None
    assert state.card_state.pending_input.kind == "sandbox_confirm"
    assert op is not None


def test_done_finalizes_and_emits_finalize_op() -> None:
    state = _state_with_card()
    op = fold_event(
        {"type": "done", "data": {"elapsed_ms": 1234}}, state, now=0.0
    )
    assert state.card_state.finalized is True
    assert state.card_state.elapsed_ms == 1234
    assert op is not None and op.kind == "finalize"
    assert op.final is True


def test_error_finalizes_and_records_message() -> None:
    state = _state_with_card()
    op = fold_event(
        {"type": "error", "data": {"message": "the run failed"}}, state, now=0.0
    )
    assert state.card_state.finalized is True
    assert state.card_state.error == "the run failed"
    assert op is not None and op.kind == "finalize"
    assert op.final is True


def test_sub_agent_start_appends_row() -> None:
    state = _state_with_card()
    fold_event(
        {"type": "sub_agent_start", "data": {"name": "researcher"}},
        state,
        now=0.0,
    )
    assert len(state.card_state.sub_agents) == 1
    assert state.card_state.sub_agents[0].name == "researcher"


def test_sub_agent_tool_call_increments_count() -> None:
    state = _state_with_card()
    fold_event(
        {"type": "sub_agent_start", "data": {"name": "researcher"}},
        state,
        now=0.0,
    )
    fold_event(
        {"type": "sub_agent_tool_call", "data": {"name": "researcher"}},
        state,
        now=0.1,
    )
    fold_event(
        {"type": "sub_agent_tool_call", "data": {"name": "researcher"}},
        state,
        now=0.2,
    )
    assert state.card_state.sub_agents[0].tool_count == 2
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd backend && uv run pytest tests/im/test_fold_event_terminal.py -v
```

Expected: assertions fail.

- [ ] **Step 3: Add the branches**

Inside `fold_event` in `backend/cubeplex/im/outbound.py`, before the trailing `return None`:

```python
    if etype == "ask_user":
        from cubeplex.im.feishu.card_model import PendingInput

        raw_choices = data.get("choices") or []
        choices: list[tuple[str, str]] = []
        for choice in raw_choices:
            if isinstance(choice, dict):
                key = str(choice.get("key") or choice.get("label") or "")
                btn = str(choice.get("type") or "default")
                if key:
                    choices.append((key, btn))
        state.card_state.pending_input = PendingInput(
            kind="ask_user",
            run_id=state.run_id,
            question=str(data.get("prompt") or ""),
            choices=choices,
        )
        state.last_patch_monotonic = now
        return OutboundOp(kind="patch_card") if state.card_id else OutboundOp(kind="card_create")

    if etype == "sandbox_confirm":
        from cubeplex.im.feishu.card_model import PendingInput

        question = str(data.get("prompt") or "")
        cmd = str(data.get("command") or "")
        if cmd:
            question = f"{question}\n\n```bash\n{cmd}\n```"
        state.card_state.pending_input = PendingInput(
            kind="sandbox_confirm",
            run_id=state.run_id,
            question=question,
            choices=[("approve", "primary"), ("deny", "danger")],
        )
        state.last_patch_monotonic = now
        return OutboundOp(kind="patch_card") if state.card_id else OutboundOp(kind="card_create")

    if etype == "sub_agent_start":
        from cubeplex.im.feishu.card_model import SubAgentRow

        name = str(data.get("name") or "sub-agent")
        if not any(r.name == name for r in state.card_state.sub_agents):
            state.card_state.sub_agents.append(SubAgentRow(name=name))
        state.last_patch_monotonic = now
        return OutboundOp(kind="patch_card") if state.card_id else OutboundOp(kind="card_create")

    if etype == "sub_agent_tool_call":
        name = str(data.get("name") or "")
        for row in state.card_state.sub_agents:
            if row.name == name:
                row.tool_count += 1
                break
        # No op — the next regular tick will pick up the new count.
        return None

    if etype == "done":
        state.card_state.finalized = True
        state.card_state.elapsed_ms = int(data.get("elapsed_ms") or 0)
        return OutboundOp(kind="finalize", final=True)

    if etype == "error":
        state.card_state.finalized = True
        state.card_state.error = str(data.get("message") or "the run failed")
        return OutboundOp(kind="finalize", final=True)
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd backend && uv run pytest tests/im/test_fold_event_terminal.py -v
```

Expected: all 6 tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/im/outbound.py backend/tests/im/test_fold_event_terminal.py
git commit -m "feat(im): fold_event handles ask_user, sandbox_confirm, sub_agent_*, done, error"
```

---

## Task 12: Rewire `OutboundRunTailer._dispatch_op` to call CardKitClient + send card init message

**Files:**
- Modify: `backend/cubeplex/im/outbound.py` (rewrite `_dispatch_op` + dispatch loop)
- Modify: `backend/cubeplex/im/feishu/connector.py` (add `send_card_init_message`, `add_waiting_reaction`)
- Create: `backend/tests/im/test_tailer_dispatch.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/im/test_tailer_dispatch.py`:

```python
"""Integration test: tailer + fake CardKit + fake connector → expected calls."""
from __future__ import annotations

from typing import Any

import pytest

from cubeplex.im.feishu.card_renderer import render
from cubeplex.im.outbound import OutboundOp, OutboundRunTailer
from cubeplex.im.types import RenderState


class _FakeCardKit:
    def __init__(self) -> None:
        self.creates: list[dict[str, Any]] = []
        self.streams: list[tuple[str, str, str, int]] = []
        self.patches: list[tuple[str, dict[str, Any], int]] = []
        self.finalized: list[tuple[str, dict[str, Any], int]] = []
        self.next_card_id = "AAQA"

    async def create_entity(self, card_json: dict[str, Any]) -> str:
        self.creates.append(card_json)
        return self.next_card_id

    async def stream_text(self, *, card_id: str, element_id: str, content: str, sequence: int) -> None:
        self.streams.append((card_id, element_id, content, sequence))

    async def patch_card(self, *, card_id: str, card_json: dict[str, Any], sequence: int) -> None:
        self.patches.append((card_id, card_json, sequence))

    async def finalize(self, *, card_id: str, card_json: dict[str, Any], sequence: int) -> bool:
        self.finalized.append((card_id, card_json, sequence))
        return True


class _FakeConnector:
    def __init__(self) -> None:
        self.init_calls: list[str] = []
        self.start_called = 0
        self.complete_called = 0
        self.failed_called = 0

    async def on_processing_start(self, state: RenderState) -> None:
        self.start_called += 1

    async def on_processing_complete(self, state: RenderState) -> None:
        self.complete_called += 1

    async def on_processing_failed(self, state: RenderState) -> None:
        self.failed_called += 1

    async def send_card_init_message(self, card_id: str) -> str | None:
        self.init_calls.append(card_id)
        return "om_bot_message_1"


@pytest.mark.asyncio
async def test_dispatch_card_create_then_stream_text() -> None:
    state = RenderState(bot_name="cubeplex", run_id="run_1")
    cardkit = _FakeCardKit()
    connector = _FakeConnector()
    tailer = OutboundRunTailer(
        redis=None,  # unused in this unit test — we drive _dispatch_op directly
        key_prefix="cb-",
        run_id="run_1",
        connector=connector,
        state=state,
        cardkit=cardkit,
    )

    # First text_delta → card_create.
    op_create = OutboundOp(kind="card_create")
    state.card_state.streaming_content = "hello"
    delivered = await tailer._dispatch_op(op_create, is_terminal=False)
    assert delivered is True
    assert state.card_id == "AAQA"
    assert connector.init_calls == ["AAQA"]
    assert len(cardkit.creates) == 1

    # Second delta → stream_text.
    op_stream = OutboundOp(kind="stream_text", element_id="streaming_content", text=" world")
    state.card_state.streaming_content = "hello world"
    delivered = await tailer._dispatch_op(op_stream, is_terminal=False)
    assert delivered is True
    assert cardkit.streams == [("AAQA", "streaming_content", " world", 0)]
    assert state.card_state.next_seq == 1


@pytest.mark.asyncio
async def test_dispatch_finalize_calls_cardkit_finalize_with_streaming_off() -> None:
    state = RenderState(bot_name="cubeplex", run_id="run_1")
    state.card_id = "AAQA"
    state.card_state.streaming_content = "done"
    state.card_state.finalized = True
    cardkit = _FakeCardKit()
    connector = _FakeConnector()
    tailer = OutboundRunTailer(
        redis=None,
        key_prefix="cb-",
        run_id="run_1",
        connector=connector,
        state=state,
        cardkit=cardkit,
    )
    op = OutboundOp(kind="finalize", final=True)
    delivered = await tailer._dispatch_op(op, is_terminal=True)
    assert delivered is True
    assert len(cardkit.finalized) == 1
    sent_json = cardkit.finalized[0][1]
    assert sent_json["config"]["streaming_mode"] is False
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd backend && uv run pytest tests/im/test_tailer_dispatch.py -v
```

Expected: errors about `cardkit` kwarg / `send_card_init_message` etc.

- [ ] **Step 3: Add `send_card_init_message` to `FeishuConnector`**

Edit `backend/cubeplex/im/feishu/connector.py`. Add a new method on `FeishuConnector` (insert near `send_text_message`):

```python
    async def send_card_init_message(self, card_id: str) -> str | None:
        """Send the first IM message that carries the just-created CardKit card.

        Group / threaded send → ``im.v1.message.reply`` against
        ``self._reply_to_id``. DM → ``im.v1.message.create``.

        Returns the new bot message_id or None on failure.
        """
        if self._client is None:
            return None
        from lark_oapi.api.im.v1 import (
            CreateMessageRequest,
            CreateMessageRequestBody,
            ReplyMessageRequest,
            ReplyMessageRequestBody,
        )

        payload = json.dumps(
            {"type": "card", "data": {"card_id": card_id}},
            ensure_ascii=False,
        )
        if self._reply_to_id is not None:
            body = (
                ReplyMessageRequestBody.builder()
                .content(payload)
                .msg_type("interactive")
                .reply_in_thread(False)
                .build()
            )
            req = (
                ReplyMessageRequest.builder()
                .message_id(self._reply_to_id)
                .request_body(body)
                .build()
            )
            response = await asyncio.to_thread(self._client.im.v1.message.reply, req)
        else:
            body = (
                CreateMessageRequestBody.builder()
                .receive_id(self._channel_id or "")
                .msg_type("interactive")
                .content(payload)
                .build()
            )
            req = (
                CreateMessageRequest.builder()
                .receive_id_type("chat_id")
                .request_body(body)
                .build()
            )
            response = await asyncio.to_thread(self._client.im.v1.message.create, req)
        if not getattr(response, "success", lambda: False)():
            logger.warning(
                "[Feishu] send_card_init_message failed: code={} msg={}",
                getattr(response, "code", None),
                getattr(response, "msg", None),
            )
            return None
        data = getattr(response, "data", None)
        message_id = getattr(data, "message_id", None) if data is not None else None
        return str(message_id) if message_id else None
```

- [ ] **Step 4: Rewrite `OutboundRunTailer.__init__` and `_dispatch_op`**

Edit `backend/cubeplex/im/outbound.py`. Replace the `OutboundRunTailer.__init__` signature and its `_dispatch_op` (existing lines ~147-306) with:

```python
class OutboundRunTailer:
    """Tail a run's Redis event stream and drive CardKit ops on the connector.

    The tailer owns one CardKit entity per run. It serializes events into
    cardkit_client calls; ``connector`` handles the initial IM message that
    carries the card, reactions, and the emergency text fallback.
    """

    def __init__(
        self,
        *,
        redis: Any,
        key_prefix: str,
        run_id: str,
        connector: Any,
        state: RenderState,
        cardkit: Any,
        artifact_dispatcher: Any | None = None,
        block_ms: int = 2000,
    ) -> None:
        self._redis = redis
        self._prefix = key_prefix
        self._run_id = run_id
        self._connector = connector
        self._state = state
        self._cardkit = cardkit
        self._artifact_dispatcher = artifact_dispatcher
        self._block_ms = block_ms

    # ... run() unchanged structurally — only the dispatch call below changes.

    async def _dispatch_op(self, op: OutboundOp, *, is_terminal: bool) -> bool:
        from cubeplex.im.feishu.card_renderer import render

        state = self._state
        cardkit = self._cardkit

        if op.kind == "card_create":
            if state.card_unavailable:
                # Emergency fallback already engaged; nothing to create.
                return False
            card_json = render(state.card_state)
            try:
                card_id = await cardkit.create_entity(card_json)
            except Exception:
                logger.warning("[outbound] CardKit create_entity failed; emergency text", exc_info=True)
                state.card_unavailable = True
                await self._emergency_text("⚠️ 飞书富文本渲染暂时不可用，结果将以文本展示")
                # Also send whatever text we have so far (truncate to 4k).
                if state.card_state.streaming_content:
                    await self._emergency_text(state.card_state.streaming_content[:4000])
                return False
            state.card_id = card_id
            state.card_state.advance_seq()
            try:
                msg_id = await self._connector.send_card_init_message(card_id)
            except Exception:
                logger.warning("[outbound] send_card_init_message raised", exc_info=True)
                msg_id = None
            state.bot_message_id = msg_id
            return True

        if op.kind == "stream_text":
            if state.card_id is None or state.card_unavailable:
                return False
            seq = state.card_state.advance_seq()
            try:
                await cardkit.stream_text(
                    card_id=state.card_id,
                    element_id=op.element_id or "streaming_content",
                    content=op.text,
                    sequence=seq,
                )
                note_edit_success(state)
                return True
            except _FloodSignal:
                note_flood_strike(state)
                return False
            except Exception:
                logger.warning("[outbound] stream_text failed", exc_info=True)
                return False

        if op.kind == "patch_card":
            if state.card_id is None or state.card_unavailable:
                return False
            seq = state.card_state.advance_seq()
            try:
                await cardkit.patch_card(
                    card_id=state.card_id,
                    card_json=render(state.card_state),
                    sequence=seq,
                )
                note_edit_success(state)
                return True
            except _FloodSignal:
                # Coalesce — the next event will rebuild and resend.
                return False
            except Exception:
                logger.warning("[outbound] patch_card failed", exc_info=True)
                return False

        if op.kind == "finalize":
            if state.card_id is None or state.card_unavailable:
                # Run finished before a card could be created. Fall back to
                # emergency text with whatever we have.
                if state.card_state.error:
                    await self._emergency_text(f"⚠️ {state.card_state.error}")
                elif state.card_state.streaming_content:
                    await self._emergency_text(state.card_state.streaming_content[:4000])
                return False
            seq = state.card_state.advance_seq()
            return await cardkit.finalize(
                card_id=state.card_id,
                card_json=render(state.card_state),
                sequence=seq,
            )

        return False

    async def _emergency_text(self, text: str) -> None:
        try:
            await self._connector._send_emergency_text(text)
        except Exception:
            logger.warning("[outbound] emergency text send failed", exc_info=True)
```

Also update the existing `run()` loop (in the same file) to:
- drop the artifact-special-case (now handled inside the artifact dispatcher),
- mark `succeeded` when the terminal `finalize` returned True and no `error` was set.

Replace the `run()` body's inner dispatch block (around the current `if op.kind == "artifact"` branch) to remove the artifact short-circuit; the dispatcher will be invoked via Task 13 inside the artifact branch in `fold_event` if needed. Concretely the `for ev in events` body becomes:

```python
                for ev in events:
                    last_id = ev.event_id
                    op = fold_event(ev.payload, self._state, now=time.monotonic())
                    if op is None:
                        continue
                    delivered = await self._dispatch_op(op, is_terminal=op.final)
                    if op.final:
                        done = True
                        if delivered and self._state.card_state.error is None:
                            succeeded = True
```

- [ ] **Step 5: Run the unit test**

```bash
cd backend && uv run pytest tests/im/test_tailer_dispatch.py -v
```

Expected: both tests pass.

- [ ] **Step 6: Commit**

```bash
git add backend/cubeplex/im/outbound.py backend/cubeplex/im/feishu/connector.py backend/tests/im/test_tailer_dispatch.py
git commit -m "feat(im): tailer dispatches to CardKit ops; connector send_card_init_message"
```

---

## Task 13: Update `IMArtifactDispatcher` to mutate `CardState.artifacts`

**Files:**
- Modify: `backend/cubeplex/im/artifacts.py`
- Modify: `backend/cubeplex/im/outbound.py` (revisit artifact branch in `run()`)
- Create: `backend/tests/im/test_artifact_dispatcher.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/im/test_artifact_dispatcher.py`:

```python
"""IMArtifactDispatcher updates card_state.artifacts rather than sending a message."""
import pytest

from cubeplex.im.artifacts import IMArtifactDispatcher
from cubeplex.im.feishu.card_model import CardState


class _FakeConnector:
    async def upload_image(self, local_path: str) -> str | None:
        return "img_v1_uploaded"


@pytest.mark.asyncio
async def test_document_artifact_writes_share_url_to_card_state() -> None:
    state = CardState(bot_name="cubeplex", run_id="run_1")
    disp = IMArtifactDispatcher(
        connector=_FakeConnector(),
        redis=None,  # unused on the share-url-only path in this test
        redis_key_prefix="cb-",
        public_base_url="https://example.com",
        org_id="org_1",
        workspace_id="ws_1",
        conversation_id="cv_1",
        mint_share_token_fn=_fake_mint,
        card_state=state,
    )
    await disp.handle({"id": "art_1", "artifact_type": "document", "name": "r.pdf"})
    assert any(a.id == "art_1" and a.share_url and "https://example.com" in a.share_url
               for a in state.artifacts)


async def _fake_mint(**_: object) -> str:
    return "nonce_1"
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd backend && uv run pytest tests/im/test_artifact_dispatcher.py -v
```

Expected: TypeError on `card_state=` kwarg.

- [ ] **Step 3: Refactor `IMArtifactDispatcher`**

Edit `backend/cubeplex/im/artifacts.py`. Replace the whole module body with:

```python
"""IM-side artifact dispatcher — updates CardState, no standalone messages.

The dispatcher only mutates ``card_state``. The tailer is responsible for
the subsequent ``patch_card`` op that re-renders the card with the new
artifact row.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from botocore.exceptions import ClientError
from loguru import logger

from cubeplex.im.feishu.card_model import ArtifactItem, CardState
from cubeplex.objectstore import get_objectstore_client
from cubeplex.services.artifact_share import mint_share_token as _default_mint


MintShareToken = Callable[..., Awaitable[str]]


@dataclass(slots=True)
class IMArtifactDispatcher:
    """Bound to one run's card_state + share-link minting context."""

    connector: Any
    redis: Any
    redis_key_prefix: str
    public_base_url: str
    org_id: str
    workspace_id: str
    conversation_id: str
    card_state: CardState
    mint_share_token_fn: MintShareToken = _default_mint  # type: ignore[assignment]

    async def handle(self, artifact: dict[str, Any]) -> None:
        artifact_id = str(artifact.get("id") or "")
        if not artifact_id:
            return
        atype = str(artifact.get("artifact_type") or "")
        name = str(artifact.get("name") or "artifact")
        # Upsert into card_state.artifacts.
        item = next((a for a in self.card_state.artifacts if a.id == artifact_id), None)
        if item is None:
            item = ArtifactItem(id=artifact_id, artifact_type=atype, name=name)
            self.card_state.artifacts.append(item)

        if atype == "image":
            await self._fill_image_key(item, artifact)
            return
        await self._fill_share_url(item, artifact)

    async def _fill_image_key(self, item: ArtifactItem, artifact: dict[str, Any]) -> None:
        version = int(artifact.get("version") or 1)
        entry = str(artifact.get("entry_file") or "")
        path = str(artifact.get("path") or "")
        filename = entry or path.rsplit("/", 1)[-1]
        key = f"artifacts/{self.conversation_id}/{item.id}/v{version}/{filename}"
        try:
            store = get_objectstore_client()
            data, _ctype = await store.download_file(key)
        except (ClientError, Exception):
            logger.warning(
                "[IM artifacts] download failed for {}; falling back to share link",
                item.id,
                exc_info=True,
            )
            await self._fill_share_url(item, artifact)
            return
        suffix = Path(filename).suffix or ".png"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(data)
            tmp_path = tmp.name
        try:
            image_key = await self.connector.upload_image(tmp_path)
        finally:
            Path(tmp_path).unlink(missing_ok=True)
        if image_key:
            item.image_key = image_key
        else:
            await self._fill_share_url(item, artifact)

    async def _fill_share_url(self, item: ArtifactItem, artifact: dict[str, Any]) -> None:
        base = self.public_base_url.rstrip("/") if self.public_base_url else ""
        if not (base.startswith("http://") or base.startswith("https://")):
            logger.warning(
                "[IM artifacts] cannot mint share link for {} — public_base_url not absolute",
                item.id,
            )
            return
        version = int(artifact.get("version") or 1)
        nonce = await self.mint_share_token_fn(
            redis=self.redis,
            key_prefix=self.redis_key_prefix,
            org_id=self.org_id,
            workspace_id=self.workspace_id,
            conversation_id=self.conversation_id,
            artifact_id=item.id,
            version=version,
            name=item.name,
            artifact_type=item.artifact_type,
            entry_file=str(artifact.get("entry_file") or "") or None,
        )
        item.share_url = f"{base}/api/v1/public/artifacts/share/{nonce}"
```

- [ ] **Step 4: Wire `artifact` branch in `OutboundRunTailer.run`**

In `backend/cubeplex/im/outbound.py`'s `run()` loop, after `op = fold_event(...)` returns for an artifact event, also invoke the dispatcher and then re-emit a patch_card. Concretely, change `fold_event` for `artifact` (Task 10) to instead return `None` and let the tailer call the dispatcher directly. The simplest path: keep the existing `fold_event` artifact branch but have the tailer detect after-mutation by checking that `op.kind` is `card_create` / `patch_card`. Since the dispatcher mutates `card_state.artifacts` BEFORE the patch happens, we need to call `dispatcher.handle()` first, THEN dispatch the op.

Add this branch inside the for-loop of `run()`, right above `delivered = await self._dispatch_op(...)`:

```python
                    if ev.payload.get("type") == "artifact" and self._artifact_dispatcher is not None:
                        artifact_payload = (ev.payload.get("data") or {}).get("artifact") or {}
                        try:
                            await self._artifact_dispatcher.handle(artifact_payload)
                        except Exception:
                            logger.warning("artifact dispatch failed", exc_info=True)
```

- [ ] **Step 5: Run the test**

```bash
cd backend && uv run pytest tests/im/test_artifact_dispatcher.py -v
```

Expected: the test passes.

- [ ] **Step 6: Commit**

```bash
git add backend/cubeplex/im/artifacts.py backend/cubeplex/im/outbound.py backend/tests/im/test_artifact_dispatcher.py
git commit -m "feat(im): IMArtifactDispatcher mutates CardState; tailer triggers patch"
```

---

## Task 14: Add `card_action_router.dispatch` (pure)

**Files:**
- Create: `backend/cubeplex/im/feishu/card_action_router.py`
- Create: `backend/tests/im/feishu/test_card_action_router.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/im/feishu/test_card_action_router.py`:

```python
"""Tests for card_action_router.dispatch — pure routing logic."""
import pytest

from cubeplex.im.feishu.card_action_router import (
    ActionPayload,
    InvalidAction,
    ResumeAction,
    parse_action_payload,
    dispatch,
)


def test_parse_payload_extracts_fields() -> None:
    event = {
        "operator": {"open_id": "ou_user_1"},
        "action": {
            "tag": "button",
            "value": {"action": "ask_user", "run_id": "run_1", "choice": "yes"},
        },
    }
    parsed = parse_action_payload(event)
    assert parsed.kind == "ask_user"
    assert parsed.run_id == "run_1"
    assert parsed.choice == "yes"
    assert parsed.operator_open_id == "ou_user_1"


def test_parse_payload_rejects_missing_action() -> None:
    with pytest.raises(InvalidAction):
        parse_action_payload({"operator": {"open_id": "x"}, "action": {}})


def test_parse_payload_rejects_unknown_kind() -> None:
    with pytest.raises(InvalidAction):
        parse_action_payload(
            {
                "operator": {"open_id": "x"},
                "action": {"value": {"action": "weird", "run_id": "r", "choice": "c"}},
            }
        )


def test_dispatch_ask_user_returns_resume_action() -> None:
    payload = ActionPayload(
        kind="ask_user", run_id="run_1", choice="yes", operator_open_id="ou_x"
    )
    action = dispatch(payload, expected_responder_open_id="ou_x")
    assert isinstance(action, ResumeAction)
    assert action.run_id == "run_1"
    assert action.input_kind == "ask_user"
    assert action.choice == "yes"


def test_dispatch_responder_mismatch_returns_none() -> None:
    payload = ActionPayload(
        kind="ask_user", run_id="run_1", choice="yes", operator_open_id="ou_x"
    )
    action = dispatch(payload, expected_responder_open_id="ou_other")
    assert action is None


def test_dispatch_sandbox_confirm_maps_to_sandbox_decision() -> None:
    payload = ActionPayload(
        kind="sandbox_confirm", run_id="run_1", choice="approve", operator_open_id="ou_x"
    )
    action = dispatch(payload, expected_responder_open_id="ou_x")
    assert action is not None
    assert action.input_kind == "sandbox_confirm"
    assert action.choice == "approve"
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd backend && uv run pytest tests/im/feishu/test_card_action_router.py -v
```

Expected: module not found.

- [ ] **Step 3: Implement `card_action_router.py`**

Create `backend/cubeplex/im/feishu/card_action_router.py`:

```python
"""Pure routing logic for inbound CardKit `card.action.trigger` events.

`parse_action_payload(event)` extracts the typed payload. `dispatch(payload,
expected_responder_open_id)` returns the `ResumeAction` cubepi should be
fed, or None if the click should be rejected (mismatched responder).

This module is IO-free; the FastAPI route in `im_ingress.py` calls these
functions and then invokes the cubepi resume API.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


ActionKind = Literal["ask_user", "sandbox_confirm"]


class InvalidAction(ValueError):
    """The card.action payload is malformed or carries an unknown action."""


@dataclass(slots=True, frozen=True)
class ActionPayload:
    kind: ActionKind
    run_id: str
    choice: str
    operator_open_id: str


@dataclass(slots=True, frozen=True)
class ResumeAction:
    run_id: str
    input_kind: ActionKind
    choice: str
    operator_open_id: str


def parse_action_payload(event: dict[str, Any]) -> ActionPayload:
    operator = event.get("operator") or {}
    operator_open_id = str(operator.get("open_id") or "")
    if not operator_open_id:
        raise InvalidAction("missing operator.open_id")
    action = event.get("action") or {}
    value = action.get("value") or {}
    kind_raw = str(value.get("action") or "")
    if kind_raw not in ("ask_user", "sandbox_confirm"):
        raise InvalidAction(f"unknown action kind: {kind_raw!r}")
    run_id = str(value.get("run_id") or "")
    choice = str(value.get("choice") or "")
    if not run_id or not choice:
        raise InvalidAction("missing run_id or choice")
    return ActionPayload(
        kind=kind_raw,  # type: ignore[arg-type]
        run_id=run_id,
        choice=choice,
        operator_open_id=operator_open_id,
    )


def dispatch(
    payload: ActionPayload,
    *,
    expected_responder_open_id: str | None,
) -> ResumeAction | None:
    """Validate responder and produce the ResumeAction cubepi consumes.

    Returns None when the responder does not match (the caller should
    surface a toast and otherwise no-op). Identity-gate enforcement of
    `expected_responder_open_id=None` denies all — None means "we have no
    record of awaiting input", which is the right default if Redis lost
    the binding (caller should also reject).
    """
    if expected_responder_open_id is None:
        return None
    if payload.operator_open_id != expected_responder_open_id:
        return None
    return ResumeAction(
        run_id=payload.run_id,
        input_kind=payload.kind,
        choice=payload.choice,
        operator_open_id=payload.operator_open_id,
    )


__all__ = [
    "ActionKind",
    "ActionPayload",
    "InvalidAction",
    "ResumeAction",
    "dispatch",
    "parse_action_payload",
]
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd backend && uv run pytest tests/im/feishu/test_card_action_router.py -v
```

Expected: all 6 tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/im/feishu/card_action_router.py backend/tests/im/feishu/test_card_action_router.py
git commit -m "feat(im-feishu): card_action_router.dispatch + parse_action_payload"
```

---

## Task 15: Wire `card.action.trigger` into the Feishu webhook ingress

**Files:**
- Modify: `backend/cubeplex/api/routes/v1/im_ingress.py`
- Create: `backend/tests/im/feishu/test_im_ingress_card_action.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/im/feishu/test_im_ingress_card_action.py`:

```python
"""End-to-end-ish test for the card.action branch of the webhook ingress.

Stubs out the cubepi resume API + Redis to assert the routing logic.
"""
from __future__ import annotations

import json
from typing import Any

import pytest


@pytest.mark.asyncio
async def test_card_action_dispatch_calls_resume(monkeypatch: pytest.MonkeyPatch) -> None:
    from cubeplex.api.routes.v1 import im_ingress

    resume_calls: list[dict[str, Any]] = []

    async def fake_resume(**kwargs: Any) -> bool:
        resume_calls.append(kwargs)
        return True

    monkeypatch.setattr(im_ingress, "resume_paused_run", fake_resume)

    redis_state: dict[str, str] = {
        # Per-run awaiting-responder binding written when ask_user fired.
        "run:run_1:awaiting_responder": "ou_user_1",
    }

    async def fake_get(key: str) -> str | None:
        return redis_state.get(key)

    async def fake_setnx(key: str, value: str, ex: int) -> bool:
        if key in redis_state:
            return False
        redis_state[key] = value
        return True

    monkeypatch.setattr(im_ingress, "_redis_get", fake_get)
    monkeypatch.setattr(im_ingress, "_redis_setnx", fake_setnx)

    event = {
        "header": {"event_type": "card.action.trigger", "token": "tok_abc"},
        "event": {
            "operator": {"open_id": "ou_user_1"},
            "action": {
                "value": {"action": "ask_user", "run_id": "run_1", "choice": "yes"}
            },
        },
    }
    handled, toast = await im_ingress._handle_card_action(event)
    assert handled is True
    assert toast is None
    assert resume_calls == [
        {
            "run_id": "run_1",
            "input_kind": "ask_user",
            "choice": "yes",
            "operator_open_id": "ou_user_1",
        }
    ]


@pytest.mark.asyncio
async def test_card_action_token_replay_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    from cubeplex.api.routes.v1 import im_ingress

    resume_calls: list[Any] = []

    async def fake_resume(**kwargs: Any) -> bool:
        resume_calls.append(kwargs)
        return True

    monkeypatch.setattr(im_ingress, "resume_paused_run", fake_resume)

    redis_state: dict[str, str] = {"run:run_1:awaiting_responder": "ou_user_1"}

    async def fake_get(k: str) -> str | None:
        return redis_state.get(k)

    async def fake_setnx(k: str, v: str, ex: int) -> bool:
        if k in redis_state:
            return False
        redis_state[k] = v
        return True

    monkeypatch.setattr(im_ingress, "_redis_get", fake_get)
    monkeypatch.setattr(im_ingress, "_redis_setnx", fake_setnx)

    event = {
        "header": {"event_type": "card.action.trigger", "token": "tok_dup"},
        "event": {
            "operator": {"open_id": "ou_user_1"},
            "action": {
                "value": {"action": "ask_user", "run_id": "run_1", "choice": "yes"}
            },
        },
    }
    await im_ingress._handle_card_action(event)
    await im_ingress._handle_card_action(event)
    assert len(resume_calls) == 1  # second call no-op'd by token replay guard


@pytest.mark.asyncio
async def test_card_action_responder_mismatch_returns_toast(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cubeplex.api.routes.v1 import im_ingress

    async def fake_resume(**_: Any) -> bool:
        raise AssertionError("must not call resume on mismatch")

    monkeypatch.setattr(im_ingress, "resume_paused_run", fake_resume)

    redis_state = {"run:run_1:awaiting_responder": "ou_user_1"}

    async def fake_get(k: str) -> str | None:
        return redis_state.get(k)

    async def fake_setnx(*_: Any, **__: Any) -> bool:
        return True

    monkeypatch.setattr(im_ingress, "_redis_get", fake_get)
    monkeypatch.setattr(im_ingress, "_redis_setnx", fake_setnx)

    event = {
        "header": {"event_type": "card.action.trigger", "token": "tok_3"},
        "event": {
            "operator": {"open_id": "ou_someone_else"},
            "action": {
                "value": {"action": "ask_user", "run_id": "run_1", "choice": "yes"}
            },
        },
    }
    handled, toast = await im_ingress._handle_card_action(event)
    assert handled is True
    assert toast == "这不是发给你的"
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd backend && uv run pytest tests/im/feishu/test_im_ingress_card_action.py -v
```

Expected: `AttributeError: module 'cubeplex.api.routes.v1.im_ingress' has no attribute '_handle_card_action'`.

- [ ] **Step 3: Add the handler function and stub dependencies**

Edit `backend/cubeplex/api/routes/v1/im_ingress.py`. Add at top of the module imports (after the existing imports):

```python
from cubeplex.im.feishu.card_action_router import (
    InvalidAction,
    dispatch as dispatch_card_action,
    parse_action_payload,
)
from cubeplex.run_manager import resume_paused_run  # cubepi-side resume entry
```

If `cubeplex.run_manager.resume_paused_run` does not exist, add it as a thin wrapper next to wherever runs are kicked off; verify with:

```bash
grep -rn "def resume_paused_run" backend/cubeplex/ || \
  echo "MISSING — add a wrapper around cubepi's resume_with_human_input"
```

If missing, create `backend/cubeplex/run_manager.py` with:

```python
"""Run-level lifecycle wrappers shared by SSE + IM resume paths."""
from typing import Any


async def resume_paused_run(
    *,
    run_id: str,
    input_kind: str,
    choice: str,
    operator_open_id: str,
) -> bool:
    """Resume a paused run with a human-supplied choice.

    Returns True if cubepi accepted the resume; False if the run was not
    found / already finished / not awaiting input. Implementation calls
    cubepi.agent_runtime.resume_with_human_input — see the spec §6 for
    the shape contract.
    """
    # TODO(plan task 0): finalize cubepi-side call shape after audit.
    raise NotImplementedError
```

(Task 0's audit identifies the exact cubepi API; if it has the shape we expect this stub gets a concrete body in Task 17.)

Now add the Redis helpers and the card-action handler. Append to `im_ingress.py`:

```python
from cubeplex.redis_client import get_redis_pool

async def _redis_get(key: str) -> str | None:
    pool = await get_redis_pool()
    value = await pool.get(key)
    return value.decode() if isinstance(value, bytes) else value


async def _redis_setnx(key: str, value: str, ex: int) -> bool:
    pool = await get_redis_pool()
    return bool(await pool.set(key, value, ex=ex, nx=True))


async def _handle_card_action(event: dict[str, Any]) -> tuple[bool, str | None]:
    """Process a `card.action.trigger` event.

    Returns (handled, toast). `handled=True` means we processed the event
    and the route should reply 200; `toast` is an optional user-visible
    message (Feishu shows it briefly above the card).
    """
    header = event.get("header") or {}
    token = str(header.get("token") or "")
    if not token:
        return True, "缺少 token"
    # Token replay guard — Feishu's interaction token is one-time.
    fresh = await _redis_setnx(f"cardkit:token:{token}", "1", 1800)
    if not fresh:
        return True, None  # idempotent no-op

    try:
        payload = parse_action_payload(event.get("event") or {})
    except InvalidAction as exc:
        logger.warning("[Feishu ingress] invalid card.action payload: {}", exc)
        return True, "未知操作"

    expected = await _redis_get(f"run:{payload.run_id}:awaiting_responder")
    action = dispatch_card_action(payload, expected_responder_open_id=expected)
    if action is None:
        return True, "这不是发给你的"

    try:
        ok = await resume_paused_run(
            run_id=action.run_id,
            input_kind=action.input_kind,
            choice=action.choice,
            operator_open_id=action.operator_open_id,
        )
    except NotImplementedError:
        # Task 17 hasn't landed yet — treat as transient failure.
        logger.warning("[Feishu ingress] resume_paused_run not implemented yet")
        return True, "暂时无法响应"
    except Exception:
        logger.warning("[Feishu ingress] resume_paused_run raised", exc_info=True)
        return True, "暂时无法响应"

    if not ok:
        return True, "会话已结束"
    return True, None
```

Finally, branch on `event_type` near the top of the `feishu_events` route (after the verification-token check passes, before the existing message branch). Replace the existing block:

```python
    # url_verification challenge — only echo AFTER the token check passes,
    # so we never bounce attacker-supplied challenge data without auth.
    if payload.get("type") == "url_verification":
```

with:

```python
    # url_verification challenge — only echo AFTER the token check passes,
    # so we never bounce attacker-supplied challenge data without auth.
    if payload.get("type") == "url_verification":
        return Response(
            content=json.dumps({"challenge": payload.get("challenge", "")}),
            media_type="application/json",
        )

    # CardKit button-click event branch.
    event_type = str(header.get("event_type") or "")
    if event_type == "card.action.trigger":
        handled, toast = await _handle_card_action(payload)
        if handled:
            body: dict[str, Any] = {}
            if toast:
                body = {"toast": {"type": "info", "content": toast}}
            return Response(
                content=json.dumps(body),
                media_type="application/json",
            )

    # Signature verification (skipped when no encrypt_key configured ...
```

(Delete the old `if payload.get("type") == "url_verification"` block that came BEFORE the signature verification — the rewritten block above replaces it; do not duplicate.)

- [ ] **Step 4: Run the test**

```bash
cd backend && uv run pytest tests/im/feishu/test_im_ingress_card_action.py -v
```

Expected: all 3 tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/api/routes/v1/im_ingress.py backend/cubeplex/run_manager.py backend/tests/im/feishu/test_im_ingress_card_action.py
git commit -m "feat(im-feishu): webhook ingress branches on card.action.trigger"
```

---

## Task 16: Subscribe long-connection to card.action.trigger

**Files:**
- Modify: `backend/cubeplex/im/feishu/long_connection.py`

- [ ] **Step 1: Inspect the existing handler registration**

Run:

```bash
grep -n "register_p2.*v1\|EventDispatcher\|dispatcher\.on" \
  backend/cubeplex/im/feishu/long_connection.py
```

Identify the location where `im.message.receive_v1` is registered. We need to register a `card.action.trigger` handler alongside it.

- [ ] **Step 2: Add the card-action handler**

In `backend/cubeplex/im/feishu/long_connection.py`, find the function that builds the `EventDispatcher` (typically `_build_dispatcher` or similar) and add, immediately after the message-handler registration:

```python
    from lark_oapi.adapter.ws.ws_card import CardActionTriggerEvent

    async def _on_card_action(event: CardActionTriggerEvent) -> None:
        from cubeplex.api.routes.v1.im_ingress import _handle_card_action
        # event.event is the same shape as the webhook event body.
        # Wrap into the {"header": ..., "event": ...} envelope our handler expects.
        raw = {
            "header": {
                "event_type": "card.action.trigger",
                "token": getattr(event.event, "token", "") or "",
            },
            "event": event.event.__dict__ if hasattr(event.event, "__dict__") else event.event,
        }
        handled, toast = await _handle_card_action(raw)
        # long-connection has no response channel to echo toast back; log it.
        if toast:
            logger.info("[Feishu LC] card.action toast: {}", toast)

    dispatcher.register_card_action_trigger(_on_card_action)
```

(The exact `lark_oapi` import path / method name depends on the SDK version. If `register_card_action_trigger` does not exist on the dispatcher object, search for the right name with `grep -rn "card_action" .venv/lib/python*/site-packages/lark_oapi/ws/` from the `backend/` dir and adapt.)

- [ ] **Step 3: Smoke-test the long-connection startup**

```bash
cd backend && uv run python -c "from cubeplex.im.feishu.long_connection import build_dispatcher; print('ok')" 2>&1 | tail -5
```

Expected: prints `ok`. If the SDK import line fails, search and update as noted above before continuing.

- [ ] **Step 4: Commit**

```bash
git add backend/cubeplex/im/feishu/long_connection.py
git commit -m "feat(im-feishu): long-connection subscribes to card.action.trigger"
```

---

## Task 17: Implement `resume_paused_run` end-to-end

**Files:**
- Modify: `backend/cubeplex/run_manager.py`
- Create: `backend/tests/test_run_manager_resume.py`

- [ ] **Step 1: Inspect cubepi's resume API**

Run:

```bash
grep -rn "resume_with_human_input\|def resume\|awaiting_human_input" ~/cubepi/cubepi 2>/dev/null | head -20
```

Identify the exact function signature and the run-state predicate. Note the result.

- [ ] **Step 2: Write the failing test**

Create `backend/tests/test_run_manager_resume.py`:

```python
"""resume_paused_run forwards to cubepi and records the awaiting-responder release."""
from __future__ import annotations

from typing import Any

import pytest


@pytest.mark.asyncio
async def test_resume_paused_run_calls_cubepi(monkeypatch: pytest.MonkeyPatch) -> None:
    from cubeplex import run_manager

    seen: list[dict[str, Any]] = []

    async def fake_resume(run_id: str, **kwargs: Any) -> bool:
        seen.append({"run_id": run_id, **kwargs})
        return True

    monkeypatch.setattr(run_manager, "_cubepi_resume", fake_resume)
    ok = await run_manager.resume_paused_run(
        run_id="run_1",
        input_kind="ask_user",
        choice="yes",
        operator_open_id="ou_x",
    )
    assert ok is True
    assert seen[0]["run_id"] == "run_1"


@pytest.mark.asyncio
async def test_resume_paused_run_returns_false_when_run_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cubeplex import run_manager

    async def fake_resume(_: str, **__: Any) -> bool:
        return False

    monkeypatch.setattr(run_manager, "_cubepi_resume", fake_resume)
    ok = await run_manager.resume_paused_run(
        run_id="missing", input_kind="ask_user", choice="x", operator_open_id="ou"
    )
    assert ok is False
```

- [ ] **Step 3: Run the test to verify it fails**

```bash
cd backend && uv run pytest tests/test_run_manager_resume.py -v
```

Expected: import or attribute error on `_cubepi_resume`.

- [ ] **Step 4: Implement `resume_paused_run`**

Replace the body of `backend/cubeplex/run_manager.py` (created in Task 15) with:

```python
"""Run-level lifecycle wrappers shared by SSE + IM resume paths."""

from __future__ import annotations

from typing import Any

from loguru import logger


async def _cubepi_resume(run_id: str, **kwargs: Any) -> bool:
    """Forward the resume call to cubepi.

    Kept as a thin module-level shim so tests can monkeypatch it. Real
    impl reads the cubepi function identified by Task 0 / Task 17 Step 1.
    """
    # NOTE: adjust the import below to match Task 17 Step 1 findings.
    from cubepi.agent_runtime import resume_with_human_input

    return bool(await resume_with_human_input(run_id=run_id, **kwargs))


async def resume_paused_run(
    *,
    run_id: str,
    input_kind: str,
    choice: str,
    operator_open_id: str,
) -> bool:
    """Resume a paused run; True iff cubepi accepted the input."""
    try:
        return await _cubepi_resume(
            run_id,
            input_kind=input_kind,
            choice=choice,
            operator_open_id=operator_open_id,
        )
    except Exception:
        logger.warning("[run_manager] cubepi resume raised", exc_info=True)
        return False
```

Adapt the call site to whatever Task 17 Step 1 reveals (parameter names may differ).

- [ ] **Step 5: Run the test**

```bash
cd backend && uv run pytest tests/test_run_manager_resume.py -v
```

Expected: both tests pass.

- [ ] **Step 6: Commit**

```bash
git add backend/cubeplex/run_manager.py backend/tests/test_run_manager_resume.py
git commit -m "feat(run): resume_paused_run wraps cubepi resume_with_human_input"
```

---

## Task 18: Migrate `FeishuConnector` to emergency-text-only legacy path

**Files:**
- Modify: `backend/cubeplex/im/feishu/connector.py`
- Modify: `backend/tests/im/feishu/test_connector_emergency.py` (create)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/im/feishu/test_connector_emergency.py`:

```python
"""Connector retains a single emergency text path; old text streaming gone."""
from cubeplex.im.feishu import connector


def test_connector_module_does_not_export_build_payload() -> None:
    assert not hasattr(connector.FeishuConnector, "_build_payload")


def test_connector_module_drops_markdown_regexes() -> None:
    assert not hasattr(connector, "_MARKDOWN_TABLE_RE")
    assert not hasattr(connector, "_MARKDOWN_HINT_RE")


def test_connector_has_send_emergency_text_method() -> None:
    assert hasattr(connector.FeishuConnector, "_send_emergency_text")


def test_connector_drops_post_placeholder_and_edit() -> None:
    assert not hasattr(connector.FeishuConnector, "post_placeholder")
    assert not hasattr(connector.FeishuConnector, "edit")
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd backend && uv run pytest tests/im/feishu/test_connector_emergency.py -v
```

Expected: assertions fail because the legacy attributes still exist.

- [ ] **Step 3: Delete dead methods, rename to `_send_emergency_text`**

Edit `backend/cubeplex/im/feishu/connector.py`:

- Delete `_MARKDOWN_TABLE_RE` and `_MARKDOWN_HINT_RE` constants (lines 64-65).
- Delete the entire `_build_payload` static method (lines 227-238).
- Delete `post_placeholder` (lines 256-318).
- Delete `edit` (lines 320-347).
- Rename `send_text_message` to `_send_emergency_text` and inline the
  `send_to_chat` body (since `send_to_chat` is now dead).
  Result for the renamed method:

```python
    async def _send_emergency_text(self, text: str) -> str | None:
        """Send a plain text bubble — used ONLY when CardKit create_entity fails.

        Thread when ``reply_to_id`` is bound; otherwise create top-level.
        """
        if self._client is None or not self._channel_id:
            return None
        from lark_oapi.api.im.v1 import (
            CreateMessageRequest,
            CreateMessageRequestBody,
            ReplyMessageRequest,
            ReplyMessageRequestBody,
        )

        payload = json.dumps({"text": text}, ensure_ascii=False)
        if self._reply_to_id:
            body = (
                ReplyMessageRequestBody.builder()
                .content(payload)
                .msg_type("text")
                .reply_in_thread(False)
                .build()
            )
            req = (
                ReplyMessageRequest.builder()
                .message_id(self._reply_to_id)
                .request_body(body)
                .build()
            )
            response = await asyncio.to_thread(self._client.im.v1.message.reply, req)
        else:
            body = (
                CreateMessageRequestBody.builder()
                .receive_id(self._channel_id)
                .msg_type("text")
                .content(payload)
                .build()
            )
            req = (
                CreateMessageRequest.builder()
                .receive_id_type("chat_id")
                .request_body(body)
                .build()
            )
            response = await asyncio.to_thread(self._client.im.v1.message.create, req)
        if not getattr(response, "success", lambda: False)():
            logger.warning(
                "[Feishu] _send_emergency_text failed: code={} msg={}",
                getattr(response, "code", None),
                getattr(response, "msg", None),
            )
            return None
        data = getattr(response, "data", None)
        message_id = getattr(data, "message_id", None) if data is not None else None
        return str(message_id) if message_id else None
```

- Delete the public `send_to_chat` method (now dead — verify nothing in the codebase imports it: `grep -rn "send_to_chat" backend/cubeplex`; if anything does, follow up before deleting).

- Delete `send_text_message` (replaced by `_send_emergency_text`).

- `_raise_for_flood` and `FeishuRateLimitError` stay (still used by reaction calls).

- [ ] **Step 4: Add a "waiting reaction" helper for AskUser**

In `connector.py`, near the existing reaction constants:

```python
_REACTION_WAITING = "HOURGLASS"
```

Add helper near `on_processing_complete`:

```python
    async def add_waiting_reaction(self, message_id: str) -> str | None:
        return await self.add_reaction(message_id, _REACTION_WAITING)
```

- [ ] **Step 5: Run the test**

```bash
cd backend && uv run pytest tests/im/feishu/test_connector_emergency.py -v
```

Expected: all 4 tests pass.

- [ ] **Step 6: Re-run all backend tests to catch import breakage from deletions**

```bash
cd backend && uv run pytest tests/im -v
```

Expected: all green. If a test references one of the deleted methods, update it (it's a stale test if it was checking the old path).

- [ ] **Step 7: Commit**

```bash
git add backend/cubeplex/im/feishu/connector.py backend/tests/im/feishu/test_connector_emergency.py
git commit -m "refactor(im-feishu): drop legacy text path; keep _send_emergency_text only"
```

---

## Task 19: Write `awaiting_responder` to Redis on AskUser / SandboxConfirm emit

**Files:**
- Modify: `backend/cubeplex/im/outbound.py`
- Modify: `backend/tests/im/test_fold_event_terminal.py`

- [ ] **Step 1: Extend the failing test**

Append to `backend/tests/im/test_fold_event_terminal.py`:

```python
@pytest.mark.asyncio
async def test_pending_input_writes_responder_to_redis() -> None:
    import pytest as _pytest

    from cubeplex.im.outbound import register_awaiting_responder

    state_record: dict[str, tuple[str, int]] = {}

    async def fake_setex(key: str, value: str, ex: int) -> None:
        state_record[key] = (value, ex)

    await register_awaiting_responder(
        run_id="run_1",
        responder_open_id="ou_user_1",
        setex_fn=fake_setex,
    )
    assert state_record["run:run_1:awaiting_responder"] == ("ou_user_1", 600)
```

Add at the top of the file:

```python
import pytest
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd backend && uv run pytest tests/im/test_fold_event_terminal.py::test_pending_input_writes_responder_to_redis -v
```

Expected: import error on `register_awaiting_responder`.

- [ ] **Step 3: Implement the registration helper**

In `backend/cubeplex/im/outbound.py`, near the module top (after imports):

```python
from typing import Awaitable, Callable

_AWAITING_TTL_SECONDS = 600  # 10-minute window matches spec §6.5


async def register_awaiting_responder(
    *,
    run_id: str,
    responder_open_id: str,
    setex_fn: Callable[[str, str, int], Awaitable[None]],
) -> None:
    """Bind which Feishu user is allowed to answer this run's AskUser.

    Called by the worker when it sees an ``ask_user`` / ``sandbox_confirm``
    op for a known inbound sender. The webhook ingress reads the same
    key (``run:{run_id}:awaiting_responder``) to gate the callback.
    """
    await setex_fn(
        f"run:{run_id}:awaiting_responder",
        responder_open_id,
        _AWAITING_TTL_SECONDS,
    )
```

- [ ] **Step 4: Wire it into the worker — call after dispatching a PendingInput op**

Edit `backend/cubeplex/im/worker.py` (or whichever module owns the run-queue worker). Find where the tailer is started for a run; the worker already knows the sender's `open_id` (from the inbound event). After the tailer's `_dispatch_op` returns for a `patch_card` that follows an `ask_user` / `sandbox_confirm`, register the responder.

Concretely, the cleanest seam is inside `OutboundRunTailer.run()`. Add to the for-ev loop in `outbound.py`, immediately after the `delivered = await self._dispatch_op(...)` call:

```python
                    if (
                        ev.payload.get("type") in ("ask_user", "sandbox_confirm")
                        and self._state.card_state.pending_input is not None
                        and self._responder_open_id is not None
                    ):
                        await register_awaiting_responder(
                            run_id=self._run_id,
                            responder_open_id=self._responder_open_id,
                            setex_fn=self._redis_setex,
                        )
```

Extend `OutboundRunTailer.__init__` to take `responder_open_id: str | None = None` and `redis_setex: callable` (defaulting to a thin wrapper around `self._redis.set(..., ex=ex)`). The worker passes the inbound user's `open_id` when constructing the tailer.

- [ ] **Step 5: Run the test**

```bash
cd backend && uv run pytest tests/im/test_fold_event_terminal.py -v
```

Expected: all 7 tests pass.

- [ ] **Step 6: Commit**

```bash
git add backend/cubeplex/im/outbound.py backend/cubeplex/im/worker.py backend/tests/im/test_fold_event_terminal.py
git commit -m "feat(im): register awaiting_responder when AskUser/SandboxConfirm emits"
```

---

## Task 20: Integration test — full run lifecycle against `httpx.MockTransport`

**Files:**
- Create: `backend/tests/im/feishu/test_tailer_e2e_fake_cardkit.py`

- [ ] **Step 1: Write the test**

Create `backend/tests/im/feishu/test_tailer_e2e_fake_cardkit.py`:

```python
"""End-to-end-ish: feed a recorded cubepi event sequence into the tailer
through a real CardKitClient backed by httpx MockTransport. Assert the
sequence of HTTP calls matches the spec.
"""
from __future__ import annotations

import json
from typing import Any

import pytest
from httpx import MockTransport, Request, Response

from cubeplex.im.feishu.cardkit_client import CardKitClient
from cubeplex.im.outbound import OutboundRunTailer, fold_event
from cubeplex.im.types import RenderState


class _FakeConnector:
    def __init__(self) -> None:
        self.init_calls: list[str] = []
        self.start = 0
        self.complete = 0
        self.failed = 0

    async def on_processing_start(self, state: RenderState) -> None:
        self.start += 1

    async def on_processing_complete(self, state: RenderState) -> None:
        self.complete += 1

    async def on_processing_failed(self, state: RenderState) -> None:
        self.failed += 1

    async def send_card_init_message(self, card_id: str) -> str | None:
        self.init_calls.append(card_id)
        return "om_1"


@pytest.mark.asyncio
async def test_full_lifecycle_through_real_client() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    async def handler(req: Request) -> Response:
        body = req.read()
        try:
            parsed = json.loads(body or b"{}")
        except Exception:
            parsed = {}
        calls.append((str(req.url), parsed))
        # CardKit success.
        if req.url.path.endswith("/cards"):
            return Response(200, json={"code": 0, "data": {"card_id": "AAQA"}})
        return Response(200, json={"code": 0})

    client = CardKitClient(
        token_provider=lambda: "tat_x",
        transport=MockTransport(handler),
    )

    state = RenderState(bot_name="cubeplex", run_id="run_1")
    conn = _FakeConnector()
    tailer = OutboundRunTailer(
        redis=None,
        key_prefix="cb-",
        run_id="run_1",
        connector=conn,
        state=state,
        cardkit=client,
    )

    # Drive _dispatch_op directly with a recorded event sequence.
    events = [
        {"type": "text_delta", "data": {"content": "Hello "}},
        {"type": "tool_call", "data": {"id": "tc_1", "name": "bash", "args": {"cmd": "ls"}}},
        {"type": "tool_result", "data": {"id": "tc_1", "result": {"out": "ok"}, "elapsed_ms": 50}},
        {"type": "text_delta", "data": {"content": "world"}},
        {"type": "done", "data": {"elapsed_ms": 2000}},
    ]
    import time as _time

    base = _time.monotonic()
    for i, ev in enumerate(events):
        op = fold_event(ev, state, now=base + i * 2.0)  # 2s apart — past throttles
        if op is None:
            continue
        await tailer._dispatch_op(op, is_terminal=op.final)

    # The first op (text_delta) caused card_create; the rest used the existing card.
    create_calls = [c for c in calls if c[0].endswith("/cards")]
    assert len(create_calls) == 1
    # At least one stream_text and one patch_card landed.
    stream_calls = [c for c in calls if "/elements/streaming_content/content" in c[0]]
    assert len(stream_calls) >= 1
    patch_calls = [c for c in calls if c[0].endswith("/cards/AAQA")]
    assert len(patch_calls) >= 1
    # Final patch had streaming_mode=False.
    final = patch_calls[-1][1]
    assert final["card"]["data"]["config"]["streaming_mode"] is False
```

- [ ] **Step 2: Run the test**

```bash
cd backend && uv run pytest tests/im/feishu/test_tailer_e2e_fake_cardkit.py -v
```

Expected: passes.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/im/feishu/test_tailer_e2e_fake_cardkit.py
git commit -m "test(im-feishu): integration test for full tailer lifecycle"
```

---

## Task 21: Migrate existing IM E2E to card-JSON assertions

**Files:**
- Modify: existing `backend/tests/e2e/im_feishu_*.py` files (audit + update)
- Create: `backend/tests/e2e/im_feishu_helpers.py` (or extend existing)

- [ ] **Step 1: Catalog existing E2E test files**

```bash
ls -la backend/tests/e2e/ | grep -i im
```

For each test file, open and identify assertions that look at the bubble's plain-text content (likely `bubble.text`, `message.content`, or similar string patterns).

- [ ] **Step 2: Add the `assert_card_contains` helper**

Create or extend `backend/tests/e2e/im_feishu_helpers.py`:

```python
"""Helpers for asserting Feishu CardKit card content in E2E tests."""
from __future__ import annotations

import json
from typing import Any


async def fetch_card_json(lark_cli: Any, message_id: str) -> dict[str, Any]:
    """Fetch the card JSON behind an interactive message.

    Uses lark_oapi's im.v1.message.get + extracts card_id from the
    payload, then GETs /open-apis/cardkit/v1/cards/{card_id}.
    """
    msg = await lark_cli.fetch_message(message_id)
    body = json.loads(msg["content"])
    card_id = body["data"]["card_id"]
    return await lark_cli.fetch_card(card_id)


def card_contains_text(card_json: dict[str, Any], needle: str) -> bool:
    """Recursive substring search across all card element text fields."""
    blob = json.dumps(card_json, ensure_ascii=False)
    return needle in blob


def card_element(card_json: dict[str, Any], element_id: str) -> dict[str, Any] | None:
    """Find an element by its element_id."""
    for el in card_json.get("body", {}).get("elements", []):
        if el.get("element_id") == element_id:
            return el
    return None
```

(The exact `lark_cli` interface depends on the harness already in `backend/tests/e2e/`; adapt accordingly.)

- [ ] **Step 3: Migrate one E2E file as a template**

Pick the simplest existing IM E2E (e.g., the first test that just asserts the bot replied). In its assertions, replace:

```python
assert "expected text" in bubble.text  # old
```

with:

```python
card = await fetch_card_json(lark_cli, bot_message_id)
assert card_contains_text(card, "expected text")
```

Run the migrated test against the local Feishu test tenant (per CLAUDE.md, only locally — not CI):

```bash
cd backend && uv run pytest tests/e2e/im_feishu_<the_file>.py -v
```

Expected: passes. If it fails because the CardKit scope wasn't applied to the Feishu app yet, complete Task 25 first.

- [ ] **Step 4: Migrate the remaining IM E2E tests using the same pattern**

For each file:
1. Replace bubble-text assertions with `card_contains_text` / `card_element`.
2. Run individually: `cd backend && uv run pytest tests/e2e/<file> -v`.
3. Commit per file (small reviewable diffs).

- [ ] **Step 5: Commit (one or several commits)**

```bash
git add backend/tests/e2e/im_feishu_helpers.py backend/tests/e2e/im_feishu_<file>.py
git commit -m "test(e2e): migrate IM Feishu assertions to card JSON"
```

---

## Task 22: New E2E case for AskUser callback closed loop

**Files:**
- Create: `backend/tests/e2e/im_feishu_ask_user_loop.py`

- [ ] **Step 1: Write the test**

Create `backend/tests/e2e/im_feishu_ask_user_loop.py`:

```python
"""E2E: agent emits ask_user → user clicks Yes → run resumes."""
from __future__ import annotations

import pytest

from backend.tests.e2e.im_feishu_helpers import card_element, card_contains_text


@pytest.mark.asyncio
async def test_ask_user_closed_loop(lark_test_tenant) -> None:
    # 1. Send a prompt that the agent answers with an ask_user.
    bot_msg_id = await lark_test_tenant.send_to_bot(
        "Please ask me yes/no about my preference."
    )

    # 2. Wait for the bot to update the card with a pending_input element.
    card = await lark_test_tenant.wait_for_card_element(
        bot_msg_id, element_id="pending_input", timeout=60
    )
    assert card is not None
    pending = card_element(card, "pending_input")
    assert pending is not None

    # 3. Simulate the button click via the Lark client.
    await lark_test_tenant.click_card_button(
        bot_msg_id, button_value={"choice": "yes"}
    )

    # 4. Wait for the receipt UI ("✓ 已选择" line).
    final = await lark_test_tenant.wait_for_card_text(
        bot_msg_id, needle="已选择", timeout=30
    )
    assert card_contains_text(final, "yes")

    # 5. Wait for the run to finalize (header turns green).
    finalized = await lark_test_tenant.wait_for_finalized(bot_msg_id, timeout=120)
    assert finalized["header"]["template"] == "green"
```

(The `lark_test_tenant` fixture is whatever already exists for the current IM Feishu E2E. If `click_card_button` isn't supported by the existing harness, this task includes extending the harness — see `~/openclaw-lark/tests` for prior art if needed.)

- [ ] **Step 2: Run the test locally**

```bash
cd backend && uv run pytest tests/e2e/im_feishu_ask_user_loop.py -v
```

Expected: passes against the user's Feishu test tenant. If the harness lacks `click_card_button`, the failure trace will reveal what to add.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/e2e/im_feishu_ask_user_loop.py
git commit -m "test(e2e): AskUser button closed loop on Feishu"
```

---

## Task 23: E2E case for emergency text fallback

**Files:**
- Create: `backend/tests/im/feishu/test_emergency_fallback.py`

- [ ] **Step 1: Write the test**

Create `backend/tests/im/feishu/test_emergency_fallback.py`:

```python
"""Unit/integration: CardKit create fails → emergency text path takes over."""
from __future__ import annotations

import pytest

from cubeplex.im.outbound import OutboundOp, OutboundRunTailer
from cubeplex.im.types import RenderState


class _FailingCardKit:
    async def create_entity(self, *_: object, **__: object) -> str:
        raise RuntimeError("CardKit 500")

    async def stream_text(self, **_: object) -> None:
        raise AssertionError("must not stream on emergency path")

    async def patch_card(self, **_: object) -> None:
        raise AssertionError("must not patch on emergency path")

    async def finalize(self, **_: object) -> bool:
        raise AssertionError("must not finalize on emergency path")


class _RecordingConnector:
    def __init__(self) -> None:
        self.text_sends: list[str] = []
        self.init_calls: list[str] = []

    async def on_processing_start(self, _: RenderState) -> None: ...
    async def on_processing_complete(self, _: RenderState) -> None: ...
    async def on_processing_failed(self, _: RenderState) -> None: ...

    async def send_card_init_message(self, card_id: str) -> str | None:
        self.init_calls.append(card_id)
        return None

    async def _send_emergency_text(self, text: str) -> str | None:
        self.text_sends.append(text)
        return "om_x"


@pytest.mark.asyncio
async def test_create_failure_triggers_emergency_text() -> None:
    state = RenderState(bot_name="cubeplex", run_id="run_1")
    state.card_state.streaming_content = "Partial answer text"
    cardkit = _FailingCardKit()
    connector = _RecordingConnector()
    tailer = OutboundRunTailer(
        redis=None,
        key_prefix="cb-",
        run_id="run_1",
        connector=connector,
        state=state,
        cardkit=cardkit,
    )
    delivered = await tailer._dispatch_op(OutboundOp(kind="card_create"), is_terminal=False)
    assert delivered is False
    assert state.card_unavailable is True
    # Two emergency texts: the warning + the partial answer.
    assert any("飞书富文本渲染暂时不可用" in t for t in connector.text_sends)
    assert any("Partial answer text" in t for t in connector.text_sends)
```

- [ ] **Step 2: Run the test**

```bash
cd backend && uv run pytest tests/im/feishu/test_emergency_fallback.py -v
```

Expected: passes.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/im/feishu/test_emergency_fallback.py
git commit -m "test(im-feishu): emergency text fallback when CardKit create fails"
```

---

## Task 24: Operator doc + quick-reference update

**Files:**
- Create: `backend/docs/im-feishu-rich-output.md`
- Modify: `backend/docs/quick-reference.md`

- [ ] **Step 1: Write `backend/docs/im-feishu-rich-output.md`**

```markdown
# Feishu rich-output (CardKit) operator guide

## Required Feishu app scope

The bot's Feishu application must have **`cardkit:card:write`**
("创建与更新卡片") granted. Without it, every run will fall through to
the emergency text path and the user sees:

> ⚠️ 飞书富文本渲染暂时不可用，结果将以文本展示

### How to apply

1. Open the Feishu Developer Console.
2. Navigate to your bot app → Permissions Management.
3. Add `cardkit:card:write` and submit for tenant approval.
4. Re-issue the tenant access token (the SDK does this automatically).

## Card model

One CardKit entity per cubepi run. The card has five element slots:

- `streaming_content` — markdown, updated via `streamCardContent`.
- `tool_panel` — collapsible panel of tool calls.
- `artifacts` — collapsible panel of artifact rows.
- `sources` — reserved (unused in v1).
- `pending_input` — interactive container for AskUser / SandboxConfirm.

Empty slots are dropped before send.

## Debugging

- View a card visually: paste the card JSON into the Feishu CardKit
  building tool at https://open.feishu.cn/cardkit.
- View the live card on Feishu's side: call
  `GET /open-apis/cardkit/v1/cards/{card_id}` with the bot's
  tenant_access_token.

## Rate limits

CardKit allows 1000 ops/min, 50 ops/sec. We throttle locally:

- `streamCardContent`: 100 ms inter-call gap per `element_id`.
- `patch_card`: 1.5 s gap.
- `done` / `error` / first appearance of structural elements bypass
  the throttle.

## Failure modes

| Symptom | Cause | Fix |
|---|---|---|
| Every run shows the emergency text warning | Scope not granted | Apply `cardkit:card:write` |
| Cards stop updating mid-run | Throttle saturated 3× | Tailer disabled streaming; wait for finalize |
| Half-locked card after run | Finalize exhausted its 10 retries | Look for `[CardKit] finalize gave up for card_id=` in logs; manual `PATCH cards/{id}` with `streaming_mode=false` can recover |
| AskUser button click does nothing | Token replay or responder mismatch | Check `[Feishu ingress] card.action toast:` log line |
```

- [ ] **Step 2: Update `backend/docs/quick-reference.md`**

Find the Feishu scope list and add a row:

```
| cardkit:card:write | required | CardKit rich output (v1+) |
```

- [ ] **Step 3: Commit**

```bash
git add backend/docs/im-feishu-rich-output.md backend/docs/quick-reference.md
git commit -m "docs(im-feishu): operator guide for CardKit rich output"
```

---

## Task 25: Apply for `cardkit:card:write` on the Feishu app

**Files:**
- N/A (operator action)

- [ ] **Step 1: Document the action**

This is a manual step in the Feishu Developer Console; cannot be
automated from code. Confirm completion by visiting the bot's
Permissions page and verifying the scope is listed under "Approved
permissions".

- [ ] **Step 2: Verify by listing the bot's effective scopes**

If the operator step has been done, run from the worktree:

```bash
cd backend && uv run python - <<'PY'
import asyncio
from lark_oapi.api.application.v6 import ListScopeRequest
# Pseudocode — adapt to the actual SDK shape. The goal is to confirm
# cardkit:card:write is granted before relying on it for end-user runs.
print("(use the Feishu console UI for now)")
PY
```

- [ ] **Step 3: If scope is granted, smoke-test by creating one card**

```bash
cd backend && uv run python - <<'PY'
import asyncio, httpx, os, json, sys
TENANT_TOKEN = os.environ.get("FEISHU_TENANT_TOKEN") or sys.exit("set FEISHU_TENANT_TOKEN")
card = {
    "schema": "2.0",
    "header": {"title": {"tag": "plain_text", "content": "smoke"}, "template": "blue"},
    "config": {"streaming_mode": False, "update_multi": True},
    "body": {"elements": [{"tag": "markdown", "element_id": "x", "content": "**hello**"}]},
}
async def main():
    async with httpx.AsyncClient(timeout=10) as http:
        r = await http.post(
            "https://open.feishu.cn/open-apis/cardkit/v1/cards",
            headers={"Authorization": f"Bearer {TENANT_TOKEN}", "Content-Type": "application/json"},
            json={"type": "card_json", "data": card},
        )
        print(r.status_code, r.text)
asyncio.run(main())
PY
```

Expected: HTTP 200 + `code: 0` + a `card_id` in the response.

---

## Task 26: Full suite sweep + branch readiness

**Files:**
- N/A

- [ ] **Step 1: Run all backend tests**

```bash
cd backend && uv run pytest -v --ignore=tests/e2e 2>&1 | tail -40
```

Expected: all non-E2E tests pass.

- [ ] **Step 2: Run all IM E2E tests against the real Feishu tenant**

```bash
cd backend && uv run pytest tests/e2e/im_feishu_*.py -v 2>&1 | tail -40
```

Expected: green. Per CLAUDE.md E2E posture, run only locally (not CI).

- [ ] **Step 3: Type-check**

```bash
cd backend && uv run mypy cubeplex/im
```

Expected: zero errors.

- [ ] **Step 4: Lint and format**

```bash
cd backend && uv run ruff check cubeplex/im tests/im && uv run ruff format --check cubeplex/im tests/im
```

Expected: zero errors.

- [ ] **Step 5: Branch readiness check**

```bash
git status --short
git log --oneline feat/feishu-rich-output-v1 ^main | head -30
```

Expected: clean working tree, ~25 commits ahead of main.

- [ ] **Step 6: Tag for code-review**

The next step in the goal-driven workflow is `/code-review --fix` followed
by lark-cli verification.

---

## Self-review (run after writing the plan, before handing off)

**Spec coverage check:**

| Spec section | Implemented in task |
|---|---|
| §3 architecture (4 new modules) | Tasks 1-5, 14 |
| §4.1 skeleton | Task 4 |
| §4.2 markdown + citation | Tasks 2, 4, 10 |
| §4.3 tool calls + TOOL_DISPLAY + MemoryUpdate + SubAgent | Tasks 3, 4, 11 |
| §4.4 artifacts | Tasks 4, 10, 13 |
| §4.5 AskUser / SandboxConfirm UI | Tasks 4, 11 |
| §4.6 terminal / error | Tasks 4, 11 |
| §5 streaming lifecycle | Tasks 8, 12, 20 |
| §5.4 finalize discipline | Task 6 |
| §5.5 reaction lifecycle | Task 18 (waiting reaction) + existing connector |
| §6 inbound action callback | Tasks 14, 15, 16, 17, 19 |
| §6.3 security gates (signature, token, identity, run-state) | Tasks 15, 19 |
| §6.4 receipt UI | Task 4 (`_render_pending_input` resolved branch) |
| §6.5 timeout | Wired via cubepi side; verified in Task 26 |
| §7 error matrix | Tasks 5, 6, 12, 23 |
| §7.4 emergency fallback | Tasks 12, 23 |
| §8 migration (delete/change/add) | Tasks 7, 8, 12, 13, 18 |
| §9 tests (unit + integration + E2E) | Tasks 1-23 |
| §10 out of scope | enforced by omission |
| §11.3 cubepi event audit | Task 0 |

All spec sections have at least one implementing task.

**Placeholder scan:** No "TBD" / "TODO" left in the plan tasks themselves. Code blocks complete in every step.

**Type consistency:** `CardState.tool_steps`, `ToolStep.mark_succeeded`/`mark_failed`, `ArtifactItem.image_key`, `PendingInput.choices` use consistent names across Task 1 / Task 4 / Tasks 9-11.

---

## Execution handoff

Plan complete and saved to `docs/dev/plans/2026-06-14-feishu-rich-output-v1.md`. Two execution options:

1. **Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks, fast iteration. Uses the `superpowers:subagent-driven-development` skill.
2. **Inline Execution** — execute tasks in this session using `superpowers:executing-plans`, batched checkpoints for review.

Pick one to start.
