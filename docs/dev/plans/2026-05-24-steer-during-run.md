# Steer During Run Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user type and send a message *while* an agent run is streaming; the message is injected into the live run as a steering message (the model picks it up at the next safe point) rather than starting a new turn.

**Architecture:** cubepi's `Agent` exposes `steer(message)`, and the loop drains steering messages at loop start and after each tool batch *when more tool calls remain*. That last condition leaves a gap: a steer that arrives while the model is finishing a turn with **no** further tool calls (a final text answer, or a tool-less run) is never drained and is silently dropped. So **cubepi needs one small upstream change** (Task 0) to also drain steering at the turn boundary and continue. The rest is plumbing: cubeplex holds no handle to the live `Agent`, so `RunManager` gains a `run_id → Agent` registry (mirroring how it tracks `run_id → asyncio.Task` for cancel). A new `POST /conversations/{id}/steer` endpoint finds the active run's agent and calls `agent.steer(...)`. The frontend enables the textarea during streaming and, when it has text, turns the Stop button into a Send button that calls a new `steer()` store action which optimistically appends the user message and hits the endpoint.

**Cross-repo ordering:** Task 0 ships in `~/cubepi` as its own PR (codex loop), then cubeplex bumps the `cubepi` pin in `backend/pyproject.toml` to the merged SHA (Task 0b) before the cubeplex feature can rely on tail-steer. The cubeplex endpoint/registry (Tasks 1-2) work against today's cubepi too — they just won't honor tail steers until the bump lands.

**Tech Stack:** FastAPI + cubepi runtime (backend), Zustand + React 19 + Next.js (frontend), pytest + vitest.

**Deployment assumption:** single uvicorn process (see `backend/main.py` — no `workers`). The live `Agent` lives in the same process that runs the background task, exactly like the existing cancel path (`RunManager._tasks`). Multi-worker steer (Redis pub/sub fan-out) is explicitly **out of scope** and inherits the same limitation cancel already has.

**Steer semantics (locked):** always steer (no steer/follow-up/abort choice), no confirmation dialog, no immediate interrupt. A steered message takes effect at cubepi's next safe point.

---

## File Structure

**cubepi (separate repo `~/cubepi`)**
- Modify `cubepi/agent/loop.py` — drain steering at the turn boundary (not only when more tool calls remain).
- Test `tests/agent/test_steering.py` (or existing steering test) — a steer drained at the tail re-invokes the model.

**Backend (cubeplex)**
- Modify `backend/pyproject.toml` — bump `cubepi` pin to the merged Task 0 SHA.
- Modify `backend/cubeplex/streams/run_manager.py` — add `self._agents: dict[str, Any]` registry; register the live `Agent` in `_run_cubepi_path`; add `RunManager.steer_run(run_id, content)`.
- Modify `backend/cubeplex/api/routes/v1/conversations.py` — add `SteerMessageRequest` model + `POST /{conversation_id}/steer` handler (mirrors `cancel_active_run`).
- Test `backend/tests/unit/test_run_manager_steer.py` — registry + `steer_run` against a fake agent.
- Test `backend/tests/e2e/test_steer_endpoint.py` — endpoint finds active run and steers (real run, blocking tool).

**Frontend**
- Modify `frontend/packages/core/src/api/stream.ts` — add `steerRun(client, conversationId, content)`.
- Modify `frontend/packages/core/src/stores/messageStore.ts` — add `steer(client, conversationId, content)` action to the `MessageStore` interface + implementation.
- Modify `frontend/packages/web/components/layout/InputBar.tsx` — enable textarea during streaming; 3-state action button (Send / Steer / Stop); route Enter to steer when streaming with text.
- Test `frontend/packages/core/__tests__/stores/messageStoreSteer.test.ts` — `steer` optimistically appends + calls `steerRun`, leaves streaming state untouched.

---

## Task 0: cubepi — drain steering at the turn boundary

**Repo:** `~/cubepi` (NOT the cubeplex worktree). Branch off `origin/main`:
`cd ~/cubepi && git fetch origin && git checkout -b fix/steer-at-turn-boundary origin/main`

**Files:**
- Modify: `cubepi/agent/loop.py`
- Test: `cubepi/tests/agent/test_steering.py` (create if absent; otherwise add to the existing steering test module — check `ls ~/cubepi/tests/agent/`)

- [ ] **Step 1: Write the failing test**

A steer enqueued before a tool-less run must be honored at the turn boundary (the model is re-invoked with the steered message). Using the faux provider, set up: turn 1 = plain text (no tools), then turn 2 = plain text. Enqueue a steer; assert it lands in history and the model ran a second time.

