# Steer Message Display Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show a sent-but-not-yet-injected steer message as a dimmed "pending" chip above the input box, then commit it inline into the transcript at the real injection point (so reload doesn't move it), with best-effort cancel.

**Architecture:** A client-minted `steer_id` is the join key across the whole flow (carried in `UserMessage.metadata`). cubepi gains queue-removal for cancel. cubeplex forwards a new `injected_message` SSE event when cubepi actually injects the steer, threaded through *both* backend translation layers. The frontend store holds pending steers outside the transcript, commits the current streaming bubble + the steer message on the SSE signal, and exposes cancel.

**Tech Stack:** Python (FastAPI, pydantic, pytest, redis pub/sub), cubepi agent runtime, TypeScript (Zustand, vitest), Next.js/React, Playwright.

**Spec:** `docs/dev/specs/2026-05-25-steer-message-display-design.md`

**Worktree:** `/home/chris/cubeplex/.worktrees/feat/steer-message-display` (ports 8059/3059 — see `.worktree.env`). cubepi lives at `/home/chris/cubepi` (separate repo; commit there separately).

---

## File Structure

| Area | File | Responsibility |
|---|---|---|
| cubepi | `cubepi/agent/agent.py` | `_MessageQueue.remove(steer_id)`, `Agent.cancel_steer(steer_id)` |
| cubepi test | `tests/agent/test_agent.py` | queue removal + cancel_steer unit tests |
| backend | `cubeplex/agents/schemas.py` | `InjectedMessageEvent` typed event |
| backend | `cubeplex/agents/stream.py` | translate injected `UserMessage` MessageEnd → wire dict |
| backend | `cubeplex/streams/run_manager.py` | `cubepi_dict_to_agent_event` branch, `_on_event` seed-skip, `steer_id` threading, `dispatch_cancel_steer`, control type |
| backend | `cubeplex/api/routes/v1/conversations.py` | `steer_id` on steer request, `POST /steer/cancel` route |
| core | `src/types/events.ts` | `InjectedMessageEvent` + union member |
| core | `src/api/stream.ts` | `steerRun(..., steerId)`, `cancelSteer(...)` |
| core | `src/stores/messageStore.ts` | `pendingSteers`, `steer`/`cancelSteer`, `buildTurnMessages`, `commitTurnAndInject`, cleanup |
| web | `components/layout/PendingSteers.tsx` | dimmed pending chip list + cancel |
| web | `components/layout/InputBar.tsx` | render `<PendingSteers>` above textarea |
| web | `components/chat/MessageList.tsx` | (no change — committed steer is a normal user message) |

---

## Phase 1 — cubepi (upstream): cancel support

> Commit these in the cubepi repo (`/home/chris/cubepi`), not cubeplex.

### Task 1: Queue removal + `Agent.cancel_steer`

**Files:**
- Modify: `/home/chris/cubepi/cubepi/agent/agent.py` (`_MessageQueue` ~line 42, `Agent` ~line 181)
- Test: `/home/chris/cubepi/tests/agent/test_agent.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/agent/test_agent.py
from cubepi.agent.agent import _MessageQueue
from cubepi.providers.base import TextContent, UserMessage


def _steer_msg(text: str, steer_id: str) -> UserMessage:
    return UserMessage(content=[TextContent(text=text)], metadata={"steer_id": steer_id})


def test_message_queue_remove_by_steer_id():
    q = _MessageQueue(mode="all")
    q.enqueue(_steer_msg("a", "s1"))
    q.enqueue(_steer_msg("b", "s2"))
    assert q.remove("s1") is True
    assert [m.content[0].text for m in q.drain()] == ["b"]


def test_message_queue_remove_missing_returns_false():
    q = _MessageQueue(mode="all")
    q.enqueue(_steer_msg("a", "s1"))
    assert q.remove("nope") is False
    assert len(q.drain()) == 1


def test_message_queue_remove_ignores_messages_without_steer_id():
    q = _MessageQueue(mode="all")
    q.enqueue(UserMessage(content=[TextContent(text="x")]))  # no metadata.steer_id
    assert q.remove("s1") is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/chris/cubepi && uv run pytest tests/agent/test_agent.py -k "message_queue_remove" -v`
Expected: FAIL with `AttributeError: '_MessageQueue' object has no attribute 'remove'`

- [ ] **Step 3: Implement `_MessageQueue.remove`**

Add to `_MessageQueue` (after `drain`, before `clear`):

```python
    def remove(self, steer_id: str) -> bool:
        kept = [
            m
            for m in self._messages
            if getattr(m, "metadata", {}).get("steer_id") != steer_id
        ]
        removed = len(kept) != len(self._messages)
        self._messages = kept
        return removed
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/chris/cubepi && uv run pytest tests/agent/test_agent.py -k "message_queue_remove" -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Write the failing test for `Agent.cancel_steer`**

```python
# tests/agent/test_agent.py — add this test
import pytest
from cubepi.agent.agent import Agent
from cubepi.providers.base import Model
from cubepi.providers.faux import FauxProvider  # already used by this test file


@pytest.mark.asyncio
async def test_agent_cancel_steer_removes_queued_steer():
    agent = Agent(provider=FauxProvider(), model=Model(id="m", provider="p"))
    agent.steer(_steer_msg("hi", "s1"))
    assert agent.cancel_steer("s1") is True
    assert agent.cancel_steer("s1") is False
```

> `FauxProvider` is imported from `cubepi.providers.faux` (the existing `test_agent.py` already uses it at line 13). Match how other `Agent` tests in this file construct the agent.

- [ ] **Step 6: Run to verify it fails**

Run: `cd /home/chris/cubepi && uv run pytest tests/agent/test_agent.py -k "cancel_steer" -v`
Expected: FAIL with `AttributeError: 'Agent' object has no attribute 'cancel_steer'`

- [ ] **Step 7: Implement `Agent.cancel_steer`**

Add to `Agent` right after `steer` (~line 182):

```python
    def cancel_steer(self, steer_id: str) -> bool:
        """Remove a not-yet-drained steering message by its steer_id.

        Returns True if a queued message was removed; False if it was already
        drained or never queued (best-effort cancel).
        """
        return self._steering_queue.remove(steer_id)
