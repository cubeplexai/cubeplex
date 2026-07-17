# Memory Reflection Quality Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix three structural gaps in the per-turn reflection agent so it stores fewer duplicates, sees tool results, and knows what's already in memory before deciding what to save.

**Architecture:** Three independent edits across two service files and one prompt file. No new tables, no migrations, no API changes. Each task is self-contained and has its own unit tests. Work in worktree `feat/memory-reflection-quality` (backend port 8071).

**Tech Stack:** Python 3.13, FastAPI, SQLModel, cubepi Agent, pytest-asyncio

**Spec:** `docs/dev/specs/2026-06-03-memory-reflection-quality-design.md`

---

## File Map

| File | What changes |
|---|---|
| `backend/cubeplex/prompts/reflection_system.py` | Rewrite prompt — conservative bias + mandatory search-first |
| `backend/cubeplex/services/reflection_runner.py` | `ReflectionInput` gets `existing_memory_items`; `_build_seed_prompt` renders it |
| `backend/cubeplex/streams/run_manager.py` | Extract tool summaries from agent messages; load personal memory before spawning reflection |
| `backend/tests/unit/test_reflection_runner.py` | Extend with seed-prompt and existing-memory tests |
| `backend/tests/unit/test_reflection_tool_summaries.py` | New — unit tests for tool-summary extraction |

---

## Task 1: Rewrite reflection_system.py prompt (C2)

**Files:**
- Modify: `backend/cubeplex/prompts/reflection_system.py`
- Test: `backend/tests/unit/test_reflection_runner.py` (smoke — verify prompt is importable and non-empty)

- [ ] **Step 1: Replace the prompt**

Replace the entire content of `backend/cubeplex/prompts/reflection_system.py`:

```python
"""System prompt for the detached memory-reflection agent."""

REFLECTION_SYSTEM_PROMPT: str = """\
You are a memory-curation assistant. Your only job is to review the last \
turn of a conversation and decide whether anything new is worth remembering.

Most turns contain nothing worth saving. When in doubt, do not save.

You have three tools:
- memory_search: check whether a fact is already stored.
- memory_save:   add a new memory.
- memory_update: refine an existing memory.

Before calling memory_save, always call memory_search to check whether a \
closely related item already exists. If one does, call memory_update instead, \
or skip entirely if the existing item already covers it.

Save only when ALL of the following are true:
- The user expressed a clear preference, correction, or durable fact about \
themselves, their team, or their project.
- It would change how you respond in a future conversation.
- It is NOT already covered by an item in the current memory shown above.

Do NOT save:
- Preferences or facts already present in the memory block above.
- Temporary state, one-off task steps, or in-progress status.
- Ephemeral values (device codes, one-time URLs, transient error messages).
- Speculative or low-confidence inferences.
- Restatements of what the assistant just did.

Scope: use 'personal' unless the user explicitly said to share with the team.

Output: call memory_search / memory_save / memory_update as needed, then end. \
If nothing is worth saving, end immediately without calling any tool. \
Do not explain — the user will not see your text.
"""
```

- [ ] **Step 2: Verify import and run existing tests**

```bash
cd /home/chris/cubeplex/.worktrees/feat/memory-reflection-quality/backend
uv run pytest tests/unit/test_reflection_runner.py -v
```