```python
import pytest

from cubepi.agent.agent import Agent
from cubepi.checkpointer.memory import MemoryCheckpointer
from cubepi.providers.base import Model, TextContent, UserMessage
from cubepi.providers.faux import FauxProvider, faux_assistant_message


def make_model() -> Model:
    return Model(id="faux-1", provider="faux")


@pytest.mark.asyncio
async def test_steer_drained_at_turn_boundary_without_tool_calls():
    provider = FauxProvider()
    # Turn 1: plain text answer (no tool calls). Turn 2: plain text after steer.
    provider.set_responses(
        [
            faux_assistant_message("first answer"),
            faux_assistant_message("acknowledged the steer"),
        ]
    )
    agent = Agent(provider=provider, model=make_model())

    # Enqueue a steer before running; with no tool calls, the only chance to
    # drain it is the turn boundary.
    agent.steer(UserMessage(content=[TextContent(text="actually do X instead")]))
    await agent.prompt("start")

    roles = [m.role for m in agent.state.messages]
    # user(start) → assistant(first) → user(steer) → assistant(ack)
    assert roles == ["user", "assistant", "user", "assistant"]
    assert any(
        getattr(b, "text", "") == "actually do X instead"
        for m in agent.state.messages
        if m.role == "user"
        for b in m.content
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/cubepi && uv run pytest tests/agent/test_steering.py -v`
Expected: FAIL — only 2 messages (`user`, `assistant`); the steer was dropped because the tail only drains follow-ups.

- [ ] **Step 3: Add the tail steering drain**

In `cubepi/agent/loop.py`, in `run_agent_loop` (and `run_agent_loop_continue` if it has the same tail — check both), after the inner `while has_more_tool_calls:` loop ends and **before** the `if get_follow_up_messages:` block (currently ~line 330), insert:

```python
        # The in-loop drain only fires when more tool calls remain, so a steer
        # that arrives while the model is finishing a tool-less turn would be
        # dropped. Drain it here and re-invoke the model so "steer anytime"
        # works during a final text turn too.
        if get_steering_messages:
            steering = await get_steering_messages() or []
            if steering:
                for msg in steering:
                    await emit_event(emit, MessageStartEvent(message=msg))
                    await emit_event(emit, MessageEndEvent(message=msg))
                    current_context.messages.append(msg)
                    new_messages.append(msg)
                first_turn = False
                continue
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/cubepi && uv run pytest tests/agent/test_steering.py -v`
Expected: PASS — 4 messages in the documented order.

- [ ] **Step 5: Full agent suite + lint**

Run: `cd ~/cubepi && uv run pytest tests/agent -q && uv run ruff format cubepi/agent/loop.py tests/agent/test_steering.py && uv run ruff check cubepi/agent/loop.py tests/agent/test_steering.py`
Expected: all pass; ruff clean.

- [ ] **Step 6: Commit, push, PR, run codex loop**

```bash
cd ~/cubepi
git add cubepi/agent/loop.py tests/agent/test_steering.py
git commit -m "fix(loop): drain steering at the turn boundary so tail steers aren't dropped"
git push -u origin fix/steer-at-turn-boundary
gh pr create --base main --title "fix(loop): drain steering at turn boundary" --body "Steering messages were only drained mid-tool-loop; a steer arriving during a final/tool-less turn was silently dropped. Drain at the turn boundary and continue."
```

Then run `/pr-codex-review-loop` for the cubepi PR until clean, and merge. **Record the merged squash SHA on main** — Task 0b needs it.

---

## Task 0b: cubeplex — bump cubepi pin to the Task 0 SHA

**Files:**
- Modify: `backend/pyproject.toml`, `backend/uv.lock`

- [ ] **Step 1: Update the pin**

In `backend/pyproject.toml` under `[tool.uv.sources]`, set the `cubepi` `rev` to the merged Task 0 SHA and refresh the comment to mention the turn-boundary steer fix.

- [ ] **Step 2: Re-lock + sync**

Run: `cd backend && uv lock && uv sync --all-extras`
Expected: `Updated cubepi ... -> <new sha>`.

- [ ] **Step 3: Verify alembic tree still loads (catch import-time regressions)**

Run: `cd backend && uv run pytest tests/unit/test_alembic_head.py -q`
Expected: PASS (single head).

- [ ] **Step 4: Commit**

```bash
git add backend/pyproject.toml backend/uv.lock
git commit -m "chore(deps): bump cubepi pin for turn-boundary steer fix"
```

---

## Task 1: Backend — RunManager agent registry + `steer_run`

**Files:**
- Modify: `backend/cubeplex/streams/run_manager.py`
- Test: `backend/tests/unit/test_run_manager_steer.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_run_manager_steer.py`:

```python
"""Unit tests for RunManager's live-agent registry + steer_run."""

import pytest

from cubeplex.streams.run_manager import RunManager


class _FakeAgent:
    def __init__(self) -> None:
        self.steered: list[str] = []

    def steer(self, message) -> None:  # noqa: ANN001 - cubepi Message
        # Record the text of the first content block.
        self.steered.append(message.content[0].text)


def _make_manager() -> RunManager:
    # Construct without touching Redis/app: registry + steer_run don't need them.
    return RunManager.__new__(RunManager)  # type: ignore[call-arg]


@pytest.mark.asyncio
async def test_steer_run_calls_agent_steer_for_registered_run() -> None:
    mgr = _make_manager()
    mgr._agents = {}
    agent = _FakeAgent()
    mgr._agents["run-1"] = agent

    steered = await mgr.steer_run("run-1", "go left instead")

    assert steered is True
    assert agent.steered == ["go left instead"]


@pytest.mark.asyncio
async def test_steer_run_returns_false_when_no_agent() -> None:
    mgr = _make_manager()
    mgr._agents = {}

    steered = await mgr.steer_run("missing", "hello")

    assert steered is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/unit/test_run_manager_steer.py -v`
