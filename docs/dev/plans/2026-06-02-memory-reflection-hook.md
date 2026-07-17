# Memory Reflection Hook Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an `on_run_end` hook to cubepi that fires once after every agent run completes, then use it in cubeplex's `ReflectionMiddleware` to prompt the agent to proactively save memories.

**Architecture:** cubepi gains `on_run_end` as a new middleware hook that runs after all turns and tool calls finish, before `AgentEndEvent`. cubeplex implements `ReflectionMiddleware` using that hook to inject a reflection `UserMessage`, causing the agent to call `memory_save`/`memory_update` if the turn contained memorable content. The reflection turn is tagged `metadata["is_reflection"]=True` so the frontend can treat it specially later.

**Tech Stack:** Python, cubepi middleware system, cubeplex FastAPI backend, Alembic (no migrations needed — pure Python changes).

---

## File Map

**cubepi repo (`/home/chris/cubepi`):**
- Modify: `cubepi/middleware/base.py` — add `on_run_end` to `Middleware` + `compose_middleware`
- Modify: `cubepi/agent/loop.py` — thread `on_run_end` through `_run_loop_inner`, `_run_loop`, three public entry-points, `_run_agent_loop_resume_body`
- Modify: `cubepi/agent/agent.py` — extract `on_run_end` from `_mw_hooks`, wire through `_run_prompt` / `_run_continuation` / `_run_hitl_resume`
- Create: `tests/middleware/test_on_run_end.py`

**cubeplex repo (`/home/chris/cubeplex/backend`):**
- Create: `cubeplex/prompts/reflection.py`
- Create: `cubeplex/middleware/reflection.py`
- Modify: `cubeplex/streams/run_manager.py` — append `ReflectionMiddleware` as entry #12
- Create: `tests/unit/test_reflection_middleware.py`

---

## Task 1 — cubepi: Add `on_run_end` to `Middleware` base + `compose_middleware`

**Files:**
- Modify: `cubepi/middleware/base.py`

- [ ] **Step 1: Add `on_run_end` method to `Middleware` class**

In `cubepi/middleware/base.py`, add after the `after_model_response` method (line 55):

```python
    async def on_run_end(
        self,
        ctx: Any,  # AgentContext — avoid circular import
        *,
        signal: Any = None,
    ) -> "list[Message] | None":
        raise NotImplementedError
```

- [ ] **Step 2: Add `on_run_end` composition block to `compose_middleware`**

At the end of `compose_middleware`, before `return hooks` (after the `amr_chain` block, ~line 198):

```python
    ore_chain = [m for m in middlewares if _has_method(m, "on_run_end")]
    if ore_chain:

        async def composed_ore(ctx, *, signal=None):
            all_inject: list[Message] = []
            for mw in ore_chain:
                result = await mw.on_run_end(ctx, signal=signal)
                if result:
                    all_inject.extend(result)
            return all_inject or None

        hooks["on_run_end"] = composed_ore
```

- [ ] **Step 3: Verify no import errors**

```bash
cd /home/chris/cubepi && python -c "from cubepi.middleware.base import Middleware, compose_middleware; print('ok')"
```
Expected: `ok`

---

## Task 2 — cubepi: Thread `on_run_end` through loop.py

**Files:**
- Modify: `cubepi/agent/loop.py`

- [ ] **Step 1: Add `on_run_end` parameter to `_run_loop_inner`, fix early-return paths, insert hook**

`_run_loop_inner` starts at line 419. Add the new parameter after `get_follow_up_messages` and add `_reflection_fired`:

```python
async def _run_loop_inner(
    *,
    current_context: AgentContext,
    new_messages: list[Message],
    provider: Provider,
    model: Model,
    convert_to_llm: Callable,
    transform_context: Callable | None,
    transform_system_prompt: Callable | None,
    after_model_response: Callable | None,
    before_tool_call: Callable | None,
    after_tool_call: Callable | None,
    should_stop_after_turn: Callable | None,
    get_steering_messages: Callable | None,
    get_follow_up_messages: Callable | None,
    on_run_end: Callable | None,
    stream_options: StreamOptions | None,
    tool_execution: str,
    emit: Callable,
) -> None:
    opts = stream_options or StreamOptions()
    first_turn = True
    _reflection_fired = False
```

