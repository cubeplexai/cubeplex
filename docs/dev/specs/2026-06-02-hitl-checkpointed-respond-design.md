# Durable HITL: checkpointed channel + auto-detach + respond path

- **Date:** 2026-06-02
- **Status:** Design, pending review.
- **Area:** `cubeplex/streams/run_manager.py`, `cubeplex/middleware/sandbox.py`,
  `cubeplex/streams/run_events.py`, `cubeplex/api/routes/v1/conversations.py`,
  frontend `paused_hitl` UX.
- **Related:**
  [backend/docs/agent-system-design.md](../../../backend/docs/agent-system-design.md)
  (note: that file is partially stale; runtime is `run_manager.py` + cubepi
  agent loop), cubepi `cubepi/hitl/channel.py`, `cubepi/agent/agent.py`
  (`detach` / `respond`).

## Background & motivation

A user-facing HITL request (sandbox command confirm, or the built-in `ask_user`
tool) today goes through `InMemoryChannel(default_timeout=180.0)` constructed
per-run in `run_manager.py`. The 180-second cap forces the user to answer
within three minutes or the agent's turn fails:

- `SandboxMiddleware.before_tool_call` catches `HitlTimedOut` and blocks the
  command as if the user had denied it.
- `ask_user_tool` returns a `timed out after 180 seconds` tool_result so the
  model retries or gives up.

Three real problems flow from this:

1. **Users routinely lose work** when they step away from the tab. The 180 s
   limit was a worker-liveness hack — it bounds how long a run task can sit
   blocked on an in-process `asyncio.Future` — not a product requirement.
2. **Pending state is invisible across workers.** The future lives in one
   process. If a different backend instance receives the answer POST, it
   publishes on a Redis control channel; only the original worker can deliver.
   If that worker has died or been recycled, the pending request and the
   conversation are stuck.
3. **No durability across restarts.** Worker crash mid-pending loses the run;
   the conversation is left in an inconsistent state (Redis active-run lock,
   no live task, no path to recovery).

cubepi already supplies the primitives to fix all three:

- `CheckpointedChannel` persists `HitlRequest` to
  `cubepi_threads.pending_request` (already migrated in this codebase via
  alembic revision `fdcc495b3704`) and reads it back from any worker.
- `agent.detach()` raises `HitlDetached` inside the pending await, lets the
  agent loop unwind silently, and leaves the persisted pending intact.
- `agent.respond(question_id, answer)` loads messages + pending from the
  checkpointer and resumes the loop from where it suspended.

The plan below uses these to remove the 180-second timeout entirely and let
any worker pick up a paused conversation.

## Goals

- Remove the 180-second cap. A pending HITL request can sit indefinitely; the
  user answers whenever they get back.
- HITL pending state is **durable**: survives worker restart and is readable
  from any backend instance.
- The answer POST is served by **whichever worker receives it**, not pinned to
  the worker that emitted the request. Cross-instance recovery works without
  Redis pub/sub forwarding.
- No regressions in the existing fast-path UX: the user sees the
  `ask_user_request` / `sandbox_confirm_request` event the moment the agent
  emits it, the card renders, the answer flows back through the same SSE
  conversation, and the stream continues with the next agent message.

## Non-goals (YAGNI)

- Reviving a run that was **mid-tool-execution** (not in HITL pending) when a
  worker died. cubepi's `HitlDurabilityNotGuaranteed` guard means only the two
  built-in HITL flows (sandbox confirm + `ask_user`) are durable; custom tool
  bodies that pause cannot be resumed cross-worker, and we don't need them to.
- A new admin UI for browsing paused conversations. The existing conversation
  list already shows the conversation; the AskUserCard reappears on reconnect
  via Redis stream replay (and as a fallback, via the persisted pending — see
  §"Frontend recovery on reload").
- Migrating the Redis active-run lock to a different scheme. We extend the
  existing status enum with one new value; the lock's role is unchanged.

## Architecture overview

The single mechanical change: **the run task releases the worker as soon as
the agent enters HITL pending.** Everything else falls out of that.

Today (simplified):

```
start_run → _execute_run → _run_cubepi_path
  build provider/middleware/tools/channel/agent
  await agent.prompt(user_msg)         ← blocks here while user thinks
  finally: cleanup
```

After this change:

```
start_run → _execute_run → _run_cubepi_path
  build via _build_agent_for_conversation(...)
  subscribe _on_event that does:
    if HitlRequestEvent: schedule agent.detach()
  await agent.prompt(user_msg)         ← returns ~immediately after detach
  finally: mark status (paused_hitl if pending persisted, else completed)
```

When the answer arrives:

```
POST /conversations/{cid}/runs/{rid}/ask-user-answer
  → run_manager.resume_run_with_answer(conversation_id, run_id, question_id, answers)
      check cubepi_threads.pending_request  ← authoritative; not Redis status
      transition Redis active-run row paused_hitl → running (same run_id)
      _run_cubepi_respond_path:
        build via _build_agent_for_conversation(...)   ← identical to above
        await agent.respond(question_id, answers)      ← reuses persisted pending
        finally: mark status (paused_hitl again if new pending, else completed)
```

The respond turn **reuses the original `run_id`** — the agent's paused turn
and the resumed turn are logically one turn, split by a pause. This keeps
the Redis event stream key stable, so the frontend's existing SSE consumer
(already attached to that `run_id`) just keeps reading without any
stream-switch protocol.

### Component changes

#### 1. CheckpointedChannel

`run_manager.py` constructs `CheckpointedChannel(checkpointer=cp,
thread_id=conversation_id, default_timeout=None)` in section 6 (sandbox
middleware setup). This requires `cp` to exist at that point, so
`async with init_checkpointer() as cp:` moves up to wrap sections 6–8 and the
agent run. The remaining code inside the existing `async with` block stays
where it is, just indented under a larger scope.

`InMemoryChannel` use in `run_manager.py` is removed entirely.

#### 2. Remove 180-second timeout

`SandboxMiddleware.approve(...)` drops the `timeout=180.0` argument. The
channel's `default_timeout=None` propagates, so the await never times out.
The `except HitlTimedOut` branch in that function stays — it's cheap defence
and keeps the policy-deny path testable — but with no caller passing a
timeout it becomes effectively dead code. We document this in a comment.

The two ban points checked above are the only places in cubeplex that pass a
timeout to HITL today; grep confirms.

**SandboxMiddleware's confirm/deny/allow logic itself does not change.**
On a respond turn, cubepi replays the suspended tool batch through the
same `before_tool_call`. The middleware evaluates `command_rules` again
against the current org policy:

- If the rule is still `confirm` (the common case), `channel.approve()`
  hits the channel's resume short-circuit (`channel.py` lines 194–203),
  returns the persisted answer immediately without emitting a new
  `HitlRequestEvent`, and the tool proceeds.
