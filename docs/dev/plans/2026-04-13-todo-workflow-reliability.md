# Todo Workflow Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden `TodoListMiddleware` so multi-step work uses a consistent checklist protocol, with aligned prompts, enforced todo invariants, ordered stale/finalization guards, bounded correction retries, and a low-cost closeout reminder.

**Architecture:** Keep the implementation centered in `cubeplex/middleware/todo.py`. The middleware should validate submitted `write_todos` payloads, reason about the last committed todo state for stale/finalization guards, and share one `_after_model_impl()` between sync and async hooks. Blocking guards must return `jump_to` state updates so the graph actually re-routes instead of continuing on the default edge. Guard feedback should use `SystemMessage`, not fabricated `ToolMessage` IDs, and the closeout nudge should live in the `write_todos` tool result path so each tool call still produces exactly one tool response.

**Tech Stack:** Python 3.12, LangGraph, LangChain middleware, Pydantic, pytest

**Spec:** `docs/superpowers/specs/2026-04-13-todo-workflow-reliability-design.md`

---

## File Structure

### Modified Files

| File | Change |
|------|--------|
| `cubeplex/middleware/todo.py` | Align prompt text, add invariant validation, add guard bookkeeping state, implement ordered `_after_model_impl()`, share logic between `after_model` and `aafter_model`, and emit the structural 3+ item reminder from `_write_todos()` |
| `tests/unit/test_middleware_todo.py` | Add prompt-alignment, validation, stale guard, finalization guard, retry escalation, atomic transition, and tool-result reminder tests |
| `tests/unit/test_graph.py` | Add a checkpointer-backed persistence test proving `todos` survive across invocations |

### No New Runtime Files

Keep everything in `cubeplex/middleware/todo.py` for v1. Do not split middleware into helper modules during this pass.

### Codebase Notes

- Existing LangGraph thread persistence already stores `PlanningState` fields, including `todos`, so resume is a verification/test problem in this repository rather than a new persistence feature.
- This plan intentionally does not enforce the spec’s "completed items must not be rewritten into a different task on later updates" rule. Without stable todo IDs, that requires fragile diff heuristics and is outside this v1 reliability pass.

---

## Task 1: Align Prompt Contract and Enforce Todo List Invariants

**Files:**
- Modify: `cubeplex/middleware/todo.py`
- Test: `tests/unit/test_middleware_todo.py`

- [ ] **Step 1: Write failing tests for prompt alignment and list validation**

```python
# tests/unit/test_middleware_todo.py
import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from cubeplex.middleware.todo import (
    TodoListMiddleware,
    WRITE_TODOS_SYSTEM_PROMPT,
    WRITE_TODOS_TOOL_DESCRIPTION,
    _write_todos,
)


def test_todo_prompts_align_with_single_in_progress_invariant():
    assert "exactly one task in_progress" in WRITE_TODOS_TOOL_DESCRIPTION
    assert "at most one task in_progress" in WRITE_TODOS_SYSTEM_PROMPT


def test_write_todos_returns_json_tool_message():
    runtime = SimpleNamespace(tool_call_id="tc-1")
    todos = [
        {"content": "Inspect payload shape", "status": "completed"},
        {"content": "Patch todo middleware", "status": "in_progress"},
    ]

    command = _write_todos(runtime, todos)

    assert command.update is not None
    assert command.update["todos"] == todos
    messages = command.update["messages"]
    assert len(messages) == 1
    assert isinstance(messages[0], ToolMessage)
    assert messages[0].tool_call_id == "tc-1"
    assert json.loads(messages[0].content) == {"todos": todos}


@pytest.mark.parametrize(
    ("todos", "expected"),
    [
        (
            [{"content": "Run tests", "status": "pending"}],
            "at least one in_progress",
        ),
        (
            [
                {"content": "Run tests", "status": "in_progress"},
                {"content": "Review output", "status": "in_progress"},
            ],
            "at most one in_progress",
        ),
        (
            [{"content": "", "status": "in_progress"}],
            "content must not be empty",
        ),
    ],
)
def test_todo_middleware_rejects_invalid_write_todos_payloads(todos, expected):
    mw = TodoListMiddleware()
    state = {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "tc-1",
                        "name": "write_todos",
                        "args": {"todos": todos},
                        "type": "tool_call",
                    }
                ],
            )
        ]
    }

    result = mw.after_model(state, runtime=SimpleNamespace())

    assert result is not None
    assert expected in result["messages"][0].content
    assert result["messages"][0].status == "error"


def test_todo_middleware_allows_all_completed_list():
    mw = TodoListMiddleware()
    state = {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "tc-1",
                        "name": "write_todos",
                        "args": {
                            "todos": [
                                {"content": "Inspect", "status": "completed"},
                                {"content": "Patch", "status": "completed"},
                            ]
                        },
                        "type": "tool_call",
                    }
                ],
            )
        ]
    }

    assert mw.after_model(state, runtime=SimpleNamespace()) is None
```