**Fix `turn_action.decision == "stop"` path (line ~527):** this currently emits `AgentEndEvent` and `return`s, bypassing `on_run_end`. Change it to break the inner while instead so the outer while handles termination:

```python
                    if turn_action.decision == "stop":
                        await emit_event(
                            emit, TurnEndEvent(message=message, tool_results=[])
                        )
                        # Break inner while so outer while runs on_run_end before AgentEndEvent.
                        has_more_tool_calls = False
                        break
```

**Fix `should_stop_after_turn` path (line ~585):** same issue — currently `AgentEndEvent` + `return`. At this point `TurnEndEvent` was already emitted at line ~581, so just break:

```python
            if should_stop_after_turn:
                stop_ctx = ShouldStopAfterTurnContext(
                    message=message,
                    tool_results=tool_results,
                    context=current_context,
                    new_messages=new_messages,
                )
                if await should_stop_after_turn(stop_ctx):
                    # Break inner while so outer while runs on_run_end before AgentEndEvent.
                    has_more_tool_calls = False
                    break
```

**Error/aborted path (line ~472) stays unchanged** — no reflection on failed runs:

```python
            if message.stop_reason in ("error", "aborted"):
                await emit_event(emit, MessageEndEvent(message=message))
                await emit_event(emit, TurnEndEvent(message=message, tool_results=[]))
                await emit_event(emit, AgentEndEvent(messages=new_messages))
                return
```

**Outer `while True` loop:** replace the existing `break` at the end (after the `get_follow_up_messages` drain, currently line 633) with:

```python
        # After inner loop completes, check for follow-up messages
        if get_follow_up_messages:
            follow_ups = await get_follow_up_messages() or []
            if follow_ups:
                for msg in follow_ups:
                    await emit_event(emit, MessageStartEvent(message=msg))
                    await emit_event(emit, MessageEndEvent(message=msg))
                    current_context.messages.append(msg)
                    new_messages.append(msg)
                first_turn = False
                continue

        # on_run_end fires exactly once per prompt() call, after all normal
        # turns and follow-ups are drained. _reflection_fired prevents the
        # reflection pass itself from triggering another reflection.
        # Skipped for error/aborted runs (those return early before reaching here).
        if on_run_end and not _reflection_fired:
            _reflection_fired = True
            inject = await on_run_end(current_context, signal=opts.signal)
            if inject:
                for msg in inject:
                    await emit_event(emit, MessageStartEvent(message=msg))
                    await emit_event(emit, MessageEndEvent(message=msg))
                    current_context.messages.append(msg)
                    new_messages.append(msg)
                first_turn = False
                continue

        break
```

- [ ] **Step 2: Add `on_run_end` to `_run_loop` and pass through to `_run_loop_inner`**

`_run_loop` starts at line 369. Add parameter after `get_follow_up_messages`:

```python
async def _run_loop(
    *,
    current_context: AgentContext,
    new_messages: list[Message],
    provider: Provider,
    model: Model,
    convert_to_llm: Callable,
    transform_context: Callable | None,
    transform_system_prompt: Callable | None,
    after_model_response: Callable | None,
    before_tool_call: Callable | None,
    after_tool_call: Callable | None,
    should_stop_after_turn: Callable | None,
    get_steering_messages: Callable | None,
    get_follow_up_messages: Callable | None,
    on_run_end: Callable | None,
    stream_options: StreamOptions | None,
    tool_execution: str,
    emit: Callable,
) -> None:
    try:
        await _run_loop_inner(
            current_context=current_context,
            new_messages=new_messages,
            provider=provider,
            model=model,
            convert_to_llm=convert_to_llm,
            transform_context=transform_context,
            transform_system_prompt=transform_system_prompt,
            after_model_response=after_model_response,
            before_tool_call=before_tool_call,
            after_tool_call=after_tool_call,
            should_stop_after_turn=should_stop_after_turn,
            get_steering_messages=get_steering_messages,
            get_follow_up_messages=get_follow_up_messages,
            on_run_end=on_run_end,
            stream_options=stream_options,
            tool_execution=tool_execution,
            emit=emit,
        )
    except (HitlDetached, HitlAborted):
        return
```

- [ ] **Step 3: Add `on_run_end` to the three public entry-points and `_run_agent_loop_resume_body`**

In each of `run_agent_loop`, `run_agent_loop_continue`, `run_agent_loop_resume`, and `_run_agent_loop_resume_body`, add `on_run_end: Callable | None = None` to the signature and pass `on_run_end=on_run_end` to every `_run_loop(...)` call within them.