- If the rule changed to `allow` or `deny` between pause and respond,
  the middleware short-circuits before calling `channel.approve()` at
  all — the new rule wins (we honor current policy, not policy at
  request time). This leaves the persisted pending dangling in DB; the
  respond-path finally block handles cleanup (see §6 "Dangling pending
  cleanup").

Rationale: an admin who tightens policy mid-pause expects the new
policy to apply immediately, not for in-flight approvals to bypass it.

#### 3. Auto-detach on `HitlRequestEvent`

`_run_cubepi_path` already subscribes a listener `_on_event(evt, _signal)`.
We extend it: when `isinstance(evt, HitlRequestEvent)`, schedule
`asyncio.create_task(agent.detach())` (capture `agent` from the outer scope).

Timing is safe:

- cubepi emits `HitlRequestEvent` from inside
  `_BaseChannel._on_pending_set`, after the pending is persisted but before
  the `asyncio.wait` on `_future`.
- The listener runs synchronously on the same loop; `create_task` schedules
  detach for the next tick.
- By that next tick the channel is awaiting `_future`. `detach()` calls
  `self._channel._future.set_exception(HitlDetached())`; the wait wakes up;
  cubepi's `_run_loop` catches `HitlDetached` silently and returns. The
  pending in DB is **not** cleared (`_on_pending_cleared` has explicit
  `if isinstance(exc, HitlDetached): return`).
- `agent.prompt()` returns to `_run_cubepi_path`, finally block runs.

`AgentSuspendedEvent` is emitted by `detach()` immediately before the
exception is set. We translate it through the existing SSE conversion path
(or drop it if not useful to the frontend — TBD during implementation; either
way it's not load-bearing for correctness).

#### 4. New run status `paused_hitl`

The run-status enum currently in use (`running` / `completed` / `cancelled` /
`errored` / `stale`) gains one value: `paused_hitl`. **Redis status is a
hint for routing and stale-sweeping; the source of truth for "can this
conversation resume" is `cubepi_threads.pending_request` in Postgres.**
Any code that gates on "is this conversation paused" checks the DB pending,
not the Redis status. The Redis status exists to:

- Tell the stale-sweeper to leave the row alone (no freshness expectation).
- Let `start_run` reject a duplicate brand-new turn fast, without a DB
  round-trip in the common case.

In `_run_cubepi_path`'s terminal block, after `agent.prompt()` returns, we
check:

```python
pending = await agent.load_pending_hitl_request()
if pending is not None:
    final_status = "paused_hitl"
else:
    final_status = "completed"   # or errored/cancelled per exception path
```

The Redis active-run record stays present with `status="paused_hitl"`.
`start_run`'s existing conflict check (`existing.status == "running"`)
extends to also reject when **either** the Redis status is
`running`/`paused_hitl`, **or** the DB has a non-null `pending_request` for
this conversation (the DB check catches the worker-crash case where the
Redis row was never transitioned to `paused_hitl`). The user must answer
the pending, explicitly cancel it (existing cancel path), or abort.

`get_active_run` and other lookups treat `paused_hitl` as "active but not
running"; specifically:

- `cancel_run` on a paused conversation goes through a paused-aware branch
  (`cancel_paused_run`). It MUST first win the `claim_resume` CAS (see §5)
  so it can't race a concurrent answer submit or a duplicate cancel; on
  `already_running` it returns 409. Once it owns the row, it builds a
  transient agent via the same factory as the respond path and calls
  `agent.abort_pending(reason)`. `abort_pending` clears
  `cubepi_threads.pending_request`, appends synthetic deny tool_results,
  and writes the terminal stop_reason message; we then mark the run
  `cancelled` and unlock the conversation. This is the only cancellation
  path that *must* rebuild — abort needs the agent to flush closing
  messages, not just clear DB rows.
- `steer_run` does **not** work for a paused run (no live agent in-process);
  the route returns the existing "no live agent" response.
- The stale-run sweeper (`status == 'running'` AND last_event_at old)
  ignores `paused_hitl` — paused runs don't have a freshness expectation.

#### 5. `resume_run_with_answer` (replaces dispatched answer delivery)

The existing `dispatch_ask_user_answer` / `dispatch_hitl_answer` methods —
plus their `_publish_control` + `_handle_control` Redis-pubsub plumbing for
the `ask_user_answer` and `hitl_answer` message types — are removed. They
existed only to forward an answer from the worker that received the POST to
the worker that owned the in-process channel; we no longer need that, since
the channel is durable in Postgres.

In their place:

```python
async def resume_run_with_answer(
    self,
    *,
    conversation_id: str,
    run_id: str,                              # original (paused) run_id, reused
    question_id: str,
    answer: ApproveAnswer | dict[str, Any],   # confirm or ask_user shape
    ctx: RunContext,
) -> str:
    # 1. authoritative check: pending = await cp.load_pending_request(cid)
    #    if None → 404 (no pending; race with another submit or cancel)
    #    if pending.question_id != question_id → 409 stale_answer
    # 2. atomic single-flight claim (see "Resume claim" below):
    #    claim_resume(run_id) — CAS Redis active-run row from
    #    {paused_hitl | stale | missing} → running WITH this worker's
    #    claim token. Returns ok|already_running|conflict.
    #    - already_running → 409 resume_in_flight
    #    - conflict (someone else just claimed) → 409 resume_in_flight
    # 3. asyncio.create_task(self._execute_respond_run(run_id=run_id, ...))
    # 4. return run_id
```

**Resume claim — single-flight guarantee**

Redis layout (existing, see `backend/cubeplex/streams/run_events.py`):

- `conversation_active_run:{cid}` — string whose VALUE is the currently
  active run_id for the conversation. The key existing AND pointing at a
  given run_id is the conversation-level lock.
- `run_meta:{run_id}` — hash with fields `status`, `last_event_id`,
  `last_event_at`, `started_at`, …; we add `claim_token`.

The `claim_resume` Lua script (KEYS[1]=active_key, KEYS[2]=meta_key,
ARGV[1]=expected_run_id, ARGV[2]=new_claim_token, ARGV[3]=ttl_seconds)
does in one round-trip:

1. `current = GET active_key`. If `current ~= expected_run_id`, return
   `conflict` — the active lock has moved to a different run_id, this
   submit is for a stale view.
2. `status = HGET meta_key 'status'`. If `status == 'running'`, return
   `already_running` — another resume/cancel/respond is in flight on
   this same run.
3. If `status` is in `{paused_hitl, stale}` OR the meta_key is missing
   (TTL expired): `HSET meta_key status='running' claim_token=ARGV[2]
   last_event_at=<now>`; `EXPIRE active_key ARGV[3]`;
   `EXPIRE meta_key ARGV[3]`. Return `ok` with the new token.
4. Any other status (`completed`, `cancelled`, `errored`): return
   `conflict` — the conversation has moved on, this submit is stale.

The meta_key-missing branch (step 3 second arm) is what keeps the
"long pause exceeds TTL" case from stranding the conversation: the
Redis row aged out, but DB pending still exists, so resume rebuilds
both the meta row and the active-key pointer in one CAS. The caller of
`claim_resume` always passes the run_id it read from `pending_hitl`
(which itself was derived from DB pending — §7), so even after the
active key has expired we know which run_id to re-claim.

`_execute_respond_run` carries the claim token; its finally block writes
the terminal status only if the token still matches (otherwise some other
flow has taken over the row and we don't clobber). Same mechanism — same
lua script, generalized — replaces `start_run`'s today-only "row doesn't
exist" check; `start_run` calls it with the prompt-path inputs and refuses
the brand-new turn if status is anything but a clean prior-completed state.

Cancel-on-paused (§4) uses the same single-flight gate: `cancel_paused_run`
calls `claim_resume(run_id)`; on `ok`, it owns the row and proceeds to
build the transient agent and call `abort_pending`; on `already_running`,
it returns 409 — the caller already has a respond or cancel in flight, and
the user-side UX surfaces "operation in progress, try again in a moment".
Cancel during a live (not paused) respond turn falls through to the
existing `cancel_run` path (`task.cancel()` on `self._tasks[run_id]`),
which is unchanged.

`_execute_respond_run` mirrors `_execute_run` but:

- Uses the **original `run_id`** — same Redis stream key, same SSE consumer
  on the frontend, no stream-switch protocol needed.
- Calls `_run_cubepi_respond_path` instead of `_run_cubepi_path`, which
  shares the agent-build factory (see §6), then awaits
  `agent.respond(question_id=question_id, answer=answer)` instead of
  `agent.prompt(user_msg)`.
- The terminal-status branch is the same as the prompt path: if a new
  pending is set during respond, status becomes `paused_hitl` again
  (same run_id, just paused on a follow-up question); otherwise
  `completed`. The frontend's SSE consumer keeps reading the same stream
  through all of it.

`POST /api/v1/ws/{ws}/conversations/{cid}/runs/{rid}/ask-user-answer` and
the parallel `hitl-answer` route both go through `resume_run_with_answer`,
passing the URL's `rid` as the resumed run_id. Recovery from the
worker-crash case (DB pending present, Redis active-run row missing /
stale / running-not-paused-hitl) is automatic: step 1 above checks DB; if
pending exists, step 2 reconstructs / refreshes the Redis row before
spawning the respond task.

#### 6. Agent build factory

`_run_cubepi_path` today is ~700 lines: it builds the provider, the eight
middleware sections, the tool list, the channel, the agent; then registers
the SSE listener and drives the prompt; then handles citation flush and
post-run bookkeeping.

We extract the "build" half into a method:

```python
async def _build_agent_for_conversation(
    self,
    *,
    ctx: RunContext,
    conversation_id: str,
    cp: PostgresCheckpointer,
    sandbox: Any | None,
    skill_catalog: Any | None,
    catalog_session: Any | None,
    effective_system_prompt: str,
    extra_ref: dict[str, Any],
    # any other inputs the build step needs
) -> tuple[Agent, list[Any], HitlChannel]:
    # returns (agent, all_tools, sandbox_hitl_channel)
```

Both `_run_cubepi_path` and `_run_cubepi_respond_path` call this, then
diverge:

- prompt path: registers `_on_event`, computes memory snapshot + attachments,
  builds user message, calls `agent.prompt(user_msg)`.
- respond path: registers `_on_event` (same shape — also auto-detaches if a
  follow-up pending is hit), calls `agent.respond(question_id, answer)`.

**Dangling pending cleanup (respond path only)**

cubepi clears `cubepi_threads.pending_request` in
`_BaseChannel._on_pending_cleared`, which runs in `_await_answer`'s
finally. On respond, the answered HITL is delivered via the channel's
resume short-circuit only if `before_tool_call` actually calls
`channel.approve()` / `channel.ask()`. If middleware short-circuits the
tool call instead (e.g. sandbox `command_rules` changed from `confirm`
to `allow`/`deny` between pause and respond — see §2), the channel
never enters `_await_answer` for that question, the finally never
runs, and the persisted pending stays in DB even though the turn moved
past it.

The respond path's terminal block reconciles this explicitly:

```python
final_pending = await cp.load_pending_request(conversation_id)
if final_pending is None:
    final_status = "completed"
elif final_pending.question_id == answered_question_id:
    # Dangling: middleware short-circuited the resumed tool call.
    # The agent has moved past it; the persisted pending is stale.
    await cp.save_pending_request(conversation_id, None)
    # Emit synthetic resolved event so any connected frontend removes
    # the still-rendered confirm/ask card. Same event shape as the
    # normal answer flow uses; frontend's existing handler clears
    # pendingConfirmMap / pendingAskMap on this event.
    await publish_stream_event(
        SandboxConfirmResolvedEvent(
            question_id=answered_question_id,
            tool_call_id=final_pending.payload.tool_call_id,  # if approve kind
            decision="policy_overridden",  # new outcome value
            reason="org sandbox policy changed during pause",
        )
        if final_pending.payload.kind == "approve"
        else AskUserResolvedEvent(
            question_id=answered_question_id,
            outcome="policy_overridden",
        )
    )
    final_status = "completed"
else:
    # A new HITL pending was emitted during respond (auto-detach fired).
    final_status = "paused_hitl"
```

Frontend additions for this path:

- `SandboxConfirmResolvedEvent.decision` accepts `"policy_overridden"`
  alongside the existing `"approve"`/`"deny"`. The
  `messageStore.applyStreamEvent` handler removes the entry from
  `pendingConfirmMap` for any of these decisions; only the optional
  reason string varies. (Same for the ask_user counterpart.)
- The frontend treats `"policy_overridden"` like a backend-side cancel:
  the card disappears, a small grey "Skipped — org sandbox policy
  changed" note replaces it inline so the user understands what
  happened.

The prompt-path terminal block uses a stricter check: any pending after
prompt is a real new pending (the prompt path has no prior
`answered_question_id` to dangle against). However we DO need to guard
against the rare case where a conversation was loaded with a stale
pending row that no current pending await is keyed to — a prior
implementation bug, an operator partial cleanup, etc. The helper:

```python
def _classify_terminal_status(
    final_pending: HitlRequest | None,
    answered_question_id: str | None,  # None on prompt path
    saw_hitl_request_event_this_turn: bool,  # tracked by _on_event
) -> tuple[str, bool]:  # (status, should_clear_pending)
    if final_pending is None:
        return ("completed", False)
    if not saw_hitl_request_event_this_turn:
        # Pending in DB but this turn never emitted a HitlRequestEvent →
        # leftover from prior session. Clear and treat as completed.
        return ("completed", True)
    if answered_question_id is not None and final_pending.question_id == answered_question_id:
        # Respond path: dangling because middleware short-circuited.
        return ("completed", True)
    # Genuine new pending — this turn emitted a HitlRequestEvent that
    # the auto-detach hook converted into a real pause.
    return ("paused_hitl", False)
```

The `saw_hitl_request_event_this_turn` flag is set by the existing
`_on_event` listener (which already routes HitlRequestEvent for the
auto-detach hook); reusing it here means the prompt-path stale-pending
case is detected without a second mechanism.

`effective_system_prompt` for the respond path: we recompute it the same way
the prompt path does, against the **current** conversation state. The
checkpointer holds the message history; system prompt is part of the
build-time inputs, not the message history, so it gets a fresh derivation
each time. This is intentional — if a workspace setting changed between
prompt and respond, the respond turn picks up the new prompt. Cache discipline
(see [prompt-cache-discipline.md](../../../backend/docs/prompt-cache-discipline.md))
is unaffected because the cache prefix is keyed off the persisted message
sequence, not the assistant-side build inputs.

#### 7. Frontend

- The Redis stream replay already re-emits `ask_user_request` /
  `sandbox_confirm_request` events on SSE reconnect, so when the user
  reloads the page during a paused conversation the AskUserCard appears
  without server-side changes.
- The card already handles `timeout_seconds === null` (no countdown).
- One new piece: the conversation API must surface the pending state so
  the composer can disable "send new message" while there's an unresolved
  pending. We extend the conversation/status response with `pending_hitl`
  (null when none), derived from `cubepi_threads.pending_request` (DB) —
  **not** from the Redis active-run status, so it's correct even after a
  mid-pause worker crash. The composer is grayed out with hover text
  "Answer the pending question above first."

  `pending_hitl` payload schema, **frozen** (kind-tagged union):

  ```ts
  type PendingHitl =
    | {
        run_id: string                  // the paused run; reused on respond
        question_id: string             // for stale-check on submit
        kind: "ask_user"
        requested_at: string            // ISO8601 for the card timestamp
        questions: Array<{              // exact shape AskUserCard renders
          key: string
          prompt: string
          options?: Array<{ label: string; value: string; description?: string }>
          multi_select: boolean
          required: boolean
        }>
      }
    | {
        run_id: string
        question_id: string
        kind: "sandbox_confirm"
        requested_at: string
        tool_call_id: string            // for the resolved event correlation
        command: string                 // SandboxConfirmCard body
        matched_pattern: string         // the rule pattern that triggered confirm
      }
  ```

  Both shapes are sufficient for the cold-start renderer to rebuild the
  card without any SSE stream replay. They mirror the existing
  `ask_user_request` / `sandbox_confirm_request` event payloads, so the
  serializer can be shared.
- Because the respond turn reuses the original `run_id`, the SSE consumer
  attached to that run after the original prompt keeps receiving events
  through the pause and into the resumed turn. No stream-switch protocol;
  no `consumeRunStream` rewiring on submit; no bootstrap reload.
- If the user tries to send anyway, the start-run endpoint returns 409 with
  `code: "paused_hitl"` and the frontend surfaces it as a toast.

### Frontend recovery on reload (cold-start case)

The Redis stream has a TTL. If the user is gone long enough that the stream
expires, on reconnect we'd miss the `ask_user_request` event. Mitigation:

The conversation status response (above) carries `pending_hitl` with enough
data to rebuild the card client-side. The frontend, on conversation load,
checks `pending_hitl`; if non-null AND the run's events stream doesn't
contain a fresh `ask_user_request`, the frontend reconstructs an
`AskUserCard` from the `pending_hitl` payload alone. This is the
"belt-and-braces" path for very stale conversations; ordinary reconnects
will hit the replay first and never use this fallback.

## Data model changes

- `cubepi_threads.pending_request` (JSONB) — **already exists** via alembic
  revision `fdcc495b3704`. No DDL.
- Run-status enum / column — currently a free-form string in Redis
  (`hset ... status`). Adding `paused_hitl` is a code change only; no DDL.
- No new tables.

## Error handling

| Scenario | Behavior |
|---|---|
| `agent.detach()` race with `HitlAborted` (user clicked Cancel between request emit and detach scheduling) | cubepi's `detach()` already no-ops if `_future` is done; safe. |
| Answer arrives but DB pending mismatch (`question_id` doesn't match) | cubepi raises `HitlStaleAnswer`; route returns 409 with `code: "stale_answer"`. |
| Answer arrives but no pending in DB | cubepi raises `HitlNoPendingRequest`; route returns 404. |
| Build step inside respond path fails (e.g. model config gone) | Mark respond-run `errored`; conversation stays in `paused_hitl` so a retry is possible after the operator fixes config. Pending NOT cleared. |
| Worker dies mid-prompt, between cubepi persisting pending and the terminal block writing `paused_hitl` | Redis row stays `running`, then ages into `stale`. DB pending is intact. When the user submits an answer, `resume_run_with_answer`'s step-1 DB check still finds the pending and resumes; step 2 refreshes the Redis row to `running` regardless of its prior state. No operator intervention needed. |
| Worker dies mid-respond | Same recovery as above. The respond run_id's Redis row is left `running` or aged to `stale`; pending stays in DB (cubepi only clears it on a clean turn completion); next submit re-enters via the same path. Idempotency is on the user side — they hit "submit" again. |
| Conversation has stale `paused_hitl` but pending is gone (e.g. operator cleared the DB row out-of-band) | Conversation-status `pending_hitl` field returns null (it's derived from DB). Composer unlocks. start-run succeeds normally. The orphan Redis row gets cleaned by the stale sweeper or overwritten by the next start_run. |
| Two tabs (or two backend workers) submit the same answer concurrently | First arrival wins the `claim_resume` CAS and spawns the respond task; second arrival sees `already_running` and returns 409 `resume_in_flight`. UX: a tame "Submitting…" → "Already submitted" toast on the second tab. The respond turn runs exactly once. |
| Cancel arrives while a respond turn is in flight | `cancel_paused_run` calls `claim_resume`, sees `already_running`, returns 409. The user can retry once the respond turn either completes or pauses again (at which point `claim_resume` succeeds and cancel takes the row). |
| Stale claim from a dead worker | The Redis row carries a TTL refreshed by run-event writes (existing); a dead claim ages into `stale` exactly like any other crashed run, and the next claim attempt succeeds. |
| Admin tightens `command_rules` (`confirm → deny`) between pause and respond | Respond replays the tool batch; `SandboxMiddleware.before_tool_call` re-evaluates and blocks. The persisted pending becomes dangling; respond's terminal block detects `final_pending.question_id == answered_question_id` and clears it. Turn ends `completed`. |
| Admin loosens `command_rules` (`confirm → allow`) between pause and respond | Same as above: middleware lets the command run without HITL; pending dangles; terminal block cleans up. |

## Testing

Unit:

- `_on_event` auto-detach: build a fake agent + channel, emit
  `HitlRequestEvent` through the listener, assert `agent.detach()` was
  scheduled and the prompt task ends.
- Terminal status branch: after `agent.prompt` returns, with persisted
  pending → status `paused_hitl`; with no pending → status `completed`.
- `_build_agent_for_conversation` produces the same middleware list / tool
  list / channel kind as the existing inline build, snapshot-asserted on a
  fixture conversation.
- `start_run` rejects with 409 / `paused_hitl` when conversation already has
  `paused_hitl` status.
- `resume_run_with_answer` schema validation and run-id allocation.

Integration:

- The existing sandbox confirm gate tests
  (`tests/unit/test_sandbox_confirm_gate.py`) update: the call no longer
  carries `timeout=180.0`. The `HitlTimedOut → blocked-as-deny` test stays
  (defence; we still preserve that behavior on the off-chance a caller
  passes a timeout).

E2E:

- Happy path single-worker: start run → agent emits `ask_user_request` →
  SSE consumer sees it → POST answer → SSE consumer sees follow-up events
  → conversation completes.
- Detach + reconnect: same, but kill the SSE connection between request and
  answer; wait >180 s (smoke that the cap is gone); reopen, POST answer,
  conversation completes.
- Cross-worker (gated on test infra supporting two workers): run starts on
  worker A; SSE drops; A's run task exits with `paused_hitl`; POST hits
  worker B; B's `resume_run_with_answer` rebuilds the agent and completes
  the conversation; verify message history is intact.
- Page reload during paused: open conversation in a second tab after a
  pending request; `pending_hitl` is in the status response; composer is
  disabled; card renders from either replay or status fallback; answering
  works.

## Rollout

- Single PR for backend + frontend. The change set crosses both layers
  (status enum surface, composer gating) and isn't usefully splittable —
  shipping the backend alone with the frontend unaware of `paused_hitl`
  would leave the composer in a broken "send another message" state during
  a paused conversation.
- No feature flag; the cubeplex project hasn't shipped publicly and we
  generally cut over cleanly (per CLAUDE.md).
- Code-level migration: no DB DDL. Existing in-flight runs at deploy time
  are not specifically migrated; the running worker finishes them under the
  old code (the new worker picks up only future runs).

## Open questions (track during implementation, not blocking design approval)

- Whether to expose `AgentSuspendedEvent` to the frontend as its own SSE
  event type or fold it into the existing `ask_user_request` /
  `sandbox_confirm_request` events. Probably fold-in — the frontend doesn't
  need to know "the worker released."
<!-- Both R1/R2/R3 open questions resolved during review. Add new ones here
as they arise during implementation. -->