- [ ] **Step 2: Run the targeted tests and verify they fail**

Run: `uv run pytest tests/unit/test_middleware_todo.py -v`
Expected: FAIL because the current prompts still allow multiple `in_progress` tasks and `after_model()` only checks parallel `write_todos` calls.

- [ ] **Step 3: Update the prompt strings to match the v1 invariants**

```python
# cubeplex/middleware/todo.py
WRITE_TODOS_TOOL_DESCRIPTION = """Use this tool to create and manage a structured task list for your current work session.
...
1. **Task States**:
   - pending: Task not yet started
   - in_progress: Currently working on
   - completed: Task finished successfully
...
2. **Task Management**:
   - Update task status in real-time as you work
   - Mark tasks complete IMMEDIATELY after finishing
   - Unless all tasks are completed, keep exactly one task in_progress
   - If work remains after completing the current item, mark the next item in_progress in the same update
"""

WRITE_TODOS_SYSTEM_PROMPT = """## `write_todos`
...
## Important To-Do List Usage Notes to Remember
- The `write_todos` tool should never be called multiple times in parallel.
- Unless all tasks are completed, keep at most one task in_progress.
- If work remains after one task completes, mark the next task in_progress in the same write_todos call.
"""
```

- [ ] **Step 4: Implement todo validation helpers**

```python
# cubeplex/middleware/todo.py
def _submitted_write_todos_calls(last_ai_msg: AIMessage) -> list[dict[str, Any]]:
    return [tc for tc in (last_ai_msg.tool_calls or []) if tc["name"] == "write_todos"]


def _validate_todos(todos: list[Todo]) -> str | None:
    in_progress_count = 0
    unfinished_count = 0

    for todo in todos:
        content = todo["content"].strip()
        if not content:
            return "Error: todo item content must not be empty."
        if todo["status"] != "completed":
            unfinished_count += 1
        if todo["status"] == "in_progress":
            in_progress_count += 1

    if unfinished_count > 0 and in_progress_count == 0:
        return (
            "Error: todo list must include at least one in_progress item while "
            "work remains."
        )
    if in_progress_count > 1:
        return "Error: todo list may include at most one in_progress item."
    return None


def _todo_validation_errors(last_ai_msg: AIMessage) -> list[ToolMessage]:
    errors: list[ToolMessage] = []
    for tc in _submitted_write_todos_calls(last_ai_msg):
        todos = tc.get("args", {}).get("todos", [])
        error = _validate_todos(todos)
        if error is not None:
            errors.append(
                ToolMessage(
                    content=error,
                    tool_call_id=tc["id"],
                    status="error",
                )
            )
    return errors
```

- [ ] **Step 5: Route validation through a shared `_after_model_impl()` entry point**

```python
# cubeplex/middleware/todo.py
def _last_ai_message(messages: list[Any]) -> AIMessage | None:
    return next((msg for msg in reversed(messages) if isinstance(msg, AIMessage)), None)


def _after_model_impl(self, state: PlanningState[ResponseT]) -> dict[str, Any] | None:
    messages = state.get("messages", [])
    if not messages:
        return None

    last_ai_msg = _last_ai_message(messages)
    if last_ai_msg is None:
        return None

    parallel_error = self._parallel_write_todos_error(state)
    if parallel_error is not None:
        return parallel_error

    validation_errors = _todo_validation_errors(last_ai_msg)
    if validation_errors:
        return {"messages": validation_errors}

    return None
```

- [ ] **Step 6: Delegate both `after_model()` and `aafter_model()` to the shared implementation**

```python
# cubeplex/middleware/todo.py
@override
def after_model(
    self,
    state: PlanningState[ResponseT],
    runtime: Runtime[ContextT],
) -> dict[str, Any] | None:
    del runtime
    return self._after_model_impl(state)


@override
async def aafter_model(
    self,
    state: PlanningState[ResponseT],
    runtime: Runtime[ContextT],
) -> dict[str, Any] | None:
    del runtime
    return self._after_model_impl(state)
```