`run_agent_loop` (line 31) — add after `get_follow_up_messages`:
```python
    on_run_end: Callable | None = None,
```
And in its `_run_loop(...)` call, add:
```python
        on_run_end=on_run_end,
```

`run_agent_loop_continue` (line 88) — same pattern.

`run_agent_loop_resume` (line 144) — same pattern; also pass `on_run_end` down into `_run_agent_loop_resume_body`.

`_run_agent_loop_resume_body` (line ~200) — add `on_run_end: Callable | None` to its signature and pass to `_run_loop(...)` call at its end.

- [ ] **Step 4: Verify import**

```bash
cd /home/chris/cubepi && python -c "from cubepi.agent.loop import run_agent_loop; print('ok')"
```
Expected: `ok`

---

## Task 3 — cubepi: Wire `on_run_end` through `Agent`

**Files:**
- Modify: `cubepi/agent/agent.py`

- [ ] **Step 1: Add `on_run_end` parameter to `Agent.__init__`**

In `Agent.__init__` (line 124), add after `should_stop_after_turn`:

```python
        on_run_end: Callable | None = None,
```

After the `_mw_hooks` extraction block (after line 172), add:

```python
        self.on_run_end = on_run_end or _mw_hooks.get("on_run_end")
```

- [ ] **Step 2: Pass `on_run_end` through `_run_prompt`**

In `_run_prompt` (line 327), add to the `run_agent_loop(...)` call:

```python
                on_run_end=self.on_run_end,
```

- [ ] **Step 3: Pass `on_run_end` through `_run_continuation`**

In `_run_continuation` (line 349), add to the `run_agent_loop_continue(...)` call:

```python
                on_run_end=self.on_run_end,
```

- [ ] **Step 4: Pass `on_run_end` through `_run_hitl_resume`**

In `_run_hitl_resume` (line 582), add to the `run_agent_loop_resume(...)` call:

```python
                on_run_end=self.on_run_end,
```

- [ ] **Step 5: Verify import**

```bash
cd /home/chris/cubepi && python -c "from cubepi.agent.agent import Agent; print('ok')"
```
Expected: `ok`

---

## Task 4 — cubepi: Tests for `on_run_end`

**Files:**
- Create: `cubepi/tests/middleware/test_on_run_end.py`

- [ ] **Step 1: Write the test file**