Expected: FAIL — `AttributeError: 'RunManager' object has no attribute '_agents'` / `steer_run`.

- [ ] **Step 3: Add the registry field in `RunManager.__init__`**

In `backend/cubeplex/streams/run_manager.py`, inside `RunManager.__init__` (right after `self._tasks: dict[str, asyncio.Task[None]] = {}`), add:

```python
        self._agents: dict[str, Any] = {}
```

- [ ] **Step 4: Add `steer_run` method**

In `RunManager`, add this method next to `cancel_run`:

```python
    async def steer_run(self, run_id: str, content: str) -> bool:
        """Inject a steering message into a live run's agent.

        Returns False when the run has no live agent in this process (already
        finished, or running in a different worker — the same single-process
        limitation as cancel_run). The agent's loop drains the message at its
        next safe point; we do not block on delivery.
        """
        agent = self._agents.get(run_id)
        if agent is None:
            return False

        from cubepi.providers.base import TextContent, UserMessage

        agent.steer(UserMessage(content=[TextContent(text=content)]))
        return True
```

- [ ] **Step 5: Register / unregister the live agent in `_run_cubepi_path`**

In `_run_cubepi_path`, the agent is created via `create_cubeplex_agent(...)` and then `agent.subscribe(_on_event)`. Immediately after `agent.subscribe(_on_event)` register it; wrap the prompt/teardown so it's always removed.

Find this block:

```python
            agent.subscribe(_on_event)
            drainer = asyncio.create_task(_drain_cubepi_sse_queue(sse_queue, publish_stream_event))
```

Change to:

```python
            agent.subscribe(_on_event)
            self._agents[run_id] = agent
            drainer = asyncio.create_task(_drain_cubepi_sse_queue(sse_queue, publish_stream_event))
```

Then locate the existing `finally:` that drains the sse_queue (the one containing `await sse_queue.put(None)` / `await drainer`) and add the deregistration there so it runs on success, error, and cancel:

```python
            finally:
                # Stop accepting steers for this run before tearing down.
                self._agents.pop(run_id, None)
                # Signal drainer and wait for it to flush remaining events so
                # all SSE dicts are published before citation buffers flush.
                await sse_queue.put(None)
                await drainer
```

- [ ] **Step 6: Defensive cleanup in `_execute_run` finally**

`_run_cubepi_path` might raise before reaching its own `finally` (e.g. agent never created). Add a belt-and-suspenders pop in `_execute_run`'s outer `finally:` block, right after `if stream_task is not None ...` handling (anywhere in that finally is fine):

```python
            self._agents.pop(run_id, None)
```

- [ ] **Step 7: Run tests + typecheck**

Run: `cd backend && uv run pytest tests/unit/test_run_manager_steer.py -v && uv run mypy cubeplex/streams/run_manager.py`
Expected: 2 passed; mypy `Success`.

- [ ] **Step 8: Commit**

```bash
git add backend/cubeplex/streams/run_manager.py backend/tests/unit/test_run_manager_steer.py
git commit -m "feat(runs): add live-agent registry + steer_run to RunManager"
```

---

## Task 2: Backend — steer endpoint

**Files:**
- Modify: `backend/cubeplex/api/routes/v1/conversations.py`
- Test: `backend/tests/e2e/test_steer_endpoint.py`

- [ ] **Step 1: Add the request model + handler**

In `backend/cubeplex/api/routes/v1/conversations.py`, add a request model near `SendMessageRequest`:

```python
class SteerMessageRequest(BaseModel):
    """Request body for steering an in-flight run."""

    content: str
```

Then add this handler next to `cancel_active_run` (mirror its structure exactly):

```python
@router.post("/{conversation_id}/steer", status_code=status.HTTP_202_ACCEPTED)
async def steer_active_run(
    conversation_id: str,
    body: SteerMessageRequest,
    raw_request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
    rds: Annotated[RedisHandle, Depends(redis_dep)],
) -> dict[str, object]:
    """Inject a steering message into the conversation's active run, if any."""
    if not body.content.strip():
        raise InvalidInputError(
            message="Steering message must not be empty",
            details="Provide non-empty content to steer the run",
        )

    conv_repo = ConversationRepository(
        session,
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user.id,
    )
    conversation = await conv_repo.get_by_id(conversation_id)
    if not conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation {conversation_id} not found",
        )

    active_run = await get_active_run(
        rds.client,
        prefix=rds.key_prefix,
        conversation_id=conversation_id,
    )
    if active_run is None or active_run.status != "running":
        return {"steered": False, "run_id": None}

    run_manager = raw_request.app.state.run_manager
    steered = await run_manager.steer_run(active_run.run_id, body.content)
    return {"steered": steered, "run_id": active_run.run_id}
```