```

- [ ] **Step 8: Run to verify it passes**

Run: `cd /home/chris/cubepi && uv run pytest tests/agent/test_agent.py -k "cancel_steer or message_queue_remove" -v`
Expected: PASS

- [ ] **Step 9: Commit (in cubepi repo)**

```bash
cd /home/chris/cubepi
git add cubepi/agent/agent.py tests/agent/test_agent.py
git commit -m "feat(agent): cancel a not-yet-drained steering message by steer_id"
```

---

## Phase 2 — cubeplex backend

### Task 2: `InjectedMessageEvent` schema + stream.py converter

**Files:**
- Modify: `backend/cubeplex/agents/schemas.py` (after `UsageEvent` ~line 158)
- Modify: `backend/cubeplex/agents/stream.py` (`convert_agent_event_to_sse` ~line 136)
- Test: add cases to the existing `backend/tests/unit/test_stream.py`

- [ ] **Step 1: Add the schema**

In `cubeplex/agents/schemas.py`, after `UsageEvent`:

```python
class InjectedMessageEvent(AgentEvent):
    """A user message injected mid-run (a steer) that cubepi has now drained
    into the thread. Carries the join key so the frontend can match it to a
    pending chip and commit it at the real transcript position.
    """

    type: Literal["injected_message"] = "injected_message"
    data: dict[str, Any] = Field(description="Event data with content and steer_id")
```

- [ ] **Step 2: Write the failing converter test**

```python
# backend/tests/unit/test_stream.py — add these cases
from cubepi.agent.types import MessageEndEvent
from cubepi.providers.base import TextContent, UserMessage
from cubeplex.agents.stream import convert_agent_event_to_sse


def test_injected_user_message_becomes_injected_message_dict():
    msg = UserMessage(content=[TextContent(text="do X instead")], metadata={"steer_id": "s1"})
    out = convert_agent_event_to_sse(MessageEndEvent(message=msg))
    assert out == [{"type": "injected_message", "content": "do X instead", "steer_id": "s1"}]


def test_injected_user_message_without_steer_id_is_dropped():
    msg = UserMessage(content=[TextContent(text="seed prompt")])
    assert convert_agent_event_to_sse(MessageEndEvent(message=msg)) == []
```

> Verify the `MessageEndEvent` import path against the existing assistant-usage path in `stream.py` (it already imports `MessageEndEvent`); match that import.

- [ ] **Step 3: Run to verify it fails**

Run: `cd /home/chris/cubeplex/.worktrees/feat/steer-message-display/backend && uv run pytest tests/unit/test_stream.py -v`
Expected: FAIL (returns `[]`, not the injected_message dict)

- [ ] **Step 4: Implement the converter branch**

In `stream.py`, add `UserMessage` to the existing cubepi imports, then insert this branch *before* the final `return []` (after the AssistantMessage usage branch ~line 147):

```python
    if isinstance(evt, MessageEndEvent) and isinstance(evt.message, UserMessage):
        steer_id = evt.message.metadata.get("steer_id")
        if steer_id:
            text = "".join(
                c.text for c in evt.message.content if isinstance(c, TextContent)
            )
            return [{"type": "injected_message", "content": text, "steer_id": steer_id}]
```

> `TextContent` is already imported in `stream.py` (used by `_stringify_tool_result`); add `UserMessage` to that import line.

- [ ] **Step 5: Run to verify it passes**

Run: `cd .../backend && uv run pytest tests/unit/test_stream.py -v`
Expected: PASS (2 tests)

- [ ] **Step 6: Commit**

```bash
git add backend/cubeplex/agents/schemas.py backend/cubeplex/agents/stream.py backend/tests/unit/test_stream.py
git commit -m "feat(agents): translate injected steer UserMessage into injected_message SSE dict"
```

### Task 3: `cubepi_dict_to_agent_event` branch + `_on_event` seed-skip

**Files:**
- Modify: `backend/cubeplex/streams/run_manager.py` (`cubepi_dict_to_agent_event` ~line 243; `_on_event` ~line 1340)
- Test: `backend/tests/unit/test_run_manager_translate.py` (create if absent)

- [ ] **Step 1: Write the failing translator test**

```python
# backend/tests/unit/test_run_manager_translate.py
from cubeplex.streams.run_manager import cubepi_dict_to_agent_event
from cubeplex.agents.schemas import InjectedMessageEvent


def test_injected_message_dict_becomes_typed_event():
    evt = cubepi_dict_to_agent_event(
        {"type": "injected_message", "content": "do X", "steer_id": "s1"},
        "2026-05-25T00:00:00+00:00",
    )
    assert isinstance(evt, InjectedMessageEvent)
    assert evt.data == {"content": "do X", "steer_id": "s1"}
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd .../backend && uv run pytest tests/unit/test_run_manager_translate.py -v`
Expected: FAIL (`cubepi_dict_to_agent_event` returns `None` for the unknown type)

- [ ] **Step 3: Implement the translator branch**

In `cubepi_dict_to_agent_event`, add `InjectedMessageEvent` to the local schema import, then add this branch (alongside the other `if t == ...` branches):

```python
    if t == "injected_message":
        return InjectedMessageEvent(
            timestamp=timestamp,
            data={"content": d.get("content", ""), "steer_id": d.get("steer_id", "")},
        )
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd .../backend && uv run pytest tests/unit/test_run_manager_translate.py -v`
Expected: PASS

- [ ] **Step 5: Implement `_on_event` seed-skip**

In `_run_cubepi_path`, just before the `_on_event` definition (~line 1340), add a counter, and skip the first user-message `MessageEnd`:

```python
            from cubepi.agent.types import MessageEndEvent as _MsgEndEvent
            from cubepi.providers.base import UserMessage as _UserMsg

            _user_msg_seen = 0

            def _on_event(evt: Any, _signal: Any = None) -> None:
                nonlocal _user_msg_seen
                if isinstance(evt, _MsgEndEvent) and isinstance(evt.message, _UserMsg):
                    _user_msg_seen += 1
                    if _user_msg_seen == 1:
                        return  # seed prompt — already shown optimistically
                for d in convert_agent_event_to_sse(evt):
                    sse_queue.put_nowait(d)