```python
"""on_run_end hook tests."""
from __future__ import annotations

import pytest

from cubepi import Agent, Model
from cubepi.agent.types import AgentContext
from cubepi.middleware.base import Middleware, compose_middleware
from cubepi.providers.base import TextContent, UserMessage
from cubepi.providers.faux import FauxProvider, faux_assistant_message


def _mk_ctx() -> AgentContext:
    return AgentContext(system_prompt="", messages=[])


# ---------------------------------------------------------------------------
# compose_middleware unit tests
# ---------------------------------------------------------------------------


class _InjectOne(Middleware):
    async def on_run_end(self, ctx, *, signal=None):
        return [UserMessage(content=[TextContent(text="reflect")])]


class _InjectTwo(Middleware):
    async def on_run_end(self, ctx, *, signal=None):
        return [
            UserMessage(content=[TextContent(text="a")]),
            UserMessage(content=[TextContent(text="b")]),
        ]


class _ReturnNone(Middleware):
    async def on_run_end(self, ctx, *, signal=None):
        return None


class _Plain(Middleware):
    pass


def test_no_middleware_hook_absent() -> None:
    hooks = compose_middleware([_Plain()])
    assert "on_run_end" not in hooks


def test_returns_none_hook_absent_when_all_none() -> None:
    """compose returns None when all middleware return None."""
    hooks = compose_middleware([_ReturnNone()])
    assert "on_run_end" in hooks


@pytest.mark.asyncio
async def test_single_middleware_returns_messages() -> None:
    hooks = compose_middleware([_InjectOne()])
    result = await hooks["on_run_end"](_mk_ctx())
    assert result is not None
    assert len(result) == 1
    assert result[0].content[0].text == "reflect"


@pytest.mark.asyncio
async def test_multiple_middleware_concatenate() -> None:
    hooks = compose_middleware([_InjectOne(), _InjectTwo()])
    result = await hooks["on_run_end"](_mk_ctx())
    assert result is not None
    assert len(result) == 3


@pytest.mark.asyncio
async def test_all_none_returns_none() -> None:
    hooks = compose_middleware([_ReturnNone()])
    result = await hooks["on_run_end"](_mk_ctx())
    assert result is None


# ---------------------------------------------------------------------------
# Agent integration tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_run_end_fires_after_main_run() -> None:
    """on_run_end injects a message and the agent runs one more model call."""
    provider = FauxProvider()
    # Main response + one reflection response
    provider.set_responses([
        faux_assistant_message("main"),
        faux_assistant_message("reflected"),
    ])

    fired: list[str] = []

    class _Reflect(Middleware):
        async def on_run_end(self, ctx, *, signal=None):
            fired.append("fired")
            return [UserMessage(content=[TextContent(text="reflect now")])]

    agent = Agent(
        model=Model(id="test", provider="faux"),
        provider=provider,
        middleware=[_Reflect()],
    )
    await agent.prompt("hi")

    assert provider.call_count == 2
    assert fired == ["fired"]


@pytest.mark.asyncio
async def test_on_run_end_fires_exactly_once() -> None:
    """Reflection pass does NOT trigger another on_run_end (_reflection_fired guard)."""
    provider = FauxProvider()
    provider.set_responses([
        faux_assistant_message("main"),
        faux_assistant_message("reflected"),
    ])

    fire_count = 0

    class _CountFires(Middleware):
        async def on_run_end(self, ctx, *, signal=None):
            nonlocal fire_count
            fire_count += 1
            return [UserMessage(content=[TextContent(text="reflect")])]

    agent = Agent(
        model=Model(id="test", provider="faux"),
        provider=provider,
        middleware=[_CountFires()],
    )
    await agent.prompt("hi")

    assert fire_count == 1
    assert provider.call_count == 2


@pytest.mark.asyncio
async def test_on_run_end_none_does_not_add_turn() -> None:
    """Returning None from on_run_end does not trigger an extra model call."""
    provider = FauxProvider()
    provider.set_responses([faux_assistant_message("main")])

    class _NoOp(Middleware):
        async def on_run_end(self, ctx, *, signal=None):
            return None

    agent = Agent(
        model=Model(id="test", provider="faux"),
        provider=provider,
        middleware=[_NoOp()],
    )
    await agent.prompt("hi")

    assert provider.call_count == 1


@pytest.mark.asyncio
async def test_on_run_end_injected_messages_in_history() -> None:
    """Messages injected by on_run_end appear in agent.state.messages."""
    from cubepi.providers.base import AssistantMessage

    provider = FauxProvider()
    provider.set_responses([
        faux_assistant_message("main"),
        faux_assistant_message("reflection response"),
    ])

    class _Inject(Middleware):
        async def on_run_end(self, ctx, *, signal=None):
            return [UserMessage(content=[TextContent(text="reflect")])]

    agent = Agent(
        model=Model(id="test", provider="faux"),
        provider=provider,
        middleware=[_Inject()],
    )
    await agent.prompt("hi")

    texts = [
        c.text
        for m in agent.state.messages
        if isinstance(m, AssistantMessage)
        for c in m.content
        if hasattr(c, "text")
    ]
    assert "main" in texts
    assert "reflection response" in texts


@pytest.mark.asyncio
async def test_on_run_end_fires_via_should_stop_after_turn() -> None:
    """on_run_end fires when should_stop_after_turn exits the inner loop."""
    from cubepi.agent.types import ShouldStopAfterTurnContext
    from cubepi.middleware.base import Middleware as _Mw

    provider = FauxProvider()
    provider.set_responses([
        faux_assistant_message("main"),
        faux_assistant_message("reflected"),
    ])

    fired: list[str] = []

    class _StopAfterFirst(_Mw):
        _seen = 0
        async def should_stop_after_turn(self, ctx: ShouldStopAfterTurnContext) -> bool:
            self._seen += 1
            return self._seen == 1  # stop after first turn

    class _Reflect(_Mw):
        async def on_run_end(self, ctx, *, signal=None):
            fired.append("fired")
            return [UserMessage(content=[TextContent(text="reflect")])]

    agent = Agent(
        model=Model(id="test", provider="faux"),
        provider=provider,
        middleware=[_StopAfterFirst(), _Reflect()],
    )
    await agent.prompt("hi")

    assert fired == ["fired"]
    assert provider.call_count == 2


@pytest.mark.asyncio
async def test_on_run_end_skipped_on_error() -> None:
    """on_run_end does NOT fire when stop_reason is error/aborted."""
    from cubepi.providers.faux import faux_assistant_message as _faux

    provider = FauxProvider()
    # Return a message with stop_reason="error"
    err_msg = _faux("oops")
    err_msg = err_msg.model_copy(update={"stop_reason": "error"})
    provider.set_responses([err_msg])

    fired: list[str] = []

    class _Reflect(Middleware):
        async def on_run_end(self, ctx, *, signal=None):
            fired.append("fired")
            return None

    agent = Agent(
        model=Model(id="test", provider="faux"),
        provider=provider,
        middleware=[_Reflect()],
    )
    await agent.prompt("hi")

    assert fired == []
```