(`InvalidInputError` is already imported at the top of this file; `get_active_run`, `RedisHandle`, `redis_dep`, `RequestContext`, `require_member` are all already imported.)

- [ ] **Step 2: Write the E2E test**

Create `backend/tests/e2e/test_steer_endpoint.py`. This drives a real run that calls a sandbox tool, steers mid-run, and asserts the steered text lands in checkpointer history. Because steer delivery is timing-dependent, the test starts the run in the background, polls until the active run exists, steers, then consumes the stream to completion and inspects history.

```python
"""E2E: steering an in-flight run injects a user message that reaches history."""

import asyncio

import pytest

from tests.e2e.conftest import collect_sse_events

pytestmark = pytest.mark.real_llm


@pytest.mark.asyncio
async def test_steer_injects_user_message_into_active_run(member_client) -> None:
    client, ws_id = member_client

    resp = await client.post(
        f"/api/v1/ws/{ws_id}/conversations", params={"title": "steer-e2e"}
    )
    resp.raise_for_status()
    conv_id = resp.json()["id"]

    # Start a run that will take a few seconds (ask it to use the sandbox),
    # consuming the SSE stream in a background task.
    async def _run() -> list[dict]:
        return await collect_sse_events(
            client,
            f"/api/v1/ws/{ws_id}/conversations/{conv_id}/messages",
            json_data={
                "content": "Run a shell command that sleeps for 3 seconds, "
                "then tell me the current directory."
            },
        )

    run_task = asyncio.create_task(_run())

    # Poll bootstrap until the run is active, then steer.
    steered = False
    for _ in range(50):
        b = await client.get(
            f"/api/v1/ws/{ws_id}/conversations/{conv_id}/bootstrap"
        )
        if b.json().get("active_run"):
            s = await client.post(
                f"/api/v1/ws/{ws_id}/conversations/{conv_id}/steer",
                json={"content": "STEER_MARKER_42: also print 'hello from steer'"},
            )
            assert s.status_code == 202
            steered = s.json()["steered"]
            break
        await asyncio.sleep(0.1)
    assert steered is True, "run never became active / agent not registered"

    await run_task

    # The steered message must be persisted as a user message in history.
    resp = await client.get(f"/api/v1/ws/{ws_id}/conversations/{conv_id}/messages")
    resp.raise_for_status()
    messages = resp.json()["messages"]
    user_texts = [
        block.get("text", "")
        for m in messages
        if m.get("role") == "user"
        for block in m.get("content", [])
    ]
    assert any("STEER_MARKER_42" in t for t in user_texts), (
        f"steered message not found in history user turns: {user_texts!r}"
    )
```

- [ ] **Step 3: Verify the endpoint with the unit-level run + lint/type**

Run: `cd backend && uv run ruff check cubeplex/api/routes/v1/conversations.py && uv run mypy cubeplex/api/routes/v1/conversations.py`
Expected: All checks pass; mypy `Success`.

(The E2E in Step 2 needs a real LLM + sandbox; run it in the worktree once `.env` and `config.development.local.yaml` are copied in — see Task 6.)

- [ ] **Step 4: Commit**

```bash
git add backend/cubeplex/api/routes/v1/conversations.py backend/tests/e2e/test_steer_endpoint.py
git commit -m "feat(api): add POST /conversations/{id}/steer endpoint"
```

---

## Task 3: Frontend — `steerRun` API client

**Files:**
- Modify: `frontend/packages/core/src/api/stream.ts`

- [ ] **Step 1: Add the API function**

In `frontend/packages/core/src/api/stream.ts`, next to `cancelActiveRun`, add:

```typescript
export interface SteerRunResponse {
  steered: boolean
  run_id: string | null
}

export async function steerRun(
  client: ApiClient,
  conversationId: string,
  content: string,
): Promise<SteerRunResponse> {
  const res = await client.post(`/api/v1/conversations/${conversationId}/steer`, { content })
  if (!res.ok) {
    throw new Error(`Failed to steer run: HTTP ${res.status}`)
  }
  return (await res.json()) as SteerRunResponse
}
```

(`steerRun` is exported through the barrel automatically via `export * from './stream'` in `frontend/packages/core/src/api/index.ts`.)

- [ ] **Step 2: Build core to typecheck**

Run: `cd frontend && pnpm --filter @cubeplex/core build`
Expected: `tsc` exits cleanly.

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/core/src/api/stream.ts
git commit -m "feat(api): add steerRun client for in-flight run steering"
```

---

## Task 4: Frontend — `steer` store action

**Files:**
- Modify: `frontend/packages/core/src/stores/messageStore.ts`
- Test: `frontend/packages/core/__tests__/stores/messageStoreSteer.test.ts`

- [ ] **Step 1: Write the failing test**

Create `frontend/packages/core/__tests__/stores/messageStoreSteer.test.ts`:

```typescript
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { useMessageStore } from '../../src/stores/messageStore'

