# Agent Middleware E2E Coverage — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add three real-LLM E2E tests in a new worktree that drive the cubepi agent through the full cubeplex middleware stack and assert on SSE event streams only.

**Architecture:** Three test files under `backend/tests/e2e/middleware/`, each exercising one scenario (happy path, subagent dispatch, forced compaction). Shared constants + SSE helpers live in `_helpers.py` next to them so the three test files can be written in parallel without merge conflicts. All tests go through the production HTTP route `POST /api/v1/ws/{wsId}/conversations/{id}/messages`, reuse `member_client` + `collect_sse_events`, and hit the real LLM configured by `CUBEPLEX_E2E_LLM_*` (currently `qwen3.6-flash`).

**Tech Stack:** pytest + pytest-asyncio, httpx async client (from `member_client` fixture), `@pytest.mark.real_llm`, existing `collect_sse_events` helper.

**Note on spec deviation:** The spec called for a single test file. The plan splits it into three sibling files plus a `_helpers.py` so Tasks 3/4/5 can be dispatched as parallel subagents per the user's explicit request to maximize parallelism. Behavior, coverage, and assertions are identical.

**Parallelism map:**

```
Task 1 (worktree)  ──┐
                     ↓
Task 2 (research + _helpers.py)
                     ↓
        ┌────────────┼────────────┐
        ↓            ↓            ↓
     Task 3       Task 4       Task 5     ← three parallel subagents
        └────────────┬────────────┘
                     ↓
                  Task 6 (sweep)
```

---

## Task 1: Create and verify the worktree

**Files:**
- Create (by script): `<worktree_root>/.worktree.env`
- Verify presence of: `backend/.env`, `backend/config.development.local.yaml`

- [ ] **Step 1: Run new-worktree wrapper from main repo root**

Run (from `/home/chris/cubeplex`):

```bash
./scripts/new-worktree feat/agent-middleware-e2e
```

Expected: prints allocated slot, DB names, ports; creates the worktree directory as a sibling of main checkout. The exact path is printed by the wrapper — capture it (referred to below as `$WT`).

- [ ] **Step 2: Enter the worktree and inspect env**

```bash
cd $WT
cat .worktree.env
git rev-parse --abbrev-ref HEAD   # expect: feat/agent-middleware-e2e
git rev-parse --show-toplevel     # expect: $WT (NOT /home/chris/cubeplex)
```

Expected: branch is `feat/agent-middleware-e2e`, toplevel is the worktree path, `.worktree.env` shows non-default ports.

- [ ] **Step 3: Verify .env and config.development.local.yaml exist**

```bash
ls backend/.env backend/config.development.local.yaml
```

If either is missing, copy from main:

```bash
cp /home/chris/cubeplex/backend/.env backend/.env
cp /home/chris/cubeplex/backend/config.development.local.yaml backend/config.development.local.yaml
```

Verify E2E LLM vars are set:

```bash
grep -E "^CUBEPLEX_E2E_LLM_" backend/.env
```

Expected: `BASE_URL`, `API_KEY`, `MODEL_ID` all populated.

- [ ] **Step 4: Run worktree doctor**

```bash
./scripts/worktree-env doctor
```

Expected: all green (DBs exist, ports free, Postgres + Redis reachable, alembic at head).

- [ ] **Step 5: Install backend deps**

```bash
cd backend && make dev-install && cd -
```

Expected: completes without errors.

- [ ] **Step 6: Smoke-run one existing real_llm test**

```bash
cd backend && uv run pytest tests/e2e/test_cubepi_path_tools.py -v --maxfail=1 -m real_llm
```

Expected: PASS. If it fails, **stop and diagnose** — env is broken, no point continuing.

---

## Task 2: Research middleware tool names, SSE shape, and write `_helpers.py`

**Goal:** Read the middleware source, record exact tool names and event types, then write the shared scaffold. After this task, Tasks 3/4/5 can run in parallel because every constant and helper they need is already in `_helpers.py`.