```

> This replaces the existing `_on_event` body. Keep the existing `convert_agent_event_to_sse` loop. Seed-dedup lives ONLY here.

- [ ] **Step 6: Verify backend still imports/lints**

Run: `cd .../backend && uv run mypy cubeplex/streams/run_manager.py && uv run ruff check cubeplex/streams/run_manager.py`
Expected: no errors

- [ ] **Step 7: Commit**

```bash
git add backend/cubeplex/streams/run_manager.py backend/tests/unit/test_run_manager_translate.py
git commit -m "feat(streams): forward injected_message through translator; skip seed user message"
```

### Task 4: `steer_id` threading + cancel endpoint + control plane

**Files:**
- Modify: `backend/cubeplex/api/routes/v1/conversations.py` (`SteerMessageRequest` ~line 270; steer route ~line 829; add cancel route)
- Modify: `backend/cubeplex/streams/run_manager.py` (`dispatch_steer`, `_publish_control`, `_handle_control`, add `dispatch_cancel_steer`)
- Test: extend the existing `backend/tests/unit/test_run_manager_steer.py`

- [ ] **Step 1: Write the failing dispatch tests**

The existing file already has a `_FakeAgent` (with `.steer`) and a `_make_manager()`
helper using `RunManager.__new__(RunManager)`. Extend `_FakeAgent` with
`cancel_steer`, and add a tiny redis stub for the publish path:

```python
# backend/tests/unit/test_run_manager_steer.py — extend the existing _FakeAgent
class _FakeAgent:
    def __init__(self) -> None:
        self.steered: list = []
        self.cancelled: list[str] = []

    def steer(self, message) -> None:  # noqa: ANN001
        self.steered.append(message)

    def cancel_steer(self, steer_id: str) -> bool:  # noqa: ANN001
        self.cancelled.append(steer_id)
        return True


class _FakeRedis:
    def __init__(self) -> None:
        self.published: list[str] = []

    async def publish(self, channel: str, payload: str) -> None:
        self.published.append(payload)


# add these tests to the file
@pytest.mark.asyncio
async def test_dispatch_steer_threads_steer_id_into_metadata() -> None:
    mgr = _make_manager()
    mgr._agents = {}
    agent = _FakeAgent()
    mgr._agents["run-1"] = agent
    status = await mgr.dispatch_steer("run-1", "do X", steer_id="s1")
    assert status == "steered"
    assert agent.steered[0].metadata["steer_id"] == "s1"


@pytest.mark.asyncio
async def test_dispatch_cancel_steer_calls_agent() -> None:
    mgr = _make_manager()
    mgr._agents = {}
    agent = _FakeAgent()
    mgr._agents["run-1"] = agent
    status = await mgr.dispatch_cancel_steer("run-1", "s1")
    assert status == "cancelled"
    assert agent.cancelled == ["s1"]


@pytest.mark.asyncio
async def test_dispatch_cancel_steer_no_local_agent_publishes() -> None:
    mgr = _make_manager()
    mgr._agents = {}
    mgr._redis = _FakeRedis()
    mgr._control_channel = "ctrl"
    status = await mgr.dispatch_cancel_steer("missing-run", "s1")
    assert status == "published"
```

> The existing `_FakeAgent` in this file currently records `message.content[0].text`
> in `steered`; change it to append the whole `message` (as shown) so the metadata
> assertion works, and update the pre-existing `steer_run` tests' assertions
> accordingly (they read `agent.steered == ["..."]` → change to
> `agent.steered[0].content[0].text == "..."`).

- [ ] **Step 2: Run to verify it fails**

Run: `cd .../backend && uv run pytest tests/unit/test_run_manager_steer.py -v`
Expected: FAIL (`dispatch_steer` has no `steer_id` kwarg; no `dispatch_cancel_steer`)

- [ ] **Step 3: Thread `steer_id` through `dispatch_steer` + control**

Replace `dispatch_steer` and extend `_publish_control` / `_handle_control` in `run_manager.py`:

```python
    async def _publish_control(
        self,
        run_id: str,
        type_: str,
        content: str | None = None,
        steer_id: str | None = None,
    ) -> None:
        import json

        payload: dict[str, Any] = {"run_id": run_id, "type": type_}
        if content is not None:
            payload["content"] = content
        if steer_id is not None:
            payload["steer_id"] = steer_id
        await self._redis.publish(self._control_channel, json.dumps(payload))

    async def dispatch_steer(self, run_id: str, content: str, steer_id: str) -> str:
        agent = self._agents.get(run_id)
        if agent is not None:
            from cubepi.providers.base import TextContent, UserMessage

            agent.steer(
                UserMessage(
                    content=[TextContent(text=content)],
                    metadata={"steer_id": steer_id},
                )
            )
            return "steered"
        await self._publish_control(run_id, "steer", content, steer_id=steer_id)
        return "published"

    async def dispatch_cancel_steer(self, run_id: str, steer_id: str) -> str:
        agent = self._agents.get(run_id)
        if agent is not None:
            removed = agent.cancel_steer(steer_id)
            return "cancelled" if removed else "not_found"
        await self._publish_control(run_id, "cancel_steer", steer_id=steer_id)
        return "published"