- [ ] **Step 7: Run the targeted tests and verify they pass**

Run: `uv run pytest tests/unit/test_middleware_todo.py -v`
Expected: PASS for prompt alignment, invariant validation, and the existing parallel-call test.

- [ ] **Step 8: Commit**

```bash
git add cubeplex/middleware/todo.py tests/unit/test_middleware_todo.py
git commit -m "feat: align and validate todo invariants"
```

---

## Task 2: Add Ordered Stale and Finalization Guards

**Files:**
- Modify: `cubeplex/middleware/todo.py`
- Test: `tests/unit/test_middleware_todo.py`

- [ ] **Step 1: Write failing tests for stale/finalization guard behavior**

```python
# tests/unit/test_middleware_todo.py
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage


def test_todo_middleware_emits_stale_todo_error_after_tool_iteration_without_write():
    mw = TodoListMiddleware()
    state = {
        "todos": [{"content": "Patch middleware", "status": "in_progress"}],
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "tc-1",
                        "name": "execute",
                        "args": {"command": "pytest tests/unit/test_middleware_todo.py -v"},
                        "type": "tool_call",
                    }
                ],
            ),
        ],
    }

    result = mw.after_model(state, runtime=SimpleNamespace())

    assert result is not None
    assert isinstance(result["messages"][0], SystemMessage)
    assert result["jump_to"] == "model"
    assert "todo list was not updated" in result["messages"][0].content


def test_todo_middleware_blocks_pure_text_finalization_with_unfinished_todos():
    mw = TodoListMiddleware()
    state = {
        "todos": [{"content": "Patch middleware", "status": "in_progress"}],
        "messages": [AIMessage(content="Implemented the change and everything is done.")],
    }

    result = mw.after_model(state, runtime=SimpleNamespace())

    assert result is not None
    assert isinstance(result["messages"][0], SystemMessage)
    assert result["jump_to"] == "model"
    assert "cannot finalize response" in result["messages"][0].content


def test_todo_middleware_skips_stale_guard_when_write_todos_occurs_in_same_iteration():
    mw = TodoListMiddleware()
    state = {
        "todos": [{"content": "Patch middleware", "status": "in_progress"}],
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "tc-1",
                        "name": "execute",
                        "args": {"command": "pytest tests/unit/test_middleware_todo.py -v"},
                        "type": "tool_call",
                    },
                    {
                        "id": "tc-2",
                        "name": "write_todos",
                        "args": {
                            "todos": [
                                {"content": "Patch middleware", "status": "completed"},
                                {"content": "Review results", "status": "in_progress"},
                            ]
                        },
                        "type": "tool_call",
                    },
                ],
            )
        ],
    }

    assert mw.after_model(state, runtime=SimpleNamespace()) is None
```

- [ ] **Step 2: Run the targeted tests and verify they fail**

Run: `uv run pytest tests/unit/test_middleware_todo.py -k "stale or finalization" -v`
Expected: FAIL because there are no stale/finalization guards, no `SystemMessage`-based correction path, and no `jump_to` routing override.

- [ ] **Step 3: Extend middleware state with retry and blocked-run bookkeeping**

```python
# cubeplex/middleware/todo.py
class PlanningState(AgentState[ResponseT]):
    todos: Annotated[NotRequired[list[Todo]], OmitFromInput]
    todo_guard_retries: Annotated[NotRequired[dict[str, int]], OmitFromInput]
    todo_guard_blocked: Annotated[NotRequired[dict[str, str]], OmitFromInput]


def _unfinished_todos(todos: list[Todo] | None) -> list[Todo]:
    return [todo for todo in (todos or []) if todo["status"] != "completed"]


def _pure_text_ai_response(last_ai_msg: AIMessage) -> bool:
    return bool(last_ai_msg.content) and not last_ai_msg.tool_calls


def _non_todo_tool_calls(last_ai_msg: AIMessage) -> list[dict[str, Any]]:
    return [tc for tc in (last_ai_msg.tool_calls or []) if tc["name"] != "write_todos"]
```

- [ ] **Step 4: Implement guard helpers using `SystemMessage`, not synthetic `ToolMessage` IDs**

