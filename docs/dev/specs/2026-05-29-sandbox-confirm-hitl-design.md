# Sandbox `confirm` rules → real human-in-the-loop (HITL)

- **Status**: Draft, awaiting review
- **Date**: 2026-05-29
- **Branch / worktree**: `feat/sandbox-confirm-hitl` / `.worktrees/feat/sandbox-confirm-hitl`
- **Author**: brainstormed with the user, drafted by Claude

## 1. Motivation

The sandbox command-policy work (#152, plan
`2026-05-27-sandbox-scoping-policy.md`) shipped three command-rule actions:
`deny`, `confirm`, `allow`. The data model and admin UI already let an org
save `confirm` rules, but at runtime `confirm` **degrades to `deny`** with a
distinct message ("requires confirmation; not yet supported in this
deployment"). The reason was purely an upstream gap: cubepi had no
human-in-the-loop primitive, so there was nothing to pause the tool call on
and resume it from.

cubepi has since shipped a full HITL channel (`dev/specs/2026-05-28-hitl-channel.md`
in the cubepi repo): a `HitlChannel` protocol, `ApprovalPolicyMiddleware`,
per-run `Agent(channel=...)` wiring, and `HitlRequestEvent` / `HitlAnswerEvent`
on the agent event stream. This spec wires that primitive into cubeplex so a
`confirm` rule does what its name says: pause the `execute` tool, surface the
command to the user, and run or skip it based on their answer.

This is the cubeplex half of the work the original spec flagged as
"External follow-up — cubepi HITL (OQ-1/OQ-2)". The acceptance criteria
stated there carry over verbatim:

- confirmation blocks **only the tool call**, not the whole run;
- the approval timeout is **180 seconds**, after which the call is treated as
  `deny` and an audit trace is written;
- the sandbox TTL clock is **not** paused while waiting for approval.

## 2. Scope

**In scope (this worktree, one feature, split into PRs by coupling):**

- Bump the cubepi pin to a revision that includes the HITL channel.
- cubepi checkpointer schema migration v1→v2 (forced by the pin bump).
- Move the command-policy gate out of the `execute` tool body and into a
  `before_tool_call` hook on `SandboxMiddleware`, backed by a per-run
  `InMemoryChannel`.
- Carry the human's answer from the browser back to the running worker over
  the **existing Redis control channel** (same mechanism as steer/cancel).
- A new scoped HTTP endpoint that publishes the answer onto that channel.
- Translate `HitlRequestEvent` / `HitlAnswerEvent` into two new SSE event
  types.
- A frontend inline confirm card in the chat stream (approve / deny +
  countdown).

**Out of scope (explicit, deferred):**

- **Edit path.** `ApproveAnswer.decision == "edit"` is rejected in v1 (the
  middleware raises so a mis-wired client can't silently mutate a command).
  Edit needs its own UX design.
- **Cross-process durable resume.** See §6 — cubeplex runs are single-process
  and not resumable across worker death today; HITL does not change that.
- **Command rules on `write_file` / `edit_file`.** v1 covers `execute` only
  (carries over OQ-10 from the original spec).
- **Per-policy configurable timeout.** Fixed 180s in v1.

## 3. Current state (what we're changing)

`backend/cubeplex/middleware/sandbox.py::_make_execute_tool._execute`
evaluates command rules inside the tool body and, on `confirm`, returns an
`is_error=True` "not yet supported" result (lines ~154–180). That whole branch
goes away.

`backend/cubeplex/streams/run_manager.py::_run_cubepi_path` builds
`SandboxMiddleware` (around line 1273) and the `Agent` (via
`create_cubeplex_agent`). Neither knows about a channel today. The Redis
control channel (`{key_prefix}:control`) already carries `steer` / `cancel`
messages handled in `_handle_control`.

`backend/cubeplex/agents/stream.py::convert_agent_event_to_sse` silently drops
every AgentEvent it doesn't recognise — including the new HITL events until we
add cases.

HITL is wired **nowhere** in cubeplex yet; this is a greenfield integration on
top of an upstream primitive.

## 4. Backend design

### 4.1 cubepi pin bump + schema migration (one commit)

Bump `backend/pyproject.toml` `cubepi` git rev to a HITL-bearing revision and
`uv lock --upgrade-package cubepi`. The pin bump raises cubepi's
`EXPECTED_SCHEMA_VERSION` from 1 to 2; `cubepi.PostgresCheckpointer` refuses to
start against a v1 database, so the migration ships in the **same commit** —
there is no runnable intermediate state otherwise.

**Migration is autogenerate + one hand-added line, not hand-written.** This
was checked against the code, correcting an earlier assumption that the whole
file had to be hand-written:

- The new column `pending_request JSONB NULL` lives on `cubepi_threads`
  (`cubepi/checkpointer/postgres/models.py`), a plain non-partitioned table.
- `backend/alembic/env.py` already includes `cubepi_metadata` in
  `target_metadata`, and `cubepi_threads` is **not** in the autogenerate
  exclude set (`_CHECKPOINT_TABLES` excludes `cubepi_messages`,
  `cubepi_schema_version`, and `cubepi_messages_p*` — but not
  `cubepi_threads`). This is deliberate upstream design: evolvable columns sit
  on the regular table so host autogenerate can capture them.

So `alembic revision --autogenerate -m "cubepi v1->v2 pending_request"`
produces the `ADD COLUMN pending_request` op on its own.

The **only** part autogenerate cannot produce is the schema-version bump: that
is a *data* row in `cubepi_schema_version` (integer `version` 1→2), not a
structural change. autogenerate diffs structure and never reads or writes
table data. So we hand-add a single line to the generated file:

```python
def upgrade() -> None:
    # ... autogen'd: op.add_column("cubepi_threads", ...pending_request...)
    from cubepi.checkpointer.postgres.alembic_helpers import write_schema_version_op
    op.execute(write_schema_version_op())   # version 1 -> 2

def downgrade() -> None:
    # ... autogen'd: op.drop_column("cubepi_threads", "pending_request")
    op.execute(
        "DELETE FROM cubepi_schema_version WHERE version <> 1; "
        "INSERT INTO cubepi_schema_version (version) VALUES (1) ON CONFLICT DO NOTHING;"
    )
```

This keeps the hand-edited surface to the one line autogenerate is
architecturally unable to reach (precedent: `555c11215b57` already hand-adds
`write_schema_version_op()` and the partition DDL for the same reason).

Implementation step 1 runs the autogen and pastes the produced diff for review
before the hand-add — confirming it emits *only* `ADD COLUMN` and deletes
nothing else.

Current alembic head to chain onto: `28c4c57516f6`.

> Note: v1 uses `InMemoryChannel` (§4.3), so `pending_request` stays NULL at
> runtime — cubepi only writes it from `CheckpointedChannel`. The migration is
> still mandatory (version check) and the column is a free forward-investment:
> if cubeplex later makes whole runs durable, the storage is already there with
> no second schema change.

### 4.2 `SandboxMiddleware` owns the policy gate (`before_tool_call`)

Remove rule evaluation from the `execute` tool body; the tool returns to being
a pure executor. Add a `before_tool_call` hook on `SandboxMiddleware` itself —
the cubepi `Middleware` protocol lets one middleware provide both tools and
the hook, so command rules, the channel, and the tools stay co-located instead
of stacking a separate `ApprovalPolicyMiddleware` outside.

`SandboxMiddleware.__init__` gains `channel: HitlChannel | None = None`. The
hook follows the same contract as cubepi's `ApprovalPolicyMiddleware`:

```python
async def before_tool_call(self, ctx, *, signal=None):
    if ctx.tool_call.name != "execute":      # v1: execute only (OQ-10)
        return None
    if self.channel is None or not self.command_rules:
        return None
    command = ctx.args.command
    action, pattern = evaluate_command(command, self.command_rules)
    if action == "allow":
        return None
    if action == "deny":
        return BeforeToolCallResult(
            block=True,
            reason=f"command blocked by org policy: {pattern}",
            deny_reason=pattern,
            hitl_trace={"decision": "policy_deny", "pattern": pattern},
        )
    # action == "confirm": pause on the channel
    try:
        answer = await self.channel.approve(
            tool_name="execute",
            tool_call_id=ctx.tool_call.id,
            args={"command": command},
            details={"matched_pattern": pattern, "command": command},
            timeout=180.0,
            signal=signal,
        )
    except HitlTimedOut:
        return BeforeToolCallResult(
            block=True, reason="approval timed out (180s); command not run",
            deny_reason="approval_timeout",
            hitl_trace={"decision": "timed_out"},
        )
    except HitlCancelled as exc:
        return BeforeToolCallResult(
            block=True, reason=f"cancelled: {exc.reason}",
            deny_reason=f"cancelled: {exc.reason}",
            hitl_trace={"decision": "cancelled", "reason": exc.reason},
        )
    if answer.decision == "approve":
        return None
    if answer.decision == "deny":
        return BeforeToolCallResult(
            block=True, reason=answer.reason or "denied by user",
            deny_reason=answer.reason or "denied by user",
            hitl_trace={"decision": "human_deny", "reason": answer.reason},
        )
    # decision == "edit": out of scope in v1 — reject loudly
    raise ValueError("edit decision not supported for sandbox confirm v1")
```

Because the gate is in `before_tool_call`, the sandbox's `execute()` body never
runs for a denied/timed-out/cancelled call — so no sandbox side effects occur
and the sandbox TTL (wall-clock) is untouched while waiting. Acceptance
criterion "TTL not paused" holds structurally.

### 4.3 Per-run channel in `run_manager`

In `_run_cubepi_path`, when a sandbox is present, create one
`InMemoryChannel(default_timeout=180.0)` per run, pass it to both
`SandboxMiddleware(channel=...)` and `create_cubeplex_agent(channel=...)`
(`Agent(channel=...)` — single channel per agent, cubepi's design), and
register it in a new `self._hitl_channels: dict[str, HitlChannel]` keyed by
`run_id`, alongside the existing `self._agents`. The `finally` teardown pops it
just like `self._agents.pop(run_id, None)`.

`create_cubeplex_agent` gains a `channel` kwarg forwarded to `Agent`.

### 4.4 Answer transport: existing Redis control channel

The human's answer reaches the still-blocked worker over
`{key_prefix}:control` — the same pub/sub used by steer/cancel. Add a
`hitl_answer` branch to `_handle_control`:

```python
if msg["type"] == "hitl_answer":
    channel = self._hitl_channels.get(msg["run_id"])
    if channel is None:
        return                       # run already finished; drop
    await channel.answer(
        msg["tool_call_id"],         # == question_id for approve (cubepi §4.1)
        ApproveAnswer(decision=msg["decision"], reason=msg.get("reason")),
    )
```

Same-process fast path (cubepi spec §5.4): the worker holding the agent is the
one subscribed to its control channel, so `answer()` resolves the in-flight
future directly. No resume, no checkpoint read.

### 4.5 HTTP endpoint

New scoped handler (workspace-scoped, its own file/handler per the
scope-isolation rule):

```
POST /api/v1/ws/{workspace_id}/conversations/{conversation_id}/sandbox-confirm/{tool_call_id}
body: {"decision": "approve" | "deny", "reason"?: str}
```

Implementation: auth + resolve the conversation's active `run_id` (existing
active-run lookup) + `redis.publish(control_channel, {"type": "hitl_answer",
"run_id": ..., "tool_call_id": ..., "decision": ..., "reason": ...})`. The
endpoint only publishes; it does not wait for the outcome (the outcome arrives
on the SSE stream as `sandbox_confirm_resolved`).

### 4.6 SSE event translation

In `convert_agent_event_to_sse`, add cases that fire **only** for approve-kind
HITL on the `execute` tool (so future HITL sources like `ask_user` don't
collide):

- `HitlRequestEvent` → `sandbox_confirm_request`
  `{tool_call_id, command, matched_pattern, timeout_seconds, created_at}`
- `HitlAnswerEvent` → `sandbox_confirm_resolved`
  `{tool_call_id, outcome: "approved"|"denied"|"timed_out"|"cancelled", reason?}`

## 5. Frontend design

- `frontend/packages/core/src/types/sse-events.ts` — add
  `sandbox_confirm_request` and `sandbox_confirm_resolved` event types.
- `frontend/packages/web/src/lib/api/sandbox-confirm.ts` —
  `postSandboxConfirm(workspaceId, conversationId, toolCallId, body)`.
- `frontend/packages/web/src/components/chat/SandboxConfirmCard.tsx` (new) —
  inline card showing the command and matched pattern; a countdown computed
  from `timeout_seconds` + `created_at`; approve / deny buttons; an optional
  reason field on deny; input box disabled while pending (same treatment as a
  pending cancel/steer); after `sandbox_confirm_resolved` the card becomes a
  non-interactive status strip (approved / denied / timed out / cancelled).
- `ChatStream` inserts the card at the matching timeline position on
  `sandbox_confirm_request` and updates it on `sandbox_confirm_resolved`.

Visual language matches existing tool-call cards; amber/red accent to signal
risk.

## 6. Why `InMemoryChannel` (design rationale)

The schema migration (§4.1) and the channel choice are **independent**. The
v1→v2 migration is forced by the version bump and must run regardless of which
channel we use; "avoiding a schema change" is **not** a reason to pick
`InMemoryChannel` (it can't be avoided).

`CheckpointedChannel`'s only added capability is **cross-process durable
resume**: a worker that dies mid-wait can be picked up by another process via
`agent.respond()`. That capability is hollow in cubeplex today:

1. **Answers return to the same process anyway.** The worker running
   `agent.prompt()` is the one subscribed to its Redis control channel; the
   approve/deny lands in that same process while it is blocked in
   `before_tool_call`. Cubepi's same-process fast path (§5.4) applies — no
   resume needed.
2. **cubeplex runs are not cross-process resumable at all.** The agent loop is
   a single in-process `agent.prompt()` run to completion. If the worker dies,
   the whole run dies (SSE breaks, `_agents` entry gone); cubeplex does not
   resume dead runs. Adding `CheckpointedChannel` for just the confirm wait
   would make one 180-second window durable inside an otherwise
   non-resumable run — a false sense of durability for a lot of
   detach()/respond()/HTTP-triggered-resume machinery.
3. **The wait is short.** 180s cap, human-clicks-a-button scale. Same-process
   blocking is the natural fit; durable resume targets hours/days waits.

`InMemoryChannel` is chosen because it matches cubeplex's existing
"single-process, run-not-resumable" model. The correct path to
`CheckpointedChannel` is to first make whole runs resumable (a separate, large
effort); the migration shipped here pre-provisions the storage for that future
without a second schema change.

## 7. Testing

- **Unit**: `evaluate_command` (exists) + new `SandboxMiddleware.before_tool_call`
  three-branch tests (allow/deny/confirm) with a mock channel; confirm covers
  approve, deny, timeout, cancel, and the edit-rejection.
- **E2E** (E2E-priority per project rules): configure a real `confirm` rule,
  run the agent on a matching `execute`, and assert:
  1. SSE emits `sandbox_confirm_request`;
  2. POST approve → command actually runs; SSE emits
     `sandbox_confirm_resolved(outcome="approved")`;
  3. POST deny → tool_result `is_error=True`, command not run;
  4. no answer → after the (test-shortened) timeout, deny-shaped tool_result.
  Timeout E2E injects a short `default_timeout` (~1.5s) instead of 180s.
- The existing confirm-as-deny E2E (from the #152 batch) changes semantics —
  its old "not supported" assertion is replaced, not kept.

## 8. PR split (decided before pushing)

1. **Backend**: pin bump + migration + middleware gate + channel + control
   branch + HTTP endpoint + SSE translation + unit/E2E. One concern: "make
   `confirm` actually pause and resume on the backend."
2. **Frontend**: SSE types + API client + `SandboxConfirmCard` + ChatStream
   wiring. One concern: "let the user answer a confirm."

Coupling note: the frontend PR depends on the backend SSE/endpoint contract,
so backend merges first; the contract (event payloads, endpoint shape) is
frozen in §4.5/§4.6 so the frontend PR can be written in parallel against it.

## 9. Acceptance criteria (from the original OQ-1/OQ-2 follow-up)

- [ ] A saved `confirm` rule pauses the `execute` tool call (not the whole run).
- [ ] Approve runs the command; deny returns an error tool_result without
      running it.
- [ ] 180s timeout → deny-shaped tool_result + `timed_out` hitl_trace.
- [ ] Sandbox TTL clock is not paused during the wait.
- [ ] cubepi checkpointer starts cleanly on the migrated (v2) schema.