```

Then extend `_handle_control` (add `steer_id` to the steer branch and a new `cancel_steer` branch):

```python
        elif type_ == "steer":
            agent = self._agents.get(run_id)
            if agent is not None:
                from cubepi.providers.base import TextContent, UserMessage

                agent.steer(
                    UserMessage(
                        content=[TextContent(text=data.get("content") or "")],
                        metadata={"steer_id": data.get("steer_id") or ""},
                    )
                )
        elif type_ == "cancel_steer":
            agent = self._agents.get(run_id)
            if agent is not None:
                agent.cancel_steer(data.get("steer_id") or "")
```

> Leave the older `steer_run` method (line ~527) in place — it is still covered by
> the existing tests in `test_run_manager_steer.py`. Only `dispatch_steer` (used by
> the route) needs the `steer_id` change. Do not delete `steer_run`.

- [ ] **Step 4: Run to verify it passes**

Run: `cd .../backend && uv run pytest tests/unit/test_run_manager_steer.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Add `steer_id` to the request model + steer route + cancel route**

In `conversations.py`, update `SteerMessageRequest`:

```python
class SteerMessageRequest(BaseModel):
    """Request body for steering an in-flight run."""

    content: str
    steer_id: str


class CancelSteerRequest(BaseModel):
    """Request body for cancelling a not-yet-drained steer."""

    steer_id: str
```

Update the steer route's dispatch call (line ~865):

```python
    dispatch_status = await run_manager.dispatch_steer(
        active_run.run_id, body.content, steer_id=body.steer_id
    )
    return {"status": dispatch_status, "run_id": active_run.run_id}
```

Add a new route after `steer_active_run`:

```python
@router.post("/{conversation_id}/steer/cancel", status_code=status.HTTP_202_ACCEPTED)
async def cancel_steer(
    conversation_id: str,
    body: CancelSteerRequest,
    raw_request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
    rds: Annotated[RedisHandle, Depends(redis_dep)],
) -> dict[str, object]:
    """Best-effort cancel of a not-yet-drained steer on the active run."""
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
        rds.client, prefix=rds.key_prefix, conversation_id=conversation_id
    )
    if active_run is None or active_run.status != "running":
        return {"status": "no_active_run", "run_id": None}

    run_manager = raw_request.app.state.run_manager
    dispatch_status = await run_manager.dispatch_cancel_steer(active_run.run_id, body.steer_id)
    return {"status": dispatch_status, "run_id": active_run.run_id}
```

- [ ] **Step 6: Verify lint/type**

Run: `cd .../backend && uv run mypy cubeplex/ && uv run ruff check cubeplex/`
Expected: no errors

- [ ] **Step 7: Commit**

```bash
git add backend/cubeplex/streams/run_manager.py backend/cubeplex/api/routes/v1/conversations.py backend/tests/unit/test_run_manager_steer.py
git commit -m "feat(api): thread steer_id through steer; add best-effort cancel-steer endpoint"
```

---

## Phase 3 — frontend core

### Task 5: Types + API client

**Files:**
- Modify: `frontend/packages/core/src/types/events.ts` (union ~line 51; add interface ~line 163)
- Modify: `frontend/packages/core/src/api/stream.ts`
- Test: `frontend/packages/core/__tests__/api/stream.test.ts` (extend if exists, else create)

- [ ] **Step 1: Add the event type**

In `events.ts`, add `'injected_message'` to the `AgentEventType` union, and add the interface:

```typescript
export interface InjectedMessageEvent extends AgentEvent {
  type: 'injected_message'
  data: { content: string; steer_id: string }
}
```

- [ ] **Step 2: Write the failing API test**

```typescript
// frontend/packages/core/__tests__/api/stream.test.ts
import { describe, it, expect, vi } from 'vitest'
import { steerRun, cancelSteer } from '../../src/api/stream'

function fakeClient(capture: { path?: string; body?: unknown }) {
  return {
    post: vi.fn(async (path: string, body: unknown) => {
      capture.path = path
      capture.body = body
      return { ok: true, json: async () => ({ status: 'steered', run_id: 'r1' }) }
    }),
  } as never
}

describe('steer api', () => {
  it('steerRun sends content + steer_id', async () => {
    const cap: { path?: string; body?: unknown } = {}
    await steerRun(fakeClient(cap), 'conv-1', 'do X', 's1')
    expect(cap.path).toBe('/api/v1/conversations/conv-1/steer')
    expect(cap.body).toEqual({ content: 'do X', steer_id: 's1' })
  })

  it('cancelSteer posts steer_id to the cancel route', async () => {
    const cap: { path?: string; body?: unknown } = {}
    await cancelSteer(fakeClient(cap), 'conv-1', 's1')
    expect(cap.path).toBe('/api/v1/conversations/conv-1/steer/cancel')
    expect(cap.body).toEqual({ steer_id: 's1' })
  })
})
```

- [ ] **Step 3: Run to verify it fails**

Run: `cd frontend/packages/core && pnpm exec vitest run __tests__/api/stream.test.ts`
Expected: FAIL (`cancelSteer` not exported; `steerRun` ignores 4th arg)

- [ ] **Step 4: Implement the API changes**

In `stream.ts`, replace `steerRun` and add `cancelSteer`:

```typescript
export async function steerRun(
  client: ApiClient,
  conversationId: string,
  content: string,
  steerId: string,
): Promise<SteerRunResponse> {
  const res = await client.post(`/api/v1/conversations/${conversationId}/steer`, {
    content,
    steer_id: steerId,
  })
  if (!res.ok) {
    throw new Error(`Failed to steer run: HTTP ${res.status}`)
  }
  return (await res.json()) as SteerRunResponse
}

export interface CancelSteerResponse {
  status: 'cancelled' | 'not_found' | 'published' | 'no_active_run'
  run_id: string | null
}

export async function cancelSteer(
  client: ApiClient,
  conversationId: string,
  steerId: string,
): Promise<CancelSteerResponse> {
  const res = await client.post(`/api/v1/conversations/${conversationId}/steer/cancel`, {
    steer_id: steerId,
  })
  if (!res.ok) {
    throw new Error(`Failed to cancel steer: HTTP ${res.status}`)
  }
  return (await res.json()) as CancelSteerResponse
}
```