**Files:**
- Create: `backend/tests/e2e/middleware/__init__.py` (empty)
- Create: `backend/tests/e2e/middleware/_helpers.py`
- Read-only: `backend/cubeplex/middleware/{todo,memory,sandbox,subagents}.py`, `backend/cubeplex/middleware/compaction/*.py`, `backend/cubeplex/streams/run_manager.py`, `backend/tests/e2e/conftest.py`, `backend/tests/e2e/test_cubepi_path_tools.py`

- [ ] **Step 1: Discover tool names**

```bash
cd backend
grep -nE "name *= *\"|AgentTool\(|register_tool" \
  cubeplex/middleware/todo.py \
  cubeplex/middleware/memory.py \
  cubeplex/middleware/sandbox.py \
  cubeplex/middleware/subagents.py
```

Record exact values for:
- Todo write tool name (e.g. `TodoWrite`)
- Memory write tool name (the write variant if multiple exist)
- Sandbox execute tool name
- Subagent spawn tool name

If a middleware registers multiple tools, pick the one most likely to fire on a "write/run/spawn" intent.

- [ ] **Step 2: Confirm SSE event types**

```bash
grep -nE "\"type\" *: *\"|type=\"[a-z_]+\"" cubeplex/streams/run_manager.py | head -40
```

Confirm the canonical strings: `text_delta`, `tool_call`, `tool_result`, `usage`, `error`, `done`. Use whatever the code actually emits — do not guess.

- [ ] **Step 3: Locate the compaction threshold**

```bash
grep -nE "threshold|max_tokens|trigger|should_compact" cubeplex/middleware/compaction/*.py | head -20
```

Record the condition (token-based or message-count-based) and default value. Note any env var override.

- [ ] **Step 4: Confirm the conversation + message endpoint contract**

Re-read `backend/tests/e2e/test_cubepi_path_tools.py` and record:
- Create-conversation request body
- Send-message request body
- How the SSE stream is opened (`client.stream("POST", ...)` vs `client.post(...)` with iter)
- How `collect_sse_events` is invoked

- [ ] **Step 5: Write `__init__.py`**

```bash
mkdir -p tests/e2e/middleware
```

Create `backend/tests/e2e/middleware/__init__.py`:

```python
```

(Empty file — pytest still discovers it.)

- [ ] **Step 6: Write `_helpers.py`**

Create `backend/tests/e2e/middleware/_helpers.py` with the discovered values. Replace every `<...>` placeholder with the value from Steps 1–4 — do not commit placeholders.