- [ ] **Step 2: Run the tests**

```bash
cd /home/chris/cubepi && python -m pytest tests/middleware/test_on_run_end.py -v
```
Expected: all tests pass.

- [ ] **Step 3: Run full middleware test suite to check for regressions**

```bash
cd /home/chris/cubepi && python -m pytest tests/middleware/ -v
```
Expected: all tests pass.

---

## Task 5 — cubepi: Commit and push

- [ ] **Step 1: Commit**

```bash
cd /home/chris/cubepi && git add cubepi/middleware/base.py cubepi/agent/loop.py cubepi/agent/agent.py tests/middleware/test_on_run_end.py
git commit -m "feat(middleware): add on_run_end hook — fires once after full agent run"
```

- [ ] **Step 2: Push**

```bash
cd /home/chris/cubepi && git push
```

- [ ] **Step 3: Note the new commit SHA**

```bash
cd /home/chris/cubepi && git rev-parse HEAD
```
Save this SHA — it's needed for the pin bump in Task 6.

---

## Task 6 — cubeplex: Bump cubepi pin

**Files:**
- Modify: `pyproject.toml` (cubeplex backend)

- [ ] **Step 1: Update cubepi dependency to the new commit**

```bash
cd /home/chris/cubeplex/backend && uv add "cubepi @ git+https://github.com/xfgong/cubepi.git@<SHA-FROM-TASK-5>"
```
Replace `<SHA-FROM-TASK-5>` with the actual SHA.

- [ ] **Step 2: Verify the new cubepi is importable with the new hook**

```bash
cd /home/chris/cubeplex/backend && uv run python -c "from cubepi.middleware.base import Middleware; m = Middleware.__dict__; print('on_run_end' in m)"
```
Expected: `True`

---

## Task 7 — cubeplex: `REFLECTION_PROMPT`

**Files:**
- Create: `cubeplex/prompts/reflection.py`

- [ ] **Step 1: Write the prompt file**

```python
"""System prompt fragment for the end-of-run memory reflection turn."""

REFLECTION_PROMPT: str = (
    "This run is complete. Before we finish: briefly review what happened in "
    "this conversation turn. Did the user express a preference, correction, "
    "opinion, or important fact worth remembering?\n\n"
    "If yes: call memory_save or memory_update. Check memory_search first to "
    "avoid duplicating an existing item. Use scope=personal unless the user "
    "explicitly asked to share with their team.\n\n"
    "If nothing is worth saving, reply with \"done\" and stop."
)
```

- [ ] **Step 2: Verify import**

```bash
cd /home/chris/cubeplex/backend && uv run python -c "from cubeplex.prompts.reflection import REFLECTION_PROMPT; print(len(REFLECTION_PROMPT), 'chars')"
```
Expected: prints a non-zero char count.

---

## Task 8 — cubeplex: `ReflectionMiddleware`

**Files:**
- Create: `cubeplex/middleware/reflection.py`

- [ ] **Step 1: Write the middleware**

```python
"""ReflectionMiddleware — end-of-run memory self-review."""

from __future__ import annotations

import time
from typing import Any

from cubepi.middleware.base import Middleware
from cubepi.providers.base import Message, TextContent, UserMessage

from cubeplex.prompts.reflection import REFLECTION_PROMPT


class ReflectionMiddleware(Middleware):
    """Injects a memory-review prompt after every agent run completes.

    Uses the cubepi on_run_end hook so the reflection executes as an extra
    turn within the same run — same agent instance, same context, same tools.
    The injected UserMessage is tagged is_reflection=True so the frontend can
    render it differently in a future iteration.
    """

    async def on_run_end(
        self, ctx: Any, *, signal: Any = None
    ) -> list[Message] | None:
        return [
            UserMessage(
                content=[TextContent(text=REFLECTION_PROMPT)],
                timestamp=time.time(),
                metadata={"is_reflection": True},
            )
        ]
```