```python
# cubeplex/middleware/todo.py
def _guard_retry_update(
    state: PlanningState[ResponseT],
    guard_type: str,
) -> tuple[int, dict[str, int]]:
    retries = dict(state.get("todo_guard_retries", {}))
    retries[guard_type] = retries.get(guard_type, 0) + 1
    return retries[guard_type], retries


def _guard_error_message(guard_type: str) -> str:
    if guard_type == "stale":
        return (
            "Error: work progressed on an active plan but the todo list was not "
            "updated. Call write_todos before continuing."
        )
    return (
        "Error: cannot finalize response while todo list still contains unfinished "
        "items. Update the list first."
    )


def _guard_response(
    state: PlanningState[ResponseT],
    guard_type: str,
) -> dict[str, Any]:
    retry_count, retries = _guard_retry_update(state, guard_type)
    message = _guard_error_message(guard_type)

    if retry_count >= 3:
        return {
            "jump_to": "model",
            "todo_guard_retries": retries,
            "todo_guard_blocked": {"guard_type": guard_type, "message": message},
            "messages": [
                SystemMessage(
                    content=(
                        "Todo synchronization failed repeatedly. Do not call any tools. "
                        "Respond to the user with a plain-text explanation that the run "
                        f"could not continue safely because: {message}"
                    )
                )
            ],
        }

    return {
        "jump_to": "model",
        "todo_guard_retries": retries,
        "messages": [SystemMessage(content=message)],
    }


def _reset_guard_retries() -> dict[str, int]:
    return {}
```

- [ ] **Step 5: Implement ordered guard checks against the last committed todo state**

```python
# cubeplex/middleware/todo.py
def _after_model_impl(self, state: PlanningState[ResponseT]) -> dict[str, Any] | None:
    messages = state.get("messages", [])
    if not messages:
        return None

    last_ai_msg = _last_ai_message(messages)
    if last_ai_msg is None:
        return None

    parallel_error = self._parallel_write_todos_error(state)
    if parallel_error is not None:
        return parallel_error

    validation_errors = _todo_validation_errors(last_ai_msg)
    if validation_errors:
        return {"messages": validation_errors}

    blocked_guard = state.get("todo_guard_blocked")
    if blocked_guard:
        # After escalation, only a final plain-text explanation is allowed.
        # Any tool-calling response keeps the run blocked and routes back to model.
        if _pure_text_ai_response(last_ai_msg):
            return {
                "jump_to": "end",
                "todo_guard_blocked": None,
                "todo_guard_retries": _reset_guard_retries(),
            }
        return {
            "jump_to": "model",
            "todo_guard_blocked": blocked_guard,
            "todo_guard_retries": dict(state.get("todo_guard_retries", {})),
            "messages": [
                SystemMessage(
                    content=(
                        "Todo synchronization is already blocked. Do not call any tools. "
                        "Respond to the user with a plain-text explanation that the run "
                        f"could not continue safely because: {blocked_guard['message']}"
                    )
                )
            ],
        }

    # Guards reason about the last committed todo state. Submitted write_todos
    # payloads have been validated above, but do not become state["todos"] until
    # tool execution completes.
    unfinished = _unfinished_todos(state.get("todos"))
    if unfinished:
        if _non_todo_tool_calls(last_ai_msg) and not _submitted_write_todos_calls(last_ai_msg):
            return self._guard_response(state, "stale")
        if _pure_text_ai_response(last_ai_msg):
            return self._guard_response(state, "finalization")

    # Retry counters measure consecutive failures, so any non-guarded iteration
    # clears them by design.
    return {"todo_guard_retries": _reset_guard_retries()}
```

- [ ] **Step 6: Declare jump destinations on both sync and async hooks**

```python
# cubeplex/middleware/todo.py
@override
def after_model(
    self,
    state: PlanningState[ResponseT],
    runtime: Runtime[ContextT],
) -> dict[str, Any] | None:
    del runtime
    return self._after_model_impl(state)


after_model.__can_jump_to__ = ["model", "end"]


@override
async def aafter_model(
    self,
    state: PlanningState[ResponseT],
    runtime: Runtime[ContextT],
) -> dict[str, Any] | None:
    del runtime
    return self._after_model_impl(state)


aafter_model.__can_jump_to__ = ["model", "end"]
```

- [ ] **Step 7: Run the targeted tests and verify they pass**

Run: `uv run pytest tests/unit/test_middleware_todo.py -k "stale or finalization" -v`
Expected: PASS for stale guard, finalization guard, same-iteration `write_todos` suppression, and `jump_to` routing updates.

- [ ] **Step 8: Commit**

```bash
git add cubeplex/middleware/todo.py tests/unit/test_middleware_todo.py
git commit -m "feat: add ordered todo guard checks"
```

---