```python
"""Shared scaffolding for the agent-middleware-coverage E2E suite.

Constants and tiny helpers consumed by ``test_journey.py``,
``test_subagent.py``, and ``test_compaction.py``. See
``docs/superpowers/specs/2026-05-15-agent-middleware-e2e-design.md``.
"""

from __future__ import annotations

from typing import Any

from tests.e2e.conftest import collect_sse_events

# --- Tool names (verified against middleware source in Task 2) ---------------
TOOL_CALCULATOR = "calculator"          # from test_cubepi_path_tools.py
TOOL_TODO = "<exact name from grep>"
TOOL_MEMORY_WRITE = "<exact name from grep>"
TOOL_SANDBOX = "<exact name from grep>"
TOOL_SUBAGENT_SPAWN = "<exact name from grep>"

# --- SSE event types (verified against run_manager.py in Task 2) -------------
EVT_TEXT_DELTA = "text_delta"
EVT_TOOL_CALL = "tool_call"
EVT_TOOL_RESULT = "tool_result"
EVT_USAGE = "usage"
EVT_ERROR = "error"
EVT_DONE = "done"

# --- Compaction threshold (verified against compaction middleware in Task 2) -
# Replace with the actual recorded value, e.g. "trims when prompt > 8000 tokens"
COMPACTION_NOTE = "<recorded threshold>"


def events_of_type(events: list[dict[str, Any]], type_name: str) -> list[dict[str, Any]]:
    """Filter events by 'type' field."""
    return [e for e in events if e.get("type") == type_name]


def tool_call_names(events: list[dict[str, Any]]) -> list[str]:
    """Extract every tool_call's tool name from a list of SSE events."""
    names: list[str] = []
    for e in events_of_type(events, EVT_TOOL_CALL):
        data = e.get("data") or {}
        n = data.get("name") or e.get("name")
        if isinstance(n, str):
            names.append(n)
    return names


async def create_conversation(client: Any, ws_id: str, title: str) -> str:
    """Create a conversation and return its id."""
    resp = await client.post(
        f"/api/v1/ws/{ws_id}/conversations",
        json={"title": title},
    )
    assert resp.status_code in (200, 201), resp.text
    return str(resp.json()["id"])


async def post_turn(
    client: Any,
    ws_id: str,
    conv_id: str,
    content: str,
) -> list[dict[str, Any]]:
    """Send a user turn and return collected SSE events.

    If the existing ``test_cubepi_path_tools.py`` uses a different streaming
    pattern (e.g. iter_lines on a regular response), match that pattern
    here exactly — verified in Task 2 Step 4.
    """
    async with client.stream(
        "POST",
        f"/api/v1/ws/{ws_id}/conversations/{conv_id}/messages",
        json={"content": content},
    ) as resp:
        assert resp.status_code == 200, await resp.aread()
        return await collect_sse_events(resp)
```

**Important:** If Task 2 Step 4 reveals that `collect_sse_events` takes the response object differently (e.g. not awaited, or works on an iterator), adjust `post_turn` to match — it must mirror the working pattern in `test_cubepi_path_tools.py`, not the placeholder above.

- [ ] **Step 7: Verify the scaffold collects no tests but imports clean**

```bash
cd backend
uv run pytest tests/e2e/middleware/ -v
```

Expected: 0 tests collected, 0 import errors. If imports fail, the constants file is broken — fix before commit.

- [ ] **Step 8: Commit**

```bash
git add tests/e2e/middleware/__init__.py tests/e2e/middleware/_helpers.py
git commit -m "test(middleware-e2e): scaffold helpers with verified tool names"
```

---

## Task 3: `test_journey.py` — 3-turn happy path

**Files:**
- Create: `backend/tests/e2e/middleware/test_journey.py`

**Independent of Tasks 4 and 5. Runs in parallel.**

- [ ] **Step 1: Confirm context (parallel-subagent prerequisite)**

```bash
cd $WT                            # subagent must `cd` to the worktree path
cat .worktree.env | head -3       # confirm worktree env is loaded
git rev-parse --abbrev-ref HEAD   # expect: feat/agent-middleware-e2e
ls backend/tests/e2e/middleware/_helpers.py  # must exist (output of Task 2)
```

- [ ] **Step 2: Write the test**

Create `backend/tests/e2e/middleware/test_journey.py`:

```python
"""E2E: a single conversation that walks through the full middleware stack.

Turn 1 → timestamps, cost, calculator/sandbox.
Turn 2 → todo.
Turn 3 → memory write.
Loaded-but-quiet middleware (skills/citation/attachments/artifacts) covered
by zero-error assertions. See design doc 2026-05-15.
"""

from __future__ import annotations

import pytest

from tests.e2e.middleware._helpers import (
    EVT_DONE,
    EVT_ERROR,
    EVT_TEXT_DELTA,
    EVT_TOOL_CALL,
    EVT_TOOL_RESULT,
    EVT_USAGE,
    TOOL_CALCULATOR,
    TOOL_MEMORY_WRITE,
    TOOL_SANDBOX,
    TOOL_TODO,
    create_conversation,
    events_of_type,
    post_turn,
    tool_call_names,
)

pytestmark = pytest.mark.real_llm


@pytest.mark.asyncio
async def test_full_middleware_journey(member_client: tuple) -> None:  # type: ignore[type-arg]
    client, ws_id = member_client
    conv_id = await create_conversation(client, ws_id, "middleware journey")

    # Turn 1: time + arithmetic → calculator/sandbox, timestamps, cost
    t1 = await post_turn(
        client,
        ws_id,
        conv_id,
        "现在几点？顺便用 calculator 工具算 (2025 - 1949) * 4。",
    )
    assert events_of_type(t1, EVT_ERROR) == [], f"errors in t1: {t1}"
    assert t1[-1].get("type") == EVT_DONE
    assert events_of_type(t1, EVT_USAGE), "no usage event (cost middleware silent)"
    t1_tools = tool_call_names(t1)
    assert any(n in {TOOL_CALCULATOR, TOOL_SANDBOX} for n in t1_tools), (
        f"expected calculator/sandbox call, got {t1_tools}"
    )

    # Turn 2: todo
    t2 = await post_turn(
        client,
        ws_id,
        conv_id,
        "把刚才解题的步骤用 todo 工具整理成一个列表，每个步骤一条。",
    )
    assert events_of_type(t2, EVT_ERROR) == []
    assert t2[-1].get("type") == EVT_DONE
    t2_tools = tool_call_names(t2)
    assert TOOL_TODO in t2_tools, f"expected {TOOL_TODO} call, got {t2_tools}"

    # Turn 3: memory write
    t3 = await post_turn(
        client,
        ws_id,
        conv_id,
        "请把这条信息存进 memory：我做数据处理偏好用 Python + pandas。",
    )
    assert events_of_type(t3, EVT_ERROR) == []
    assert t3[-1].get("type") == EVT_DONE
    t3_tools = tool_call_names(t3)
    assert TOOL_MEMORY_WRITE in t3_tools, f"expected {TOOL_MEMORY_WRITE} call, got {t3_tools}"

    # Whole-conversation union check
    all_types = {e.get("type") for e in (t1 + t2 + t3)}
    assert {EVT_TEXT_DELTA, EVT_TOOL_CALL, EVT_TOOL_RESULT, EVT_USAGE, EVT_DONE} <= all_types
```

- [ ] **Step 3: Run only this test**

```bash
cd backend
uv run pytest tests/e2e/middleware/test_journey.py -v
```

Expected: PASS. If the model fails to call the named tool, verify first that the constant in `_helpers.py` matches the middleware source — do not weaken the assertion.

- [ ] **Step 4: Commit**

```bash
git add tests/e2e/middleware/test_journey.py
git commit -m "test(middleware-e2e): full middleware journey test"
```

---

## Task 4: `test_subagent.py` — explicit subagent dispatch

**Files:**
- Create: `backend/tests/e2e/middleware/test_subagent.py`

**Independent of Tasks 3 and 5. Runs in parallel.**

- [ ] **Step 1: Confirm context**

```bash
cd $WT
cat .worktree.env | head -3
git rev-parse --abbrev-ref HEAD                  # expect: feat/agent-middleware-e2e
ls backend/tests/e2e/middleware/_helpers.py      # must exist
```

- [ ] **Step 2: Write the test**

Create `backend/tests/e2e/middleware/test_subagent.py`:

```python
"""E2E: explicit subagent dispatch.

Confirms the subagents middleware registers a spawn tool and the agent
actually calls it under a real LLM. See design doc 2026-05-15.
"""

from __future__ import annotations

import pytest

from tests.e2e.middleware._helpers import (
    EVT_DONE,
    EVT_ERROR,
    TOOL_SUBAGENT_SPAWN,
    create_conversation,
    events_of_type,
    post_turn,
    tool_call_names,
)

pytestmark = pytest.mark.real_llm


@pytest.mark.asyncio
async def test_subagent_dispatch_real_llm(member_client: tuple) -> None:  # type: ignore[type-arg]
    """If qwen3.6-flash consistently refuses to spawn, convert to
    @pytest.mark.xfail(strict=False, reason=...) — do not soften the
    assertion to 'any tool'. We want visibility, not green-but-uncovered.
    """
    client, ws_id = member_client
    conv_id = await create_conversation(client, ws_id, "subagent dispatch")

    events = await post_turn(
        client,
        ws_id,
        conv_id,
        "请用 subagent 工具派一个子代理去帮我总结一句话：'cubeplex 是什么'，"
        "你只负责派单和汇总，不要自己回答。",
    )

    assert events_of_type(events, EVT_ERROR) == [], f"errors: {events}"
    assert events[-1].get("type") == EVT_DONE
    tools = tool_call_names(events)
    assert TOOL_SUBAGENT_SPAWN in tools, (
        f"expected {TOOL_SUBAGENT_SPAWN} call, got {tools}"
    )
```

- [ ] **Step 3: Run only this test**

```bash
cd backend
uv run pytest tests/e2e/middleware/test_subagent.py -v
```

Expected: PASS. If it consistently fails because the model refuses to spawn (verify by reading captured `text_delta` content), wrap the test with `@pytest.mark.xfail(strict=False, reason="qwen3.6-flash declines subagent spawn")` and re-run.

- [ ] **Step 4: Commit**

```bash
git add tests/e2e/middleware/test_subagent.py
git commit -m "test(middleware-e2e): subagent dispatch test"
```

---

## Task 5: `test_compaction.py` — forced history compaction

**Files:**
- Create: `backend/tests/e2e/middleware/test_compaction.py`

**Independent of Tasks 3 and 4. Runs in parallel.**

The filler size below assumes a token-based threshold around 8k. If Task 2 recorded a different threshold in `_helpers.py::COMPACTION_NOTE`, adjust the `range(...)` and `* 60` multiplier so total input clearly exceeds it.

- [ ] **Step 1: Confirm context**

```bash
cd $WT
cat .worktree.env | head -3
git rev-parse --abbrev-ref HEAD                  # expect: feat/agent-middleware-e2e
ls backend/tests/e2e/middleware/_helpers.py
grep COMPACTION_NOTE backend/tests/e2e/middleware/_helpers.py
```

- [ ] **Step 2: Write the test**

Create `backend/tests/e2e/middleware/test_compaction.py`:

```python
"""E2E: forced compaction.

Pre-seeds the conversation with large filler turns until history crosses
the compaction middleware's trigger threshold, then sends a short final
turn. Strong signal: final usage event reports trimmed prompt tokens.
Weak fallback: stream terminates cleanly. See design doc 2026-05-15.
"""

from __future__ import annotations

import pytest

from tests.e2e.middleware._helpers import (
    EVT_DONE,
    EVT_ERROR,
    EVT_USAGE,
    create_conversation,
    events_of_type,
    post_turn,
)

pytestmark = pytest.mark.real_llm


@pytest.mark.asyncio
async def test_forced_compaction(member_client: tuple) -> None:  # type: ignore[type-arg]
    client, ws_id = member_client
    conv_id = await create_conversation(client, ws_id, "compaction")

    filler = ("背景资料段落，仅用于撑大上下文。" * 60) + "请简短回复 ok。"
    total_user_chars = 0
    for i in range(5):
        evts = await post_turn(client, ws_id, conv_id, f"[{i}] {filler}")
        assert events_of_type(evts, EVT_ERROR) == [], f"errors in filler {i}"
        assert evts[-1].get("type") == EVT_DONE
        total_user_chars += len(filler)

    final = await post_turn(
        client,
        ws_id,
        conv_id,
        "用一句话总结上面对话的主题。",
    )
    assert events_of_type(final, EVT_ERROR) == [], f"errors in final: {final}"
    assert final[-1].get("type") == EVT_DONE

    # Strong signal: usage shows trimmed history.
    usage_events = events_of_type(final, EVT_USAGE)
    if usage_events:
        last_usage = usage_events[-1].get("data") or {}
        input_tokens = last_usage.get("input_tokens") or last_usage.get("prompt_tokens")
        if isinstance(input_tokens, int):
            rough_uncompacted = total_user_chars // 4  # chars/token ~ 4
            assert input_tokens < rough_uncompacted * 1.5, (
                f"history does not appear compacted: input_tokens={input_tokens}, "
                f"rough sent={rough_uncompacted}"
            )
```