vi.mock('../../src/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api')>()
  return {
    ...actual,
    steerRun: vi.fn().mockResolvedValue({ steered: true, run_id: 'r1' }),
  }
})

import { steerRun } from '../../src/api'

const fakeClient = { resolvePath: (s: string) => s, post: vi.fn() } as never

describe('messageStore.steer', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useMessageStore.setState({
      messages: { conv1: [] },
      streamAgents: { main: { text: 'partial', toolCalls: [], toolResults: [], thinking: '', blocks: [], name: null } },
      isStreaming: true,
      streamingConversationId: 'conv1',
      currentRunId: 'r1',
    })
  })

  it('optimistically appends the user message and calls steerRun', async () => {
    await useMessageStore.getState().steer(fakeClient, 'conv1', 'go left instead')

    const state = useMessageStore.getState()
    // Streaming state is untouched — the run keeps going.
    expect(state.isStreaming).toBe(true)
    expect(state.streamingConversationId).toBe('conv1')
    const msgs = state.messages.conv1
    expect(msgs).toHaveLength(1)
    expect(msgs[0].role).toBe('user')
    expect(msgs[0].content).toEqual([{ type: 'text', text: 'go left instead' }])
    expect(steerRun).toHaveBeenCalledWith(fakeClient, 'conv1', 'go left instead')
  })

  it('is a no-op for empty content', async () => {
    await useMessageStore.getState().steer(fakeClient, 'conv1', '   ')
    expect(steerRun).not.toHaveBeenCalled()
    expect(useMessageStore.getState().messages.conv1).toHaveLength(0)
  })

  it('does nothing when not streaming the given conversation', async () => {
    useMessageStore.setState({ isStreaming: false, streamingConversationId: null })
    await useMessageStore.getState().steer(fakeClient, 'conv1', 'hi')
    expect(steerRun).not.toHaveBeenCalled()
  })

  it('rolls back the optimistic bubble when the run was not steered', async () => {
    ;(steerRun as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      steered: false,
      run_id: null,
    })
    await useMessageStore.getState().steer(fakeClient, 'conv1', 'too late')
    expect(useMessageStore.getState().messages.conv1).toHaveLength(0)
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && pnpm --filter @cubeplex/core test -- messageStoreSteer`
Expected: FAIL — `steer is not a function`.

- [ ] **Step 3: Add `steer` to the `MessageStore` interface**

In `frontend/packages/core/src/stores/messageStore.ts`, in the `MessageStore` interface, next to `cancelStream`, add:

```typescript
  steer(client: ApiClient, conversationId: string, content: string): Promise<void>
```

- [ ] **Step 4: Update the `steerRun` import**

The store already imports from `../api`. Add `steerRun` to that import line:

```typescript
import { cancelActiveRun, getConversationBootstrap, steerRun, streamMessages, streamRun } from '../api'
```

- [ ] **Step 5: Implement the `steer` action**

In the store object, add the action next to `cancelStream`:

```typescript
  async steer(client, conversationId, content) {
    const text = content.trim()
    if (!text) return
    const state = get()
    if (!state.isStreaming || state.streamingConversationId !== conversationId) return

    // Optimistically show the steered user message immediately for responsive
    // feedback. cubepi emits the injected message's MessageStart/End to the
    // checkpointer (not as SSE deltas), so this in-memory bubble is the live
    // display; a reload after the run completes re-reads it from history.
    // Streaming state is deliberately untouched — the run keeps going and the
    // model picks the message up at its next safe point.
    //
    // If the endpoint reports the run was NOT steered (already finished /
    // not in this process), roll the optimistic bubble back so we don't leave
    // a user message that never reaches the checkpointer.
    const optimisticId = nextMessageId('user-steer')
    const userMessage: UserMessageType = {
      id: optimisticId,
      role: 'user',
      content: [{ type: 'text', text }],
      timestamp: Date.now() / 1000,
      metadata: {},
    }
    set((s) => ({
      messages: {
        ...s.messages,
        [conversationId]: [...(s.messages[conversationId] ?? []), userMessage],
      },
    }))

    const rollback = () =>
      set((s) => ({
        messages: {
          ...s.messages,
          [conversationId]: (s.messages[conversationId] ?? []).filter(
            (m) => m.id !== optimisticId,
          ),
        },
      }))

    try {
      const res = await steerRun(client, conversationId, text)
      if (!res.steered) rollback()
    } catch (err) {
      console.error('Failed to steer run:', err)
      rollback()
    }
  },
```

(`UserMessageType` and `nextMessageId` already exist in this file.)

> **Known v1 limitation (document, don't fix here):** the optimistic bubble renders *after* the live merged assistant block (the live view renders all `messages` then the single growing `streamAgents` block — `MessageList.tsx`), so a mid-run steer shows below the still-streaming assistant text rather than inline. It lands in correct order after the run completes and history is re-read. Splitting the live assistant block at steer boundaries is out of scope for v1.

- [ ] **Step 6: Run test to verify it passes**

Run: `cd frontend && pnpm --filter @cubeplex/core test -- messageStoreSteer`
Expected: 4 passed.

- [ ] **Step 7: Build core**

Run: `cd frontend && pnpm --filter @cubeplex/core build`
Expected: `tsc` clean.

- [ ] **Step 8: Commit**

```bash
git add frontend/packages/core/src/stores/messageStore.ts frontend/packages/core/__tests__/stores/messageStoreSteer.test.ts
git commit -m "feat(store): add steer action that injects into the live run"
```

---

## Task 4b: Frontend — don't duplicate the original user turn on reload-during-steer

**Why:** On reload mid-run, `loadMessages` calls `trimHistoryForActiveRun` to cut history at the active run's original user message (so the SSE replay doesn't double-render). It scans backward and **breaks on the first user message it sees**. Once a steer is checkpointed, the last user message in history is the *steer*, not the original — so the scan breaks, the original isn't matched, and the function appends a **duplicate** pending original user message (`messageStore.ts` `trimHistoryForActiveRun`, ~lines 244-262). Fix: keep scanning back to find the run's original user message; only append the pending placeholder when the original truly isn't in history yet.

**Files:**
- Modify: `frontend/packages/core/src/stores/messageStore.ts`
- Test: `frontend/packages/core/__tests__/stores/messageStoreTrim.test.ts`

- [ ] **Step 1: Write the failing test**

Create `frontend/packages/core/__tests__/stores/messageStoreTrim.test.ts`:

```typescript
import { describe, expect, it } from 'vitest'
import { trimHistoryForActiveRun } from '../../src/stores/messageStore'
import type { Message } from '../../src/types'

function user(text: string, ts: number): Message {
  return { id: `u-${text}`, role: 'user', content: [{ type: 'text', text }], timestamp: ts, metadata: {} } as Message
}
function assistant(text: string, ts: number): Message {
  return { id: `a-${text}`, role: 'assistant', content: [{ type: 'text', text }], stop_reason: 'stop', timestamp: ts, metadata: {} } as Message
}

describe('trimHistoryForActiveRun with steers', () => {
  it('does not append a duplicate original when a steer follows it in history', () => {
    // started_at = 1000ms → started_at seconds 1.0; messages use epoch seconds.
    const history: Message[] = [
      user('original', 1.0),
      assistant('partial', 1.1),
      user('steer one', 1.2), // checkpointed steer, newer than original
    ]
    const result = trimHistoryForActiveRun(history, 'run-1', 'original', '1970-01-01T00:00:01.000Z')
    // Original is present → no pending duplicate appended.
    const originals = result.filter((m) => m.role === 'user' && (m.content[0] as { text: string }).text === 'original')
    expect(originals).toHaveLength(1)
    // No synthesized pending-<runId> placeholder.
    expect(result.some((m) => m.id === 'pending-run-1')).toBe(false)
  })

  it('still appends a pending original when history has no matching user turn', () => {
    const history: Message[] = [user('different', 1.0)]
    const result = trimHistoryForActiveRun(history, 'run-1', 'original', '1970-01-01T00:00:02.000Z')
    expect(result.some((m) => m.id === 'pending-run-1')).toBe(true)
  })
})
```

- [ ] **Step 2: Export `trimHistoryForActiveRun` + run test to verify it fails**

`trimHistoryForActiveRun` is currently a module-private function. Add `export` to its declaration so the test can import it. Then:

Run: `cd frontend && pnpm --filter @cubeplex/core test -- messageStoreTrim`
Expected: FAIL — first test finds a `pending-run-1` duplicate.

- [ ] **Step 3: Fix the scan to find the original, not just the last user turn**

Replace the body of `trimHistoryForActiveRun`:

```typescript
export function trimHistoryForActiveRun(
  messages: Message[],
  runId: string,
  content: string,
  startedAt: string | null,
): Message[] {
  const startedAtMs = startedAt ? Date.parse(startedAt) : NaN
  // Find the run's ORIGINAL user message: the last user turn matching `content`
  // at-or-after the run start. Skipping non-matching newer user turns is what
  // lets checkpointed steer messages (which appear after the original) not
  // trigger a duplicate placeholder.
  for (let i = messages.length - 1; i >= 0; i--) {
    const msg = messages[i]
    if (msg.role !== 'user') continue
    const msgMs = msg.timestamp != null ? msg.timestamp * 1000 : NaN
    if (Number.isFinite(startedAtMs) && Number.isFinite(msgMs) && msgMs < startedAtMs) {
      break
    }
    if (getTextContent(msg) === content) return messages.slice(0, i + 1)
    // Not the original (e.g. a steer turn) — keep scanning back.
  }
  return [...messages, buildPendingUserMessage(runId, content)]
}
```

(The only behavioral change vs. the original is the trailing `// keep scanning back` — the original `break` after the content mismatch is removed, and the loop now continues instead of bailing on the first non-matching user turn.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && pnpm --filter @cubeplex/core test -- messageStoreTrim`
Expected: 2 passed.

- [ ] **Step 5: Run the full core suite (guard the no-steer path)**

Run: `cd frontend && pnpm --filter @cubeplex/core test`
Expected: all green — confirms the existing single-user-turn trim behavior is unchanged.

> **Known v1 limitation:** when the original is found, history is trimmed through it and the SSE replay reconstructs the rest — but checkpointed steer turns (which are NOT in the Redis event stream) are sliced away, so a steer entered before a mid-run reload won't show until the run completes and history is re-read clean. Acceptable for v1; no data loss.

- [ ] **Step 6: Commit**

```bash
git add frontend/packages/core/src/stores/messageStore.ts frontend/packages/core/__tests__/stores/messageStoreTrim.test.ts
git commit -m "fix(store): don't duplicate original user turn on reload after a steer"
```

---

## Task 5: Frontend — InputBar 3-state button + mid-run typing

**Files:**
- Modify: `frontend/packages/web/components/layout/InputBar.tsx`

**Behavior:** During streaming, the textarea is editable. If it has text → the action button is a **Send** (steer) button; if empty → it stays the **Stop** button. When not streaming → normal **Send**. Enter routes to steer when streaming-with-text, else to submit.

- [ ] **Step 1: Pull the `steer` action and stop disabling the textarea during streaming**

In `InputBar.tsx`, add the `steer` selector next to the existing `cancelStream`/`send` selectors:

```typescript
  const steer = useMessageStore((s) => s.steer)
```

Change `isSubmitting` so streaming no longer disables the textarea (it still blocks the *initial* send path which checks `isSubmitting`):

Find:

```typescript
  const isSubmitting = isLoading || messageIsStreaming || isHandlingSubmit
```

Replace with:

```typescript
  // Streaming no longer locks the textarea — the user can type to steer.
  // handleSubmit still guards against starting a *new* turn mid-stream via
  // `messageIsStreaming` directly (see handleSubmit).
  const isSubmitting = isLoading || isHandlingSubmit
  const hasText = content.trim().length > 0
```

Because `canAttach` is defined as `Boolean(conversationId || onSubmit) && !isSubmitting` and steering carries **text only**, dropping streaming from `isSubmitting` would wrongly enable attachment uploads during a run. Keep attachments disabled while streaming — find:

```typescript
  const canAttach = Boolean(conversationId || onSubmit) && !isSubmitting
```

Replace with:

```typescript
  // Steering is text-only; don't allow new attachment uploads mid-run.
  const canAttach = Boolean(conversationId || onSubmit) && !isSubmitting && !messageIsStreaming
```

Update `handleSubmit`'s guard (it previously relied on `isSubmitting` including streaming) to explicitly block starting a new turn while streaming:

Find the first line of `handleSubmit`:

```typescript
    if (isSubmitting || uploadInFlight || (!content.trim() && stagedFileCount === 0)) return
```

Replace with:

```typescript
    if (isSubmitting || messageIsStreaming || uploadInFlight || (!content.trim() && stagedFileCount === 0))
      return
```

- [ ] **Step 2: Add a `handleSteer` and route Enter**

Add next to `handleCancel`:

```typescript
  const handleSteer = async (): Promise<void> => {
    if (!conversationId || !hasText) return
    const client = createApiClient('')
    if (workspaceId) client.setWorkspaceId(workspaceId)
    const text = content
    setContent('')
    resetTextareaHeight()
    await steer(client, conversationId, text)
  }
```

Update `handleKeyDown` so Enter steers when streaming-with-text:

Find:

```typescript
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      void handleSubmit()
    }
```

Replace with:

```typescript
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      if (messageIsStreaming && hasText) {
        void handleSteer()
      } else {
        void handleSubmit()
      }
    }
```

- [ ] **Step 3: Make the action button 3-state**

The current render has `canCancel ? <Stop/> : <Send/>`. Change the condition so a streaming run with text shows Send (steer); streaming with no text shows Stop; not streaming shows Send (submit).

Find:

```typescript
  const canCancel = messageIsStreaming && Boolean(conversationId)
```

Replace with:

```typescript
  // Show Stop only while streaming AND the box is empty; once the user types,
  // the button becomes Send (which steers the live run).
  const showStop = messageIsStreaming && Boolean(conversationId) && !hasText
```

Find the button block `{canCancel ? ( ... Stop button ... ) : ( ... Send button ... )}` and:
- change `canCancel ?` to `showStop ?`
- change the Send button's `onClick` to dispatch steer when streaming:

The Send button's `onClick={() => void handleSubmit()}` becomes:

```typescript
          onClick={() => void (messageIsStreaming ? handleSteer() : handleSubmit())}
```

Leave the Send button's `disabled` logic as-is but ensure it is enabled when `messageIsStreaming && hasText` (it keys off content/attachments today; confirm a streaming+text state renders it enabled — if the existing `disabled` expression references `isSubmitting`, it no longer includes streaming after Step 1, so it will be enabled).

- [ ] **Step 4: Manual verification in the browser** (no unit test — this is presentation wiring)

Run backend + frontend in the worktree (ports from `.worktree.env`: API 8001, web 3001):

```bash
# terminal 1
cd backend && CUBEPLEX_API__HOST=0.0.0.0 uv run python main.py
# terminal 2
cd frontend && pnpm dev   # wrapped script picks up PORT=3001 from .worktree.env
```

Then in a browser at the worktree web port:
1. Send a message that triggers a multi-step/tool run.
2. While it streams, type into the box → confirm the Stop button becomes a Send button and the textarea is editable.
3. Press Enter / click Send → the typed message appears immediately as a user bubble, the run keeps streaming (no Stop→restart), and the model acknowledges the steer at its next step.
4. Clear the box mid-run → button reverts to Stop; clicking it cancels (existing behavior).
5. Reload → the steered user message is still present in the right place.

Report what you observed (golden path + the empty-box Stop fallback). If you cannot run the browser, say so explicitly.

- [ ] **Step 5: Lint + build web**

Run: `cd frontend && pnpm --filter @cubeplex/web lint && pnpm --filter @cubeplex/core build`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add frontend/packages/web/components/layout/InputBar.tsx
git commit -m "feat(input): steer the live run when typing during a stream"
```

---

## Task 6: Verification sweep (worktree E2E + full changed-module tests)

**Files:** none (verification only)

- [ ] **Step 1: Ensure worktree has E2E config**

The worktree needs `backend/.env` and `backend/config.development.local.yaml` (both gitignored) copied from the main checkout before E2E can hit a real LLM + sandbox:

```bash
cp /home/chris/cubeplex/backend/.env backend/.env
cp /home/chris/cubeplex/backend/config.development.local.yaml backend/config.development.local.yaml
```

- [ ] **Step 2: Backend changed-module tests**

Run: `cd backend && uv run pytest tests/unit/test_run_manager_steer.py -v`
Expected: PASS.

Run the steer E2E (real LLM + sandbox):
Run: `cd backend && uv run pytest tests/e2e/test_steer_endpoint.py -v`
Expected: PASS — the `STEER_MARKER_42` user turn appears in history.

- [ ] **Step 3: Frontend tests**

Run: `cd frontend && pnpm --filter @cubeplex/core test`
Expected: all green (incl. `messageStoreSteer`).

- [ ] **Step 4: Pre-PR full sweep + hooks**

Run: `cd /home/chris/cubeplex/.worktrees/feat/steer-during-run && make check-ci`
Expected: backend ruff + mypy + unit pass; frontend build + lint + format + type-check + vitest pass.

- [ ] **Step 5: Commit any fixups, then hand off to the PR + codex review loop**

Use `/finishing-a-development-branch` to decide merge/PR, then `/pr-codex-review-loop`.

---

## Self-Review notes

- **SSE display of the steered message:** cubepi emits `MessageStart`/`MessageEnd` for injected steering messages, but `convert_agent_event_to_sse` / `cubepi_dict_to_agent_event` only translate assistant deltas / tool / usage / error events — **not** a plain user message. So the steered message does NOT arrive as an SSE event; the frontend optimistic append (Task 4) is the live display mechanism, and a post-completion reload reads it from the checkpointer. Task 4b keeps a mid-run reload from duplicating the original turn. (An alternative design — translating the injected user message into a dedicated SSE event so the Redis replay carries it — was considered and rejected for v1: it wouldn't fix live ordering, since the live view renders the whole run as one merged assistant block.)
- **Empty-box Stop preserved:** Task 5 keeps the existing cancel path verbatim for the streaming + empty-box case.
- **Single-process limitation:** `steer_run` returns `false` if the run's agent isn't in this process — identical to cancel today; documented, not solved here. The frontend rolls back the optimistic bubble on `steered:false` (Task 4 Step 5).
- **Endpoint semantics + late-steer race:** `steered:true` means "accepted into the live agent's queue," NOT "consumed by the model." There is a tiny race: a steer enqueued in the instant *after* the loop's final tail drain (Task 0) returns empty and *before* it breaks is delivered (200, `steered:true`) but never consumed — the run ends. This window is small (same event loop, the tail drain + break straddle only a couple of awaits) and the cost is a no-op steer, not corruption. Acceptable for v1; closing it fully would need cubepi to drain-under-lock after the stream completes.
- **Task/async safety of `steer_run`:** `agent.steer()` is called from the HTTP request task while `agent.prompt()` runs in the background run task. `_steering_queue` is a plain list with no lock, but both tasks run on the **same single-threaded event loop** and `steer()` does a synchronous `list.append` with no `await` — so there's no interleaving hazard. Do NOT call `steer_run` from a thread; keep it on the event loop.
- **Reload-during-run ordering (Task 4b):** the optimistic steer bubble renders below the live merged assistant block, and a steer entered before a mid-run reload may not show until the run completes — both documented as v1 limitations in Tasks 4 / 4b. No data loss; corrects after completion.
- **Empty-box Stop preserved:** Task 5 keeps the existing cancel path verbatim for the streaming + empty-box case; attachments stay disabled during streaming (steer is text-only).
- **Type consistency:** `steerRun` (api) → `steer` (store) → `handleSteer` (InputBar) all use `(client, conversationId, content: string)`. Endpoint returns `{steered, run_id}`; `SteerRunResponse` mirrors it.