## Task 3: Add Retry Escalation and Structural Closeout Reminder

**Files:**
- Modify: `cubeplex/middleware/todo.py`
- Test: `tests/unit/test_middleware_todo.py`

- [ ] **Step 1: Write failing tests for escalation and closeout reminder behavior**

```python
# tests/unit/test_middleware_todo.py
def test_todo_middleware_escalates_after_repeated_stale_failures():
    mw = TodoListMiddleware()
    state = {
        "todos": [{"content": "Patch middleware", "status": "in_progress"}],
        "todo_guard_retries": {"stale": 2},
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "tc-1",
                        "name": "execute",
                        "args": {"command": "pytest tests/unit/test_middleware_todo.py -v"},
                        "type": "tool_call",
                    }
                ],
            )
        ],
    }

    result = mw.after_model(state, runtime=SimpleNamespace())

    assert result is not None
    assert isinstance(result["messages"][0], SystemMessage)
    assert result["jump_to"] == "end"
    assert result["todo_guard_blocked"]["guard_type"] == "stale"


def test_todo_middleware_allows_atomic_complete_and_next_start_transition():
    mw = TodoListMiddleware()
    state = {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "tc-1",
                        "name": "write_todos",
                        "args": {
                            "todos": [
                                {"content": "Inspect payload shape", "status": "completed"},
                                {"content": "Patch middleware", "status": "in_progress"},
                                {"content": "Review results", "status": "pending"},
                            ]
                        },
                        "type": "tool_call",
                    }
                ],
            )
        ]
    }

    assert mw.after_model(state, runtime=SimpleNamespace()) is None


def test_write_todos_adds_closeout_nudge_for_completed_three_item_list():
    runtime = SimpleNamespace(tool_call_id="tc-1")
    todos = [
        {"content": "Inspect", "status": "completed"},
        {"content": "Patch", "status": "completed"},
        {"content": "Review", "status": "completed"},
    ]

    command = _write_todos(runtime, todos)
    payload = json.loads(command.update["messages"][0].content)

    assert payload["todos"] == todos
    assert "verify outcomes before finalizing" in payload["reminder"]


def test_write_todos_skips_closeout_nudge_for_short_lists():
    runtime = SimpleNamespace(tool_call_id="tc-1")
    todos = [
        {"content": "Inspect", "status": "completed"},
        {"content": "Patch", "status": "completed"},
    ]

    command = _write_todos(runtime, todos)
    payload = json.loads(command.update["messages"][0].content)

    assert payload == {"todos": todos}
```

- [ ] **Step 2: Run the targeted tests and verify they fail**

Run: `uv run pytest tests/unit/test_middleware_todo.py -k "escalates or closeout or atomic" -v`
Expected: FAIL because retry escalation is not fully exercised and `_write_todos()` does not yet emit the structural reminder.

- [ ] **Step 3: Implement the structural 3+ item reminder in the tool result path**

```python
# cubeplex/middleware/todo.py
def _closeout_nudge(todos: list[Todo]) -> str | None:
    if len(todos) < 3:
        return None
    if any(todo["status"] != "completed" for todo in todos):
        return None
    return (
        "Reminder: the todo list is complete. Verify outcomes before finalizing "
        "and report any remaining uncertainty clearly."
    )


def _build_todo_tool_message(
    tool_call_id: str | None,
    todos: list[Todo],
) -> ToolMessage:
    payload: dict[str, Any] = {"todos": todos}
    reminder = _closeout_nudge(todos)
    if reminder is not None:
        payload["reminder"] = reminder
    return ToolMessage(
        content=json.dumps(payload, ensure_ascii=False),
        tool_call_id=tool_call_id,
    )
```

- [ ] **Step 4: Verify retry semantics stay consecutive by design**

```python
# cubeplex/middleware/todo.py
# No separate code path is needed here beyond the existing reset behavior:
# successful, non-guarded iterations clear todo_guard_retries intentionally
# because the budget tracks consecutive correction failures rather than
# failures across the full session lifetime.
```

- [ ] **Step 5: Run the targeted tests and verify they pass**

Run: `uv run pytest tests/unit/test_middleware_todo.py -v`
Expected: PASS for escalation, atomic transitions, and the 3+ item reminder emitted from `_write_todos()`.

- [ ] **Step 6: Commit**

```bash
git add cubeplex/middleware/todo.py tests/unit/test_middleware_todo.py
git commit -m "feat: add todo closeout reminder and escalation"
```

---