- [ ] **Step 3: Run only this test**

```bash
cd backend
uv run pytest tests/e2e/middleware/test_compaction.py -v
```

Expected: PASS. If filler size is insufficient to cross the threshold (check usage tokens vs. `COMPACTION_NOTE`), bump `range(5)` and/or `* 60` and re-run. Do not delete the strong-signal assertion.

- [ ] **Step 4: Commit**

```bash
git add tests/e2e/middleware/test_compaction.py
git commit -m "test(middleware-e2e): forced compaction test"
```

---

## Task 6: Integration sweep + cleanup

**Files:**
- Possibly modify: any of the three test files if flakes surface

- [ ] **Step 1: Run the full middleware folder**

```bash
cd backend
uv run pytest tests/e2e/middleware/ -v -m real_llm
```

Expected: 3 PASS, 0 FAIL. If a test flakes (model nondeterminism), re-run twice more:

```bash
for i in 1 2; do uv run pytest tests/e2e/middleware/ -v -m real_llm; done
```

- [ ] **Step 2: Lint + type-check**

```bash
cd backend
make lint
make type-check
```

Expected: clean. Fix any issues inline.

- [ ] **Step 3: Regression check on existing real_llm tests**

```bash
cd backend
uv run pytest tests/e2e/test_cubepi_path_tools.py tests/e2e/test_cubepi_path_conversation.py -v -m real_llm
```

Expected: PASS. Confirms shared fixtures still work.

- [ ] **Step 4: Commit any fix-ups, then push**

```bash
git status
# if anything outstanding:
git add -A
git commit -m "test(middleware-e2e): lint and integration fixes"
git push -u origin feat/agent-middleware-e2e
```

- [ ] **Step 5: Hand off**

Invoke `superpowers:finishing-a-development-branch` to decide PR / merge path.

---

## Parallel Subagent Protocol (Tasks 3/4/5)

Per memory note `feedback_subagent_worktree_cwd`, subagents do not inherit cwd. When dispatching Tasks 3, 4, and 5 as parallel subagents in one message, each subagent prompt **must** include:

1. The absolute worktree path (`$WT` resolved).
2. An instruction to start with `cd <abs path> && cat .worktree.env && git rev-parse --abbrev-ref HEAD`.
3. The full task body from this plan (Steps 1–4 verbatim).
4. A note that `_helpers.py` already exists (Task 2 output) and lists which constants/helpers it provides.

Each subagent creates one new file. There is no shared-file edit, so no merge conflicts. Each commits its own work on `feat/agent-middleware-e2e`. The parent agent collects all three before running Task 6.

## Spec Coverage Check

- Worktree creation + env verification → Task 1 ✓
- Test-file scaffolding + tool-name discovery + helpers → Task 2 ✓
- 3-turn journey covering timestamps/cost/sandbox/todo/memory → Task 3 ✓
- Subagent dispatch → Task 4 ✓
- Forced compaction → Task 5 ✓
- Integration sweep, lint, type-check, regression check → Task 6 ✓
- "Weak-loaded" middleware (skills/citation/attachments/artifacts) → covered by zero-error assertions in Tasks 3/4/5 ✓
- SSE-only assertion policy → enforced throughout ✓
- Real LLM via `CUBEPLEX_E2E_LLM_*` → enforced via `pytest.mark.real_llm` ✓
- Parallel execution map → Tasks 3/4/5 each in own file with shared `_helpers.py` ✓