- [ ] **Step 5: Run to verify it passes**

Run: `cd frontend/packages/core && pnpm exec vitest run __tests__/api/stream.test.ts`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add frontend/packages/core/src/types/events.ts frontend/packages/core/src/api/stream.ts frontend/packages/core/__tests__/api/stream.test.ts
git commit -m "feat(core): injected_message event type; steerRun steer_id + cancelSteer api"
```

### Task 6: `messageStore` — pending steers state + steer/cancelSteer actions

**Files:**
- Modify: `frontend/packages/core/src/stores/messageStore.ts`
- Test: `frontend/packages/core/__tests__/stores/messageStorePendingSteer.test.ts` (create)

- [ ] **Step 1: Write the failing tests**

```typescript
// frontend/packages/core/__tests__/stores/messageStorePendingSteer.test.ts
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { useMessageStore } from '../../src/stores/messageStore'

vi.mock('../../src/api', async (orig) => {
  const actual = await (orig as () => Promise<Record<string, unknown>>)()
  return {
    ...actual,
    steerRun: vi.fn(async () => ({ status: 'steered', run_id: 'r1' })),
    cancelSteer: vi.fn(async () => ({ status: 'cancelled', run_id: 'r1' })),
  }
})

const client = {} as never

describe('pending steers', () => {
  beforeEach(() => {
    useMessageStore.setState({
      messages: {},
      pendingSteers: {},
      isStreaming: true,
      streamingConversationId: 'c1',
    })
  })

  it('steer() adds to pendingSteers, not messages', async () => {
    await useMessageStore.getState().steer(client, 'c1', 'do X')
    const s = useMessageStore.getState()
    expect(s.pendingSteers.c1).toHaveLength(1)
    expect(s.pendingSteers.c1[0].text).toBe('do X')
    expect(s.messages.c1 ?? []).toHaveLength(0)
  })

  it('cancelSteer() removes the pending entry', async () => {
    await useMessageStore.getState().steer(client, 'c1', 'do X')
    const id = useMessageStore.getState().pendingSteers.c1[0].steerId
    await useMessageStore.getState().cancelSteer(client, 'c1', id)
    expect(useMessageStore.getState().pendingSteers.c1 ?? []).toHaveLength(0)
  })
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend/packages/core && pnpm exec vitest run __tests__/stores/messageStorePendingSteer.test.ts`
Expected: FAIL (`pendingSteers` undefined; `cancelSteer` not a function)

- [ ] **Step 3: Add state + interface members**

In `MessageStore` interface add:

```typescript
  pendingSteers: Record<string, { steerId: string; text: string }[]>
  cancelSteer(client: ApiClient, conversationId: string, steerId: string): Promise<void>
```

In the store initializer (alongside `messages: {}`) add `pendingSteers: {},`.
Add `cancelSteer` and `steerRun` to the `../api` import (the file already imports `steerRun`; add `cancelSteer`).

- [ ] **Step 4: Rewrite `steer()` to use pendingSteers**

Replace the existing `steer` action body:

```typescript
  async steer(client, conversationId, content) {
    const text = content.trim()
    if (!text) return
    const state = get()
    if (!state.isStreaming || state.streamingConversationId !== conversationId) return

    const steerId = nextMessageId('steer')
    set((s) => ({
      pendingSteers: {
        ...s.pendingSteers,
        [conversationId]: [
          ...(s.pendingSteers[conversationId] ?? []),
          { steerId, text },
        ],
      },
    }))

    const removePending = () =>
      set((s) => ({
        pendingSteers: {
          ...s.pendingSteers,
          [conversationId]: (s.pendingSteers[conversationId] ?? []).filter(
            (p) => p.steerId !== steerId,
          ),
        },
      }))

    try {
      const res = await steerRun(client, conversationId, text, steerId)
      if (res.status === 'no_active_run') removePending()
    } catch (err) {
      console.error('Failed to steer run:', err)
      removePending()
    }
  },

  async cancelSteer(client, conversationId, steerId) {
    set((s) => ({
      pendingSteers: {
        ...s.pendingSteers,
        [conversationId]: (s.pendingSteers[conversationId] ?? []).filter(
          (p) => p.steerId !== steerId,
        ),
      },
    }))
    try {
      await cancelSteer(client, conversationId, steerId)
    } catch (err) {
      console.error('Failed to cancel steer:', err)
    }
  },
```

> This removes the old optimistic-`messages` append entirely.

- [ ] **Step 5: Run to verify it passes**

Run: `cd frontend/packages/core && pnpm exec vitest run __tests__/stores/messageStorePendingSteer.test.ts`
Expected: PASS

- [ ] **Step 6: Update the existing steer test**

`__tests__/stores/messageStoreSteer.test.ts` asserts the old optimistic-message behavior. Update its assertions to expect `pendingSteers` instead of an appended message. Run:
`cd frontend/packages/core && pnpm exec vitest run __tests__/stores/messageStoreSteer.test.ts`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add frontend/packages/core/src/stores/messageStore.ts frontend/packages/core/__tests__/stores/messageStorePendingSteer.test.ts frontend/packages/core/__tests__/stores/messageStoreSteer.test.ts
git commit -m "feat(core): hold steers in pendingSteers state with cancel, off the transcript"
```

### Task 7: `messageStore` — commit on injected_message + buildTurnMessages refactor + cleanup

**Files:**
- Modify: `frontend/packages/core/src/stores/messageStore.ts`
- Test: `frontend/packages/core/__tests__/stores/messageStoreCommitSteer.test.ts` (create)

- [ ] **Step 1: Extract `buildTurnMessages` (pure refactor, no behavior change)**

Pull the message-building body out of `finalizeCompletedStream` into a module-level pure function. It takes the agents map + maps and returns the messages; `finalizeCompletedStream` then calls it. Signature:

```typescript
function buildTurnMessages(
  agents: Record<string, AgentStream>,
  toolResultMap: MessageStore['toolResultMap'],
  turnUsage: import('../types').TurnUsage | null,
): { assistantMessage: AssistantMessageType | null; toolMessages: ToolResultMessageType[] } {
  const mainStream = agents[MAIN_AGENT_KEY]
  if (!mainStream) return { assistantMessage: null, toolMessages: [] }
  // ... existing finalBlocks / assistantMessage / toolMessages construction
  // moved verbatim from finalizeCompletedStream, returning the pieces ...
  return { assistantMessage, toolMessages }
}
```

Refactor `finalizeCompletedStream` to call it and then `set(...)` the messages + clear streaming state exactly as before. Run the full core suite to confirm no regressions:
`cd frontend/packages/core && pnpm exec vitest run`
Expected: PASS (unchanged behavior)

- [ ] **Step 2: Commit the refactor**

```bash
git add frontend/packages/core/src/stores/messageStore.ts
git commit -m "refactor(core): extract buildTurnMessages from finalizeCompletedStream"
```

- [ ] **Step 3: Write the failing commit test**

```typescript
// frontend/packages/core/__tests__/stores/messageStoreCommitSteer.test.ts
import { describe, it, expect, beforeEach } from 'vitest'
import { useMessageStore } from '../../src/stores/messageStore'

function applyInjected(convId: string, content: string, steerId: string) {
  // helper invoked by the store's stream consumer; we test the handler directly
  return useMessageStore.getState().__commitTurnAndInject(convId, {
    content,
    steer_id: steerId,
  })
}

describe('commit on injected_message', () => {
  beforeEach(() => {
    useMessageStore.setState({
      messages: { c1: [{ id: 'u1', role: 'user', content: [{ type: 'text', text: 'go' }], timestamp: 1, metadata: {} }] },
      streamAgents: { main: { text: 'partial', toolCalls: [], toolResults: [], thinking: '', blocks: [{ type: 'text', text: 'partial' }], name: null } },
      pendingSteers: { c1: [{ steerId: 's1', text: 'do X' }] },
      toolResultMap: {},
      turnUsage: { c1: null },
      isStreaming: true,
      streamingConversationId: 'c1',
    })
  })

  it('finalizes the current bubble, inserts the steer user msg, resets streams, clears pending', () => {
    applyInjected('c1', 'do X', 's1')
    const s = useMessageStore.getState()
    const msgs = s.messages.c1
    const roles = msgs.map((m) => m.role)
    expect(roles).toEqual(['user', 'assistant', 'user']) // original, finalized bubble, steer
    expect(msgs[2].content[0]).toMatchObject({ type: 'text', text: 'do X' })
    expect(msgs[2].metadata?.steer_id).toBe('s1')
    expect(s.streamAgents.main.text).toBe('') // reset
    expect(s.pendingSteers.c1 ?? []).toHaveLength(0)
  })

  it('skips the empty assistant bubble when the main stream has no content', () => {
    useMessageStore.setState({
      streamAgents: { main: { text: '', toolCalls: [], toolResults: [], thinking: '', blocks: [], name: null } },
    })
    applyInjected('c1', 'do X', 's1')
    const roles = useMessageStore.getState().messages.c1.map((m) => m.role)
    expect(roles).toEqual(['user', 'user']) // original, steer — no empty assistant
  })
})
```

> The test reaches a `__commitTurnAndInject(convId, data)` method exposed on the store for testability. The stream consumers call the same method.

- [ ] **Step 4: Run to verify it fails**

Run: `cd frontend/packages/core && pnpm exec vitest run __tests__/stores/messageStoreCommitSteer.test.ts`
Expected: FAIL (`__commitTurnAndInject` not a function)

- [ ] **Step 5: Implement `__commitTurnAndInject` + wire into both consumers**

Add to the `MessageStore` interface:

```typescript
  __commitTurnAndInject(conversationId: string, data: { content: string; steer_id: string }): void
```

Implement in the store:

```typescript
  __commitTurnAndInject(conversationId, data) {
    const state = get()
    // Idempotency: if this steer is already committed, no-op (replay-safe).
    const already = (state.messages[conversationId] ?? []).some(
      (m) => m.role === 'user' && m.metadata?.steer_id === data.steer_id,
    )
    if (already) return

    const { assistantMessage, toolMessages } = buildTurnMessages(
      state.streamAgents,
      state.toolResultMap,
      state.turnUsage[conversationId] ?? null,
    )
    const mainHasContent =
      !!assistantMessage && assistantMessage.content.length > 0

    const steerMessage: UserMessageType = {
      id: nextMessageId('user-steer'),
      role: 'user',
      content: [{ type: 'text', text: data.content }],
      timestamp: Date.now() / 1000,
      metadata: { steer_id: data.steer_id },
    }

    set((s) => ({
      messages: {
        ...s.messages,
        [conversationId]: [
          ...(s.messages[conversationId] ?? []),
          ...(mainHasContent ? [assistantMessage as AssistantMessageType, ...toolMessages] : []),
          steerMessage,
        ],
      },
      streamAgents: { [MAIN_AGENT_KEY]: emptyStream() },
      pendingSteers: {
        ...s.pendingSteers,
        [conversationId]: (s.pendingSteers[conversationId] ?? []).filter(
          (p) => p.steerId !== data.steer_id,
        ),
      },
    }))
  },
```

Then, in BOTH stream consumers, handle the event. In `send()`'s event loop and in `consumeRunStream()`'s event loop, add a branch alongside the `artifact`/`citation`/`error`/`done` handling (after the `event_id` ordering guard, before `batchedSet`):

```typescript
          } else if (event.type === 'injected_message') {
            const d = event.data as { content: string; steer_id: string }
            // Both consumers buffer mutations through createBatcher (batchedSet);
            // flush so __commitTurnAndInject reads the fully-applied streamAgents,
            // not a stale snapshot with pending batched deltas.
            flush()
            set((s) => ({
              lastAppliedEventId: nextEventId(s.lastAppliedEventId, event.event_id),
            }))
            get().__commitTurnAndInject(conversationId, d)
            continue
          }
```

> `flush` is the function returned by `createBatcher` in each consumer (`send()` and
> `consumeRunStream()` both destructure `{ batchedSet, flush }`). Calling it here is
> mandatory — the commit reads `streamAgents` synchronously, and unflushed
> `batchedSet` deltas would otherwise be dropped/reordered relative to the commit.
> Place the branch so it shares the same `lastAppliedEventId` skip guard the loop
> already applies. In `consumeRunStream`, use the same shape.

- [ ] **Step 6: Run to verify it passes**

Run: `cd frontend/packages/core && pnpm exec vitest run __tests__/stores/messageStoreCommitSteer.test.ts`
Expected: PASS (2 tests)

- [ ] **Step 7: Add pending cleanup on run-ending paths**

In `loadMessages` (the big `set(...)` that resets state), add `pendingSteers: { ...get().pendingSteers, [conversationId]: [] },`. In `finalizeCompletedStream` clear the same — note it has **two** `set(...)` exit points: the early-return branch when `mainStream` is absent (~line 522) AND the final `set` (~line 628); add the clear to **both**. In the `error` branches of `send()` and `consumeRunStream()`, in `cancelStream`, and in `clearStream()`, clear `pendingSteers` for the conversation (in `clearStream`, reset to `{}`). Write a quick test:

```typescript
// append to messageStoreCommitSteer.test.ts
it('clears pending steers on finalize', () => {
  useMessageStore.setState({
    streamAgents: { main: { text: 'x', toolCalls: [], toolResults: [], thinking: '', blocks: [{ type: 'text', text: 'x' }], name: null } },
    pendingSteers: { c1: [{ steerId: 's1', text: 'do X' }] },
  })
  // finalizeCompletedStream is module-internal; trigger via the exported path used in send().
  // Instead assert via clearStream which is public:
  useMessageStore.getState().clearStream()
  expect(useMessageStore.getState().pendingSteers).toEqual({})
})
```

Run: `cd frontend/packages/core && pnpm exec vitest run __tests__/stores/messageStoreCommitSteer.test.ts`
Expected: PASS

- [ ] **Step 8: Run the full core suite + build**

Run: `cd frontend/packages/core && pnpm exec vitest run && pnpm build`
Expected: PASS + clean build (so `@cubeplex/web` sees the new types).

- [ ] **Step 9: Commit**

```bash
git add frontend/packages/core/src/stores/messageStore.ts frontend/packages/core/__tests__/stores/messageStoreCommitSteer.test.ts
git commit -m "feat(core): commit steer into transcript on injected_message; clear pending on run end"
```

---

## Phase 4 — frontend web

### Task 8: Pending steer chips above the input + remove optimistic transcript path

**Files:**
- Create: `frontend/packages/web/components/layout/PendingSteers.tsx`
- Modify: `frontend/packages/web/components/layout/InputBar.tsx`
- Test: `frontend/packages/web/__tests__/components/PendingSteers.test.tsx` (create)

- [ ] **Step 1: Write the failing component test**

```tsx
// frontend/packages/web/__tests__/components/PendingSteers.test.tsx
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { PendingSteers } from '../../components/layout/PendingSteers'

const mocks = vi.hoisted(() => ({
  cancelSteer: vi.fn(),
  setWorkspaceId: vi.fn(),
  pending: [] as { steerId: string; text: string }[],
}))

vi.mock('@cubeplex/core', () => ({
  createApiClient: () => ({ setWorkspaceId: mocks.setWorkspaceId }),
  useMessageStore: (sel: (s: { pendingSteers: Record<string, unknown>; cancelSteer: typeof mocks.cancelSteer }) => unknown) =>
    sel({ pendingSteers: { 'conv-1': mocks.pending }, cancelSteer: mocks.cancelSteer }),
}))
vi.mock('@/hooks/useWorkspaceContext', () => ({ useWorkspaceContext: () => ({ workspaceId: 'ws-1' }) }))

describe('PendingSteers', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.pending = [{ steerId: 's1', text: 'do X instead' }]
  })

  it('renders pending steer text and cancels on click', () => {
    render(<PendingSteers conversationId="conv-1" />)
    expect(screen.getByText('do X instead')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /cancel/i }))
    expect(mocks.cancelSteer).toHaveBeenCalledWith(expect.anything(), 'conv-1', 's1')
  })

  it('renders nothing when there are no pending steers', () => {
    mocks.pending = []
    const { container } = render(<PendingSteers conversationId="conv-1" />)
    expect(container).toBeEmptyDOMElement()
  })
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend/packages/web && pnpm exec vitest run __tests__/components/PendingSteers.test.tsx`
Expected: FAIL (module not found)

- [ ] **Step 3: Implement `PendingSteers.tsx`**

```tsx
'use client'

import { useMessageStore, createApiClient } from '@cubeplex/core'
import { X } from 'lucide-react'
import { useWorkspaceContext } from '@/hooks/useWorkspaceContext'

interface PendingSteersProps {
  conversationId: string
}

export function PendingSteers({ conversationId }: PendingSteersProps): React.ReactElement | null {
  const pending = useMessageStore((s) => s.pendingSteers[conversationId] ?? [])
  const cancelSteer = useMessageStore((s) => s.cancelSteer)
  const { workspaceId } = useWorkspaceContext()

  if (pending.length === 0) return null

  const onCancel = (steerId: string): void => {
    const client = createApiClient('')
    if (workspaceId) client.setWorkspaceId(workspaceId)
    void cancelSteer(client, conversationId, steerId)
  }

  return (
    <div className="mb-2 flex flex-col gap-1.5">
      {pending.map((p) => (
        <div
          key={p.steerId}
          className="flex items-center gap-2 rounded-lg border border-border/60 bg-muted/40 px-3 py-1.5 text-sm text-muted-foreground"
        >
          <span className="flex-1 truncate opacity-70">{p.text}</span>
          <span className="text-[10px] uppercase tracking-wide opacity-50">steering…</span>
          <button
            type="button"
            aria-label="Cancel pending steer"
            onClick={() => onCancel(p.steerId)}
            className="grid size-5 place-items-center rounded hover:bg-muted"
          >
            <X className="size-3" />
          </button>
        </div>
      ))}
    </div>
  )
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd frontend/packages/web && pnpm exec vitest run __tests__/components/PendingSteers.test.tsx`
Expected: PASS (2 tests)

- [ ] **Step 5: Render it in InputBar**

In `InputBar.tsx`, import `PendingSteers` and render it inside the top-level wrapper, above the input shell (right after the opening `<div className="w-full max-w-3xl mx-auto">`):

```tsx
      {conversationId && <PendingSteers conversationId={conversationId} />}
```

- [ ] **Step 6: Fix the existing `InputBar.test.tsx` mock (it will otherwise crash)**

`InputBar` now renders `<PendingSteers>`, which selects `s.pendingSteers[conversationId]`
and `s.cancelSteer`. The existing `InputBar.test.tsx` `useMessageStore` mock returns a
fixed object without those keys, so `s.pendingSteers` is `undefined` and the render
throws. Add the two keys to that mock's `selector({...})` call:

```typescript
    selector({
      send: storeMocks.send,
      steer: storeMocks.steer,
      cancelStream: storeMocks.cancelStream,
      cancelSteer: storeMocks.cancelSteer ?? (() => {}),
      pendingSteers: {},
      isStreaming: storeMocks.state.isStreaming,
      streamingConversationId: storeMocks.state.streamingConversationId,
    }),
```

(Add `cancelSteer: vi.fn()` to the `storeMocks` hoisted object too.)

- [ ] **Step 7: Verify web suite + typecheck**

Run: `cd frontend/packages/web && pnpm exec vitest run __tests__/components && pnpm exec tsc --noEmit`
Expected: PASS + no type errors.

- [ ] **Step 8: Commit**

```bash
git add frontend/packages/web/components/layout/PendingSteers.tsx frontend/packages/web/components/layout/InputBar.tsx frontend/packages/web/__tests__/components/PendingSteers.test.tsx frontend/packages/web/__tests__/components/InputBar.test.tsx
git commit -m "feat(web): dimmed pending-steer chips above the input with cancel"
```

---

## Phase 5 — E2E

### Task 9: Steer pending → commit → cancel E2E

**Files:**
- Modify/extend: the existing steer E2E under `frontend/packages/web/__tests__/e2e/` (find it: `grep -rln "steer" frontend/packages/web/__tests__/e2e`)
- Prereq: copy `backend/.env` + `backend/config.development.local.yaml` into the worktree backend dir; start backend on 8059 and frontend on 3059 (see `.worktree.env`).

- [ ] **Step 1: Write the E2E spec**

Add a test that, against a real run:
1. sends a first message and waits for streaming to start,
2. types a steer + submits, asserts a `steering…` chip appears above the input (`getByText('steering…')` scoped to the input area),
3. waits for the chip to disappear and the steer text to appear as a user message in the transcript **below** the first assistant bubble,
4. reloads the page and asserts the steer user message is in the same position (no jump) — assert ordering of message roles is stable across reload,
5. (separate case) sends a steer and immediately clicks cancel; asserts the chip disappears and — if cancelled before injection — the text never appears in the transcript.

Model the harness/selectors on the existing steer E2E. Use the data-testids already in `InputBar.tsx` (`chat-input`, `send-button`). Add a `data-testid="pending-steer"` to the chip in `PendingSteers.tsx` to make selection robust.

- [ ] **Step 2: Add the testid to the chip**

In `PendingSteers.tsx`, add `data-testid="pending-steer"` to the chip `<div>`.

- [ ] **Step 3: Run the E2E**

Run (from worktree, with servers up): `cd frontend/packages/web && pnpm exec playwright test <steer-spec-file>`
Expected: PASS. If the model/run timing is flaky, gate waits on conditions (chip present, chip absent, message present) rather than fixed timeouts.

- [ ] **Step 4: Commit**

```bash
git add frontend/packages/web/components/layout/PendingSteers.tsx frontend/packages/web/__tests__/e2e/<steer-spec-file>
git commit -m "test(e2e): steer pending chip → inline commit → reload-stable position → cancel"
```

---

## Self-Review Notes

- **Spec coverage:** A=Task 1; B(schema+converter)=Task 2; B(translator+seed-skip)=Task 3; C=Task 4; D=Task 5; E(state/steer/cancel)=Task 6, E(commit/buildTurnMessages/cleanup)=Task 7; F=Task 8; Testing=Tasks 1–9 (E2E=Task 9). All spec sections mapped.
- **Type consistency:** `pendingSteers: Record<string, { steerId; text }[]>`, `steer_id` (wire/metadata) vs `steerId` (TS), `__commitTurnAndInject`, `buildTurnMessages`, `cancelSteer`, `InjectedMessageEvent` used consistently across tasks.
- **Cross-repo:** Task 1 commits in cubepi; Tasks 2–9 in cubeplex. The cubeplex steer feature depends on the cubepi `cancel_steer` for Task 4's cancel path — land cubepi first (or pin the local cubepi for dev).