## Task 4: Prove Todos Persist Across Invocations

**Files:**
- Modify: `tests/unit/test_graph.py`

- [ ] **Step 1: Write a failing persistence test for todos**

```python
# tests/unit/test_graph.py
import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver

from cubeplex.agents.graph import create_cubeplex_agent


class _TodoWritingLLM:
    def __init__(self):
        self.bind_tools = lambda *_args, **_kwargs: self

    async def ainvoke(self, *_args, **_kwargs):
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "tc-1",
                    "name": "write_todos",
                    "args": {
                        "todos": [{"content": "Patch middleware", "status": "in_progress"}]
                    },
                    "type": "tool_call",
                }
            ],
        )

    def invoke(self, *_args, **_kwargs):
        raise AssertionError("sync path not used in this test")


@pytest.mark.asyncio
async def test_agent_persists_todos_across_invocations():
    llm = _TodoWritingLLM()
    checkpointer = MemorySaver()
    agent = create_cubeplex_agent(llm=llm, tools=[], checkpointer=checkpointer)
    config = {"configurable": {"thread_id": "todo-thread"}}

    await agent.ainvoke({"messages": [HumanMessage(content="First")]}, config=config)
    state = await agent.aget_state(config)

    assert state.values["todos"] == [{"content": "Patch middleware", "status": "in_progress"}]
```

- [ ] **Step 2: Run the targeted test and verify it fails**

Run: `uv run pytest tests/unit/test_graph.py -k persists_todos -v`
Expected: FAIL because the repository does not yet have an explicit todo persistence test.

- [ ] **Step 3: Adjust the test only if the mock/tool wiring needs to use the real middleware path**

```python
# tests/unit/test_graph.py
# Prefer a minimal proof, but if one invoke plus aget_state() only proves a
# same-run checkpoint snapshot, strengthen the test with a second invoke on the
# same thread using a staged fake LLM that verifies the prior todo ToolMessage
# or todo state was restored before the resumed model call.
# Do not use a second pure-text terminal response, because finalization guards
# would make that mock ambiguous once Task 2 is implemented.
```

- [ ] **Step 4: Run the graph tests and verify they pass**

Run: `uv run pytest tests/unit/test_graph.py -v`
Expected: PASS, including the new checkpointer-backed todo persistence test.

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_graph.py
git commit -m "test: cover todo state persistence"
```

---

## Task 5: Final Verification

**Files:**
- Modify: `cubeplex/middleware/todo.py`
- Modify: `tests/unit/test_middleware_todo.py`
- Modify: `tests/unit/test_graph.py`

- [ ] **Step 1: Run the focused unit suite**

Run: `uv run pytest tests/unit/test_middleware_todo.py tests/unit/test_graph.py -v`
Expected: PASS

- [ ] **Step 2: Run the full unit suite**

Run: `uv run pytest tests/unit -v`
Expected: PASS with no regressions in unrelated middleware tests.

- [ ] **Step 3: Inspect the final diff**

Run: `git diff -- cubeplex/middleware/todo.py tests/unit/test_middleware_todo.py tests/unit/test_graph.py`
Expected: Diff shows prompt alignment, invariant validation, shared sync/async guard logic, `SystemMessage`-based guard feedback, structural reminder in `_write_todos()`, and persistence coverage only.

- [ ] **Step 4: Commit only if verification required follow-up fixes**

```bash
git add cubeplex/middleware/todo.py tests/unit/test_middleware_todo.py tests/unit/test_graph.py
git commit -m "feat: harden todo workflow reliability"
```

---

## Self-Review

### Spec Coverage

- Prompt text aligned with the new single-`in_progress` contract: covered in Task 1
- Todo schema and list invariants: covered in Task 1
- Ordered stale/finalization guards and retry escalation: covered in Task 2
- Structural 3+ item closeout nudge: covered in Task 3
- Resume reliability grounded in current checkpointer behavior: covered in Task 4
- Async production path via `aafter_model`: covered in Task 1 and Task 2

### Placeholder Scan

The plan contains exact files, commands, tests, and implementation snippets for each task. No TODO/TBD placeholders remain.

### Type Consistency

`PlanningState` owns `todos`, `todo_guard_retries`, and `todo_guard_blocked`. Guard feedback uses `SystemMessage` instead of synthetic `ToolMessage` IDs, blocking guard returns carry `jump_to`, and the closeout reminder stays inside `_write_todos()` so each tool call still produces one tool result message.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-13-todo-workflow-reliability.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