- [ ] **Step 2: Verify import**

```bash
cd /home/chris/cubeplex/backend && uv run python -c "from cubeplex.middleware.reflection import ReflectionMiddleware; print('ok')"
```
Expected: `ok`

---

## Task 9 — cubeplex: Wire `ReflectionMiddleware` into run_manager

**Files:**
- Modify: `cubeplex/streams/run_manager.py`

- [ ] **Step 1: Append `ReflectionMiddleware` as entry #12**

After the `TodoListMiddleware` block (the `# 11. TodoListMiddleware` comment section, which ends around line 1676), add:

```python
        # 12. ReflectionMiddleware — memory self-review at end of every run
        try:
            from cubeplex.middleware.reflection import ReflectionMiddleware

            cubepi_middleware.append(ReflectionMiddleware())
        except Exception as _exc:
            logger.warning("ReflectionMiddleware unavailable: {}", _exc)
```

- [ ] **Step 2: Verify the import resolves**

```bash
cd /home/chris/cubeplex/backend && uv run python -c "from cubeplex.middleware.reflection import ReflectionMiddleware; print('ok')"
```
Expected: `ok`

---

## Task 10 — cubeplex: Unit tests for `ReflectionMiddleware`

**Files:**
- Create: `tests/unit/test_reflection_middleware.py`

- [ ] **Step 1: Write the test file**

```python
"""Unit tests for ReflectionMiddleware."""
from __future__ import annotations

import pytest

from cubepi.agent.types import AgentContext
from cubepi.providers.base import UserMessage

from cubeplex.middleware.reflection import ReflectionMiddleware
from cubeplex.prompts.reflection import REFLECTION_PROMPT


def _mk_ctx() -> AgentContext:
    return AgentContext(system_prompt="", messages=[])


@pytest.mark.asyncio
async def test_on_run_end_returns_user_message() -> None:
    mw = ReflectionMiddleware()
    result = await mw.on_run_end(_mk_ctx())
    assert result is not None
    assert len(result) == 1
    assert isinstance(result[0], UserMessage)


@pytest.mark.asyncio
async def test_on_run_end_content_is_reflection_prompt() -> None:
    mw = ReflectionMiddleware()
    result = await mw.on_run_end(_mk_ctx())
    assert result is not None
    msg = result[0]
    assert isinstance(msg, UserMessage)
    text = msg.content[0].text  # type: ignore[attr-defined]
    assert text == REFLECTION_PROMPT


@pytest.mark.asyncio
async def test_on_run_end_metadata_is_reflection() -> None:
    mw = ReflectionMiddleware()
    result = await mw.on_run_end(_mk_ctx())
    assert result is not None
    msg = result[0]
    assert isinstance(msg, UserMessage)
    assert msg.metadata.get("is_reflection") is True
```

- [ ] **Step 2: Run unit tests**

```bash
cd /home/chris/cubeplex/backend && uv run pytest tests/unit/test_reflection_middleware.py -v
```
Expected: all 3 tests pass.

- [ ] **Step 3: Run full unit suite**

```bash
cd /home/chris/cubeplex/backend && uv run pytest tests/unit/ -v
```
Expected: no regressions.

---

## Task 11 — cubeplex: Commit

- [ ] **Step 1: Run mypy on changed files**

```bash
cd /home/chris/cubeplex/backend && uv run mypy cubeplex/prompts/reflection.py cubeplex/middleware/reflection.py cubeplex/streams/run_manager.py
```
Expected: no errors.

- [ ] **Step 2: Commit**

```bash
cd /home/chris/cubeplex/backend && git add cubeplex/prompts/reflection.py cubeplex/middleware/reflection.py cubeplex/streams/run_manager.py tests/unit/test_reflection_middleware.py pyproject.toml uv.lock
git commit -m "feat(memory): add ReflectionMiddleware — end-of-run memory self-review via cubepi on_run_end hook"
```