Expected: all existing tests pass (no behavioural change yet — tests don't inspect the prompt string).

- [ ] **Step 3: Commit**

```bash
cd /home/chris/cubeplex/.worktrees/feat/memory-reflection-quality
git add backend/cubeplex/prompts/reflection_system.py
git commit -m "fix(memory): add conservative bias and mandatory search-first to reflection prompt"
```

---

## Task 2: Add existing_memory_items to ReflectionInput and seed prompt (C3 — runner side)

**Files:**
- Modify: `backend/cubeplex/services/reflection_runner.py`
- Modify: `backend/tests/unit/test_reflection_runner.py`

- [ ] **Step 1: Write failing tests**

Add to `backend/tests/unit/test_reflection_runner.py`:

```python
from cubeplex.services.reflection_runner import ReflectionRunner


class TestBuildSeedPrompt:
    """Unit tests for _build_seed_prompt — no async needed."""

    def _runner(self) -> ReflectionRunner:
        return ReflectionRunner(
            user_event_service=MagicMock(),
            agent_factory=MagicMock(),
        )

    def _inp(
        self,
        *,
        existing: list[tuple[str, str, str]] | None = None,
        tool_summaries: list[dict[str, str]] | None = None,
    ) -> ReflectionInput:
        return ReflectionInput(
            conversation_id="c",
            run_id="r",
            user_id="u",
            workspace_id=None,
            turn=ReflectionTurn(
                user_message="USER MSG",
                assistant_message="ASST MSG",
                tool_summaries=tool_summaries or [],
            ),
            existing_memory_items=existing or [],
        )

    def test_no_existing_memory_no_memory_block(self) -> None:
        seed = self._runner()._build_seed_prompt(self._inp())
        assert "current memory" not in seed
        assert "USER MSG" in seed
        assert "ASST MSG" in seed

    def test_existing_memory_renders_block(self) -> None:
        items = [
            ("mem-abc", "preference", "用户偏好中文交流"),
            ("mem-def", "project_fact", "CubePi 是 Agent 框架"),
        ]
        seed = self._runner()._build_seed_prompt(self._inp(existing=items))
        assert "current memory" in seed
        assert "[mem-abc]" in seed
        assert "(preference)" in seed
        assert "用户偏好中文交流" in seed
        assert "[mem-def]" in seed
        # memory block appears BEFORE the turn
        assert seed.index("current memory") < seed.index("Last turn")

    def test_existing_memory_content_truncated_to_200_chars(self) -> None:
        long_content = "x" * 300
        items = [("mem-xyz", "project_fact", long_content)]
        seed = self._runner()._build_seed_prompt(self._inp(existing=items))
        assert long_content not in seed
        assert "x" * 200 in seed

    def test_tool_summaries_rendered(self) -> None:
        summaries = [
            {"name": "execute", "args_summary": "pip install foo", "outcome": "ok"},
            {"name": "execute", "args_summary": "twitter whoami", "outcome": "error: HTTP 403"},
        ]
        seed = self._runner()._build_seed_prompt(self._inp(tool_summaries=summaries))
        assert "Tools called" in seed
        assert "execute(pip install foo) -> ok" in seed
        assert "execute(twitter whoami) -> error: HTTP 403" in seed
```

- [ ] **Step 2: Run to confirm tests fail**

```bash
cd /home/chris/cubeplex/.worktrees/feat/memory-reflection-quality/backend
uv run pytest tests/unit/test_reflection_runner.py::TestBuildSeedPrompt -v
```

Expected: `AttributeError` or `TypeError` — `ReflectionInput` has no `existing_memory_items` and `_build_seed_prompt` takes `turn` not `inp`.

- [ ] **Step 3: Implement the changes**

Replace `reflection_runner.py` with:

```python
"""Out-of-band memory reflection — runs after AgentEndEvent.

Spawns a detached cubepi Agent (cheap model, memory tools only) seeded with
the last conversation turn plus the current memory snapshot. Captures any
memory_save / memory_update tool executions and publishes a UserEvent so
the frontend can surface the change.

Failure semantics: fire-and-forget. Timeout, LLM errors, and memory write
errors are logged and swallowed; never propagate to the main conversation.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from cubepi import Agent
from cubepi.agent.types import AgentEvent

from cubeplex.models.user_event import UserEventType
from cubeplex.services.reflection_context import set_reflection_source
from cubeplex.services.user_event import PublishUserEventInput, UserEventService

logger = logging.getLogger(__name__)

_CONTENT_TRUNCATE = 200  # max chars per existing memory item in seed


@dataclass
class ReflectionTurn:
    user_message: str
    assistant_message: str
    tool_summaries: list[dict[str, str]] = field(default_factory=list)
    # each: {"name": "...", "args_summary": "...", "outcome": "ok"|"error"}


@dataclass
class ReflectionInput:
    conversation_id: str
    run_id: str
    user_id: str
    workspace_id: str | None
    turn: ReflectionTurn
    existing_memory_items: list[tuple[str, str, str]] = field(default_factory=list)
    # each: (memory_id, type_value, content)


# Agent factory signature: given a ReflectionInput, build & return an Agent
# whose tools include memory_save/memory_update/memory_search bound to the
# user's MemoryService. Concrete factory wired in run_manager / DI setup.
AgentFactory = Callable[[ReflectionInput], "Agent[Any]"]


class ReflectionRunner:
    def __init__(
        self,
        *,
        user_event_service: UserEventService,
        agent_factory: AgentFactory,
        timeout_sec: float = 30.0,
    ) -> None:
        self._svc = user_event_service
        self._make_agent = agent_factory
        self._timeout = timeout_sec
        self._seen_runs: set[str] = set()  # idempotency

    async def reflect(self, inp: ReflectionInput) -> None:
        if inp.run_id in self._seen_runs:
            logger.debug("reflection already completed for run_id=%s, skipping", inp.run_id)
            return
        try:
            await asyncio.wait_for(self._reflect_impl(inp), timeout=self._timeout)
        except TimeoutError:
            logger.warning(
                "reflection timed out for run_id=%s conversation_id=%s",
                inp.run_id,
                inp.conversation_id,
            )
            return
        except Exception:
            logger.exception(
                "reflection failed for run_id=%s conversation_id=%s",
                inp.run_id,
                inp.conversation_id,
            )
            return
        self._seen_runs.add(inp.run_id)

    async def _reflect_impl(self, inp: ReflectionInput) -> None:
        agent = self._make_agent(inp)
        seed = self._build_seed_prompt(inp)

        items: list[dict[str, Any]] = []

        # cubepi calls listeners with (event, signal) — accept both positionally.
        def listener(event: AgentEvent, signal: Any = None) -> None:
            if event.type != "tool_execution_end":
                return
            name = getattr(event, "tool_name", None)
            if name not in ("memory_save", "memory_update"):
                return
            payload = self._extract_memory_result(event)
            if payload is not None:
                items.append(
                    {
                        "op": "save" if name == "memory_save" else "update",
                        **payload,
                    }
                )

        unsub = agent.subscribe(listener)
        try:
            # Keep the ContextVar active across wait_for_idle: cubepi can
            # execute memory tool calls after prompt() returns (they finish
            # during the idle drain), and tool callbacks must see
            # reflection_source_active() == True to tag writes correctly.
            with set_reflection_source():
                await agent.prompt(seed)
                await agent.wait_for_idle()
        finally:
            unsub()

        if not items:
            return

        await self._svc.publish(
            PublishUserEventInput(
                user_id=inp.user_id,
                workspace_id=inp.workspace_id,
                type=UserEventType.MEMORY_UPDATED,
                payload={
                    "conversation_id": inp.conversation_id,
                    "run_id": inp.run_id,
                    "items": items,
                },
            )
        )

    def _build_seed_prompt(self, inp: ReflectionInput) -> str:
        turn = inp.turn

        memory_block = ""
        if inp.existing_memory_items:
            lines = [
                f"- [{mid}] ({mtype}) {mcontent[:_CONTENT_TRUNCATE]}"
                for mid, mtype, mcontent in inp.existing_memory_items
            ]
            memory_block = (
                "Your current memory for this user (personal, active):\n"
                + "\n".join(lines)
                + "\n\n"
            )

        tools_block = ""
        if turn.tool_summaries:
            tools_block = "\n\nTools called in this turn:\n" + "\n".join(
                f"- {t['name']}({t.get('args_summary', '')}) -> {t.get('outcome', 'ok')}"
                for t in turn.tool_summaries
            )

        return (
            f"{memory_block}"
            "Last turn for review:\n\n"
            f"USER: {turn.user_message}\n\n"
            f"ASSISTANT: {turn.assistant_message}"
            f"{tools_block}"
        )

    def _extract_memory_result(self, event: AgentEvent) -> dict[str, Any] | None:
        # tool_execution_end carries the AgentToolResult; memory_save returns
        # {"status": "saved", "memory_id": "..."} and memory_update returns
        # {"status": "updated", "memory_id": "..."} as JSON text content.
        try:
            result = getattr(event, "result", None)
            if result is None or not result.content:
                return None
            text = result.content[0].text
            obj = json.loads(text)
        except Exception:
            return None
        if obj.get("status") not in ("saved", "updated"):
            return None
        memory_id = obj.get("memory_id")
        if not memory_id:
            return None
        return {"memory_id": memory_id}
```

- [ ] **Step 4: Run tests**

```bash
cd /home/chris/cubeplex/.worktrees/feat/memory-reflection-quality/backend
uv run pytest tests/unit/test_reflection_runner.py -v
```

Expected: all tests pass including the new `TestBuildSeedPrompt` class.

- [ ] **Step 5: Commit**

```bash
cd /home/chris/cubeplex/.worktrees/feat/memory-reflection-quality
git add backend/cubeplex/services/reflection_runner.py \
        backend/tests/unit/test_reflection_runner.py
git commit -m "feat(memory): inject existing personal memory into reflection seed prompt"
```

---

## Task 3: Extract tool summaries from agent state messages (C1)

**Files:**
- Modify: `backend/cubeplex/streams/run_manager.py`
- Create: `backend/tests/unit/test_reflection_tool_summaries.py`

- [ ] **Step 1: Write failing tests**

Create `backend/tests/unit/test_reflection_tool_summaries.py`:

```python
"""Unit tests for the _extract_tool_summaries helper extracted from run_manager."""

from __future__ import annotations

import pytest
from cubepi.providers.base import (
    AssistantMessage,
    TextContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)

# Import the helper once it's been extracted into a testable location.
# It lives as a module-level function in run_manager; we import it directly.
from cubeplex.streams.run_manager import _extract_tool_summaries


def _user(text: str = "hi") -> UserMessage:
    return UserMessage(content=[TextContent(text=text)])


def _assistant_with_calls(*calls: tuple[str, str, dict]) -> AssistantMessage:
    # calls: (call_id, tool_name, arguments)
    content = [ToolCall(id=cid, name=name, arguments=args) for cid, name, args in calls]
    return AssistantMessage(content=content)  # type: ignore[arg-type]


def _result(call_id: str, tool_name: str, text: str, *, is_error: bool = False) -> ToolResultMessage:
    return ToolResultMessage(
        tool_call_id=call_id,
        tool_name=tool_name,
        content=[TextContent(text=text)],
        is_error=is_error,
    )


def test_empty_messages_returns_empty() -> None:
    assert _extract_tool_summaries([]) == []


def test_no_tool_calls_returns_empty() -> None:
    msgs = [_user("hello"), AssistantMessage(content=[TextContent(text="hi")])]
    assert _extract_tool_summaries(msgs) == []


def test_single_tool_call_ok() -> None:
    msgs = [
        _user("run something"),
        _assistant_with_calls(("tc1", "execute", {"command": "pip install foo"})),
        _result("tc1", "execute", "Successfully installed foo"),
    ]
    summaries = _extract_tool_summaries(msgs)
    assert len(summaries) == 1
    assert summaries[0]["name"] == "execute"
    assert "pip install foo" in summaries[0]["args_summary"]
    assert summaries[0]["outcome"] == "ok"


def test_error_result_prefixes_error() -> None:
    msgs = [
        _user("test"),
        _assistant_with_calls(("tc1", "execute", {"command": "twitter whoami"})),
        _result("tc1", "execute", "HTTP 403", is_error=True),
    ]
    summaries = _extract_tool_summaries(msgs)
    assert summaries[0]["outcome"].startswith("error:")
    assert "HTTP 403" in summaries[0]["outcome"]


def test_args_truncated_to_150_chars() -> None:
    long_cmd = "x" * 300
    msgs = [
        _user("run"),
        _assistant_with_calls(("tc1", "execute", {"command": long_cmd})),
        _result("tc1", "execute", "ok"),
    ]
    summaries = _extract_tool_summaries(msgs)
    assert len(summaries[0]["args_summary"]) <= 150


def test_outcome_truncated_to_150_chars() -> None:
    long_output = "y" * 300
    msgs = [
        _user("run"),
        _assistant_with_calls(("tc1", "execute", {"command": "cmd"})),
        _result("tc1", "execute", long_output, is_error=True),
    ]
    summaries = _extract_tool_summaries(msgs)
    assert len(summaries[0]["outcome"]) <= len("error: ") + 150


def test_capped_at_10_summaries() -> None:
    calls = [(f"tc{i}", "execute", {"command": f"cmd{i}"}) for i in range(15)]
    results = [_result(f"tc{i}", "execute", f"out{i}") for i in range(15)]
    msgs = [_user("run many")] + [_assistant_with_calls(*calls)] + results
    summaries = _extract_tool_summaries(msgs)
    assert len(summaries) <= 10


def test_only_tools_after_last_user_message() -> None:
    # Tools before the last UserMessage should be ignored
    msgs = [
        _user("first"),
        _assistant_with_calls(("old_tc", "execute", {"command": "old"})),
        _result("old_tc", "execute", "old result"),
        _user("second"),
        _assistant_with_calls(("new_tc", "execute", {"command": "new"})),
        _result("new_tc", "execute", "new result"),
    ]
    summaries = _extract_tool_summaries(msgs)
    assert len(summaries) == 1
    assert "new" in summaries[0]["args_summary"]
```

- [ ] **Step 2: Run to confirm import fails**

```bash
cd /home/chris/cubeplex/.worktrees/feat/memory-reflection-quality/backend
uv run pytest tests/unit/test_reflection_tool_summaries.py -v 2>&1 | head -20
```

Expected: `ImportError` — `_extract_tool_summaries` does not exist yet.

- [ ] **Step 3: Add `_extract_tool_summaries` to run_manager and wire it**

In `backend/cubeplex/streams/run_manager.py`, add the following module-level helper near the top of the file (after imports, before the class definition). Find the `class RunManager` line and insert above it:

```python
def _extract_tool_summaries(messages: list[Any]) -> list[dict[str, str]]:
    """Build compact tool-call summaries from a turn's message list.

    Collects all ToolResultMessages that appear after the last UserMessage,
    paired with their corresponding ToolCall arguments. Capped at 10 entries;
    args and error text truncated to 150 chars each.
    """
    from cubepi.providers.base import (
        AssistantMessage,
        TextContent,
        ToolCall,
        ToolResultMessage,
        UserMessage,
    )

    _ARGS_LIMIT = 150
    _RESULT_LIMIT = 150
    _MAX_SUMMARIES = 10

    # Find the last UserMessage index
    last_user_idx = -1
    for i, msg in enumerate(messages):
        if isinstance(msg, UserMessage):
            last_user_idx = i
    if last_user_idx == -1:
        return []

    # Index tool call args by call_id from AssistantMessages after last user msg
    tool_args: dict[str, str] = {}
    for msg in messages[last_user_idx + 1 :]:
        if isinstance(msg, AssistantMessage):
            for part in msg.content:
                if isinstance(part, ToolCall):
                    args_str = ", ".join(
                        f"{k}={repr(v)}" for k, v in part.arguments.items()
                    )
                    tool_args[part.id] = args_str[:_ARGS_LIMIT]

    # Build summaries from ToolResultMessages
    summaries: list[dict[str, str]] = []
    for msg in messages[last_user_idx + 1 :]:
        if not isinstance(msg, ToolResultMessage):
            continue
        if len(summaries) >= _MAX_SUMMARIES:
            break
        result_text = "".join(
            c.text for c in msg.content if isinstance(c, TextContent)
        )
        if msg.is_error:
            outcome = f"error: {result_text[:_RESULT_LIMIT]}"
        else:
            outcome = "ok"
        summaries.append(
            {
                "name": msg.tool_name,
                "args_summary": tool_args.get(msg.tool_call_id, ""),
                "outcome": outcome,
            }
        )
    return summaries
```

Then in the `_run_reflection` closure (around line 1511), replace the `tool_summaries=[]` hardcode:

```python
# was:
turn=ReflectionTurn(
    user_message=user_msg_text,
    assistant_message=last_assistant,
    tool_summaries=[],
),

# becomes:
turn=ReflectionTurn(
    user_message=user_msg_text,
    assistant_message=last_assistant,
    tool_summaries=_extract_tool_summaries(agent_ref.state.messages),
),
```

- [ ] **Step 4: Run tests**

```bash
cd /home/chris/cubeplex/.worktrees/feat/memory-reflection-quality/backend
uv run pytest tests/unit/test_reflection_tool_summaries.py -v
```

Expected: all 8 tests pass.

- [ ] **Step 5: Commit**

```bash
cd /home/chris/cubeplex/.worktrees/feat/memory-reflection-quality
git add backend/cubeplex/streams/run_manager.py \
        backend/tests/unit/test_reflection_tool_summaries.py
git commit -m "feat(memory): populate tool_summaries in reflection turn from agent message history"
```

---

## Task 4: Load personal memory in run_manager and pass to reflection (C3 — run_manager side)

**Files:**
- Modify: `backend/cubeplex/streams/run_manager.py`

No new unit test file needed — the runner-side rendering is already covered by Task 2's tests. This task wires the DB load.

- [ ] **Step 1: Add memory load inside `_run_reflection`**

In `backend/cubeplex/streams/run_manager.py`, inside the `_run_reflection` async closure, add a memory load block after `last_assistant` is confirmed non-empty and before the `ReflectionInput` is constructed. The full updated closure body (from `last_assistant = ...` to `await _runner.reflect(inp)`) becomes:

```python
last_assistant = _last_assistant_text(agent_ref.state.messages)
if not last_assistant:
    return
user_msg_text = _stringify_user_msg(user_msg_ref)

# Load the user's active personal memory to give the reflection
# agent a complete picture before it decides what to save.
_existing_items: list[tuple[str, str, str]] = []
try:
    import datetime

    from cubeplex.models.memory import MemoryScope, MemoryStatus
    from cubeplex.repositories.memory import MemoryRepository

    async with _ue_session_maker() as _mem_session:
        _mem_repo = MemoryRepository(
            _mem_session,
            user_id=ctx.user_id,
            org_id=ctx.org_id,
            workspace_id=ctx.workspace_id,
        )
        _all_personal = await _mem_repo.list(
            scope=MemoryScope.PERSONAL,
            status=MemoryStatus.ACTIVE,
            limit=200,
        )
    # Sort by recency of use, then creation; take top 40.
    _sorted = sorted(
        _all_personal,
        key=lambda m: (
            m.last_used_at or datetime.datetime.min.replace(tzinfo=datetime.UTC),
            m.created_at,
        ),
        reverse=True,
    )[:40]
    _existing_items = [(m.id, m.type.value, m.content) for m in _sorted]
except Exception:
    logger.warning(
        "reflection: failed to load existing memory for run_id={}", run_id, exc_info=True
    )

inp = ReflectionInput(
    conversation_id=conversation_id,
    run_id=run_id,
    user_id=ctx.user_id,
    workspace_id=ctx.workspace_id,
    turn=ReflectionTurn(
        user_message=user_msg_text,
        assistant_message=last_assistant,
        tool_summaries=_extract_tool_summaries(agent_ref.state.messages),
    ),
    existing_memory_items=_existing_items,
)
async with _ue_session_maker() as _session:
    _repo = UserEventRepository(_session)
    _svc = UserEventService(repo=_repo, bus=bus)
    _runner = ReflectionRunner(
        user_event_service=_svc,
        agent_factory=agent_factory,
    )
    await _runner.reflect(inp)
```

- [ ] **Step 2: Run mypy to confirm types**

```bash
cd /home/chris/cubeplex/.worktrees/feat/memory-reflection-quality/backend
uv run mypy cubeplex/streams/run_manager.py cubeplex/services/reflection_runner.py cubeplex/prompts/reflection_system.py --ignore-missing-imports 2>&1 | tail -10
```

Expected: `Success: no issues found` (or only pre-existing errors unrelated to these files).

- [ ] **Step 3: Run the full unit test suite for changed modules**

```bash
cd /home/chris/cubeplex/.worktrees/feat/memory-reflection-quality/backend
uv run pytest tests/unit/test_reflection_runner.py \
             tests/unit/test_reflection_tool_summaries.py \
             tests/unit/test_reflection_context.py \
             tests/unit/test_reflection_factory.py -v
```

Expected: all pass.

- [ ] **Step 4: Commit**

```bash
cd /home/chris/cubeplex/.worktrees/feat/memory-reflection-quality
git add backend/cubeplex/streams/run_manager.py
git commit -m "feat(memory): load personal memory snapshot into reflection input before spawning agent"
```

---

## Task 5: Pre-PR sweep

- [ ] **Step 1: Run full unit test suite**

```bash
cd /home/chris/cubeplex/.worktrees/feat/memory-reflection-quality/backend
uv run pytest tests/unit/ -v 2>&1 | tail -20
```

Expected: all pass.

- [ ] **Step 2: Run mypy on all changed files**

```bash
cd /home/chris/cubeplex/.worktrees/feat/memory-reflection-quality/backend
uv run mypy cubeplex/ --ignore-missing-imports 2>&1 | grep -E "error:|Success"
```

Expected: no new errors.

- [ ] **Step 3: Run ruff**

```bash
cd /home/chris/cubeplex/.worktrees/feat/memory-reflection-quality/backend
uv run ruff check cubeplex/prompts/reflection_system.py \
                  cubeplex/services/reflection_runner.py \
                  cubeplex/streams/run_manager.py
```

Expected: no issues.
