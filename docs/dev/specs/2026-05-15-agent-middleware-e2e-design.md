# Agent Middleware E2E Coverage — Design

**Date**: 2026-05-15
**Branch**: `feat/agent-middleware-e2e` (new worktree)
**Status**: Approved for planning

## Goal

Add an end-to-end test suite that drives the full cubepi-based agent
(`_run_cubepi_path` in `streams/run_manager.py`) through a real LLM and
exercises the production cubeplex middleware stack
(`backend/cubeplex/middleware/*`). Coverage target: every middleware sees
at least one execution under a real provider call, without mocking.

The goal is regression detection for the middleware composition itself —
that the assembled stack actually runs without runtime errors and emits
the SSE events callers expect. This is **not** a behavioral spec of any
single middleware (those belong in unit tests where applicable).

## Non-Goals

- DB / checkpointer state assertions (user explicitly chose SSE-only).
- Per-middleware behavioral correctness (e.g., "memory writes the right
  row"). We only assert the middleware participates in the event stream.
- Reaching 100% coverage of every middleware code path. We accept that
  some branches (e.g., `compaction` triggered by token threshold,
  `attachments` with no file) may degrade to weak assertions if the test
  LLM (`qwen3.6-flash`) does not follow the prompt deterministically.

## Test Environment

- **Worktree**: created via `./scripts/new-worktree feat/agent-middleware-e2e`
  from the main repo root. After creation, `cat .worktree.env` to read
  allocated ports / DB names; `backend/.env` and
  `backend/config.development.local.yaml` must be copied from main if not
  auto-copied by the wrapper.
- **LLM**: `CUBEPLEX_E2E_LLM_*` already configured in `backend/.env`
  (currently `qwen3.6-flash` via `gateway.chat.sensedeal.vip`).
  `config.test.yaml` wires this provider as `default_model`.
- **Marker**: `@pytest.mark.real_llm` (same gate as existing
  `test_cubepi_path_*.py` files).
- **Fixtures**: `member_client` (existing), `collect_sse_events`
  (existing helper in `tests/e2e/conftest.py`).

## Test File

`backend/tests/e2e/test_agent_middleware_coverage.py`

One file, three tests:

### 1. `test_full_middleware_journey` — main scenario

Single conversation, multi-turn, designed so each turn naturally
triggers a different cluster of middleware. All POSTs go through
`/api/v1/ws/{wsId}/conversations/{id}/messages` and the SSE stream is
collected per turn.

| Turn | Prompt (intent) | Middleware exercised |
|------|-----------------|---------------------|
| 1 | "现在几点？顺便算一下 (2025 − 1949) × 4。" | `timestamps` (injects current time into system prompt), `cost` (emits `usage`), `sandbox` or `calculator` tool (whichever the builtin tool set exposes for arithmetic) |
| 2 | "把刚才的步骤整理成 todo 列表，每一步一条。" | `todo` (TodoWrite tool_call), continued `cost`/`timestamps` |
| 3 | "记住：我做数据处理偏好用 Python + pandas。" | `memory` (memory_write tool_call) |

Implicit-on-every-turn middleware (loaded but may not emit visible
events): `attachments` (attachment-hint injection — no attachment
present, so we only verify no error), `citation` (no source to cite —
same), `skills` (loaded; weak assertion).

**Assertions** (per turn unless noted):
- 0 events with `type == "error"`.
- Final event is `done`.
- At least one `usage` event (proves `cost` participated).
- Turn 1: at least one `tool_call` whose name is in the calculator/sandbox set.
- Turn 2: at least one `tool_call` whose name matches the todo tool.
- Turn 3: at least one `tool_call` whose name matches the memory-write tool.
- Across the whole conversation: union of seen event types ⊇
  `{text_delta, tool_call, tool_result, usage, done}`.

### 2. `test_subagent_dispatch_real_llm`

New conversation. Single prompt explicitly asking the agent to delegate
("用一个子代理帮我把 …"). Asserts the SSE stream contains a tool_call
for the subagent-spawn tool exposed by the `subagents` middleware, and
that the outer stream still terminates with `done` and zero errors.

If `qwen3.6-flash` refuses to dispatch (model capability limitation),
the test is marked `xfail(strict=False)` with a clear reason — we want
visibility, not a green-but-uncovered claim. (Note in the test docstring
why this is the only `xfail` in the file.)

### 3. `test_forced_compaction`

New conversation. Pre-seed the conversation by sending several large
filler turns (each prompt padded to ~2k tokens of innocuous content)
until total prompt size crosses the compaction middleware's configured
threshold. Then send one final short turn.

**Assertions**:
- The final turn's SSE stream completes (`done`, zero `error`).
- Either: a compaction-related event type appears in the stream, OR
  (weak fallback) the `usage` event of the final turn shows prompt
  tokens lower than the cumulative sent — evidence the history was
  trimmed.

If neither signal is detectable from SSE alone for this provider, the
test logs the full event-type set and degrades to "compaction
middleware did not crash the stack." Documented in the docstring.

## SSE Helper Use

`collect_sse_events(response_iterator)` already returns a `list[dict]`
in the cubeplex envelope shape (`{"type": ..., "data": {...}}`). The
tests build small predicates over that list — no new helpers needed.
If repeated logic emerges (e.g., "find tool_call by name"), inline a
local helper inside the test module; do not extend `conftest.py`.

## What "All Middleware" Maps To

Current middleware in `backend/cubeplex/middleware/`:

| Middleware | Triggered by | Test coverage |
|-----------|--------------|---------------|
| `timestamps` | Always | Turn 1 (implicit) |
| `cost` | Always | `usage` event check |
| `sandbox` | Tool call | Turn 1 |
| `todo` | Tool call | Turn 2 |
| `memory` | Tool call | Turn 3 |
| `subagents` | Tool call | Test 2 |
| `compaction` | History size | Test 3 |
| `skills` | Prompt patterns | Loaded; weak assertion |
| `attachments` | Attached file | Loaded; weak (no upload in this suite) |
| `citation` / `citations/` | Sourced answers | Loaded; weak |
| `artifacts` | Generated artifact | Loaded; weak |

"Weak assertion" = the middleware is in the composed stack (verified by
the run not erroring) but the test does not force its visible behavior.
Future work could add a dedicated upload + citation test; out of scope
here.

## Risks & Open Questions

- **Model determinism**: `qwen3.6-flash` may not consistently invoke
  named tools. Mitigation: prompts are explicit ("use the calculator
  tool", "write a todo list using the todo tool"), and assertions allow
  any tool name within a small set rather than a single exact match.
- **Worktree isolation**: tests must run inside the new worktree using
  its allocated DB / Redis prefix from `.worktree.env`. The existing
  Playwright/pytest auto-loaders handle this; verifying via
  `./scripts/worktree-env doctor` before first run.
- **Cost**: each full run hits the real provider ~5–10 turns. Acceptable
  for an E2E gated by `real_llm`; not run on every CI invocation.

## Out of Scope

- Frontend changes.
- New middleware.
- Refactoring `_run_cubepi_path` or the middleware composition order.
- Changes to `conftest.py` / shared fixtures (unless a flake forces it).
