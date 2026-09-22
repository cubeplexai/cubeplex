# Agent Runtime

**Read before modifying:** agent construction, middleware, tool assembly, run streaming, checkpoints, or event types.

CubePlex runs agents with [CubeLoop](https://github.com/cubeplexai/cubeloop). The authoritative execution path is `cubeplex/streams/run_manager.py`; `cubeplex/agents/graph.py` is the small factory that creates a `cubeloop.Agent` from the already-resolved model, tools, middleware, checkpointer, and thread ID.

## Runtime flow

1. A workspace-scoped conversation route creates or resumes a run through `RunManager`.
2. `RunManager` resolves the workspace's model configuration, prompt, enabled tools, and sandbox configuration.
3. It builds the CubeLoop middleware stack and passes it with the stable tool order to `create_cubeplex_agent`.
4. `execution_adapter.execute_session` attaches the required event consumer before calling the public CubeLoop `ExecutionSession.execute` API. `RunManager` maps agent events to CubePlex events and writes them to the Redis run stream consumed by SSE clients.
5. CubeLoop returns an explicit `ExecutionResult`; the host maps its outcome and durable checkpoint facts to completed, cancelled, failed, or paused run state. Host event-delivery diagnostics are recorded separately so they do not replace failed or cancelled outcomes. A checkpoint-durable HITL suspension remains resumable when projection fails; for a same-question suspension, the host repairs the answered pending request before surfacing the delivery error.
6. On the prompt path, CubePlex drains projected events and emits the sole public `DoneEvent` before marking Redis run metadata terminal, so the SSE consumer cannot exit before the terminal event is stored.
7. On the respond path, CubePlex atomically leases the resume claim before durable pending cleanup, so stale recovery cannot hand the same answer to another worker while PostgreSQL and Redis are being reconciled. The lease expires at the normal stale-run threshold, allowing recovery if the finalizing worker dies. A successful stale transition revokes the expired claim before releasing the active-run slot, so the old worker can no longer commit terminal metadata. Startup recovery performs its database and scheduler repairs only after that stale transition succeeds. CubePlex then drains projected events, commits terminal metadata under a live claim, and emits `DoneEvent`. CubeLoop's internal `ExecutionFinished` event is never projected to SSE.

`RunManager` owns background execution and Redis persistence. Redis holds active-run coordination, control signals, event streams, and their expiry; it is not a replacement for the durable conversation state in Postgres.

### Durable execution admission integration

The lifecycle implementation adds an internal `admission_id` input to `start_run`.
For an admitted user input, RunManager checks the original scope, actor, conversation,
run ID, and request fingerprint before claiming Redis. It builds the model chain
from the admission's frozen model selection and reasoning, using the current provider
configuration rather than resolving the default again. A recorded start request or
finished run returns the original run ID without executing it again, even after Redis
history expires. An uncertain start is not permission to replay.
Revoked inputs likewise return their original run binding without touching a newer
pending question. Admitted starts never implicitly cancel a pending HITL request;
answering or stopping that request belongs to its explicit control path, even when
the Redis pause keys have expired.

`run_start_requested_at` records the persisted start claim; `run_started_at` records
the claimed worker entering execution. The worker rechecks generation closure and run Stop before
entry, and the main agent and its subagents recheck authority at model and tool
boundaries. These checks do not rewrite messages or the cached prompt prefix.
`run_finished_at` records owner teardown, not successful task completion. A durable
HITL pause remains unfinished even though the current worker has detached.
If Stop wins before worker entry, the rejected worker records a cancellation and
finishes cleanup without setting `run_started_at`. A receipt that says the worker
never entered cannot finish an attempt whose entry was already recorded.

Admitted prompt workers put the same attempt token in Redis and their durable
start claim. Model/tool admission and the required Session event consumer check
that token and the conversation's active slot. Redis event append, metadata,
heartbeat, error-pointer, and slot-release writes also check ownership atomically;
a pre-write read alone cannot protect against a takeover between the two calls.
Terminal checkpoint cleanup uses the existing finalization lease to exclude stale
recovery while the owner is committing. A superseded worker stops new work and
does not finalize its replacement or repair the replacement's pending history.
For admitted terminal runs, publishing terminal metadata does not release this
lease: it remains through checkpoint/input cleanup and active-slot release.
The durable finish receipt is written after slot release and event-data expiry,
so a failed release cannot report cleanup complete. A fast next send cannot
steal the slot before the owner releases it. HITL pauses
release the finalization lease so an immediate answer is not delayed; crashed
terminal owners remain recoverable after the bounded lease expires.
Unresolved durable receipts remain available for reconciliation, not automatic replay.
Startup recovery applies the same stale-heartbeat threshold as inline recovery and
compares the observed heartbeat atomically before revoking an owner. Starting another
replica alone must not cancel a live worker or stamp its checkpoint run complete.

HITL answer entry points recognize the run's durable admission without
accepting a replacement source identity. They check the original generation and
the original actor's current access. Only that actor may answer or approve;
conversation participation is not delegation to use another person's credentials.
The run continues under its original model/reasoning snapshot and trigger. The Redis resume
attempt gets a fresh claim token, while the durable initial start token and timestamps
stay unchanged. Worker entry and model/tool boundaries recheck authority. A second
HITL pause leaves the receipt unfinished; only owned teardown with no matching
pending question records its end. Question, conversation and run must all match,
even after Redis expires. Pre-cutover runs without an admission still follow the
existing path and must be covered by the migration gate before activation.

If a terminal Redis write commits but its response is lost, the owner reads back
the terminal fact instead of overwriting it with an error or losing cleanup rights.
Cancellation after terminal commit likewise preserves that outcome. Idempotent
start retries that cannot execute return the original binding even if its model
has since been removed; a genuinely unstarted input still validates availability.

This is an integration step, not the lifecycle cutover. The Web model-run entrance now
creates this admission before calling RunManager and reuses the bound run on retry. The
Web install shortcut uses the same source identity but records a direct-execution result:
the installation and result commit together, and stable checkpoint message IDs let a
retry repair a lost checkpoint acknowledgement without installing or appending twice.
Web steering for both running and HITL-paused runs enters the durable steering queue;
the run's original actor is rechecked before a new input can affect later model or tool
work. Human IM messages use the durable webhook receipt as their stable source identity.
The IM worker freezes the resolved actor, text, attachments, and model selection before
calling RunManager; reclaiming the queue row therefore reuses the same admission and run
instead of spending twice. Synthetic IM rows from schedules and triggers are not labeled
as user input: they retain their schedule/trigger occurrence identity for the automation
admission step. The final IM handoff binds its admission while holding the connector
account lock. Disabling or deleting that connector cancels queued admissions that have
not acquired run ownership, while already-started runs keep their history. The scheduler
now freezes each occurrence when it is claimed, binds one
conversation, run ID, and automatic admission, and reuses those bindings across busy,
IM, and stale-claim retries. A fixed target's generation is frozen at claim time, so an
older occurrence cannot reopen a conversation after Stop All; an occurrence claimed
after Stop All may open a new generation. IM handoff remains `queued` until the admitted
run reports an outcome and is not treated as proof that execution started. Pausing or
deleting the schedule cancels only occurrences that have not acquired a run start token;
accepted runs keep their history. Trigger ingest now commits a validated, immutable
execution snapshot before returning 202. A lease-based worker reclaims pending or
abandoned events and binds one conversation, run ID, and automatic admission across
retries. Manual dead-letter replay increments the event execution revision, so it is a new
authorized occurrence rather than renewed authority for the old admission. Disabling or
soft-deleting a trigger cancels only unstarted admissions and queued IM handoffs; accepted
runs and the source proof remain intact. The new task coordinator stays inactive until the
remaining entrance and data-migration gates are complete.

The paused-run branch of main Stop no longer synthesizes an answer or starts a
model. For admitted work it durably stops the named run, then claims
the paused run for cleanup. The cleanup holds the conversation lock, checks its
Redis attempt, repairs only that run's unanswered tool calls, clears only the
matching question, and emits a cancelled Done before terminal metadata. Queued
guidance is cancelled; checkpoint-proven input stays injected. A lost terminal
reply does not prevent the finish receipt and slot release. The 202 response confirms
the persisted stop intent, not evidence that cleanup already finished.
The application starts a dedicated scan of persisted Stop/revocation intents
in bounded pages and stops that scan before draining workers. The paused-run recovery branch
can reclaim an expired Redis pause or a failed cleanup attempt after its lease
expires. It uses the persisted Stop proof, not renewed execution authority from
the original user, and never starts a model. Current owners and newer questions
remain protected. If the pending question was already cleared, recovery requires
the matching CubeLoop run's completed checkpoint before continuing. A cleanup-only
claim can retain existing completed/cancelled/errored metadata; it never makes an
ordinary answer eligible to resume a terminal run or emits a second terminal reply
for a known result, and never reoccupies an already-released active slot.
Slot release and finish-receipt failures can then be retried without another model
call. Prompt and HITL completion now persist an attempt-fenced terminal outcome in
the admission before Redis cleanup. Recovery may reconcile a matching Redis terminal
fact or rebuild cleanup-only state after Redis expiry; it never infers an outcome from
a completed checkpoint alone. Unstarted stopped runs and leftover terminal HITL
questions follow the same durable proof and cleanup rules. This completes the planned
C2a recovery paths, while later entrance and cutover work remains gated separately.

The control service now separates `run_stop_requested_at` from admission revocation
and generation closure. Run Stop cancels only that run's unhanded foreground tasks
and user inputs; handoff and Stop serialize under the conversation/admission locks.
Already handed-off tasks retain their execution and result-notification authority.
New reservations and late handoffs cannot escape a run Stop. Deadline expiry is
different: its final result may still be handed to the background for delivery.
Cancelled foreground tasks need only execution/log cleanup, not background handoff.
The coordinator stops scanning them once execution and log recovery are settled;
it does not invent foreground delivery evidence to make them disappear.
Steering records carry source kind and generation for targeted cancellation;
unsettled input claims retain their owner and require checkpoint reconciliation.
The workspace-scoped `POST /conversations/{id}/cancel` now requires `run_id`,
and the separate `POST /conversations/{id}/stop-all` requires `execution_generation`.
Both return 202 with the same target, `accepted`, and `cleanup_pending` only after
committing the stop transaction. Signalling is best effort and bounded; it does not
choose whichever run happens to be active or report cleanup complete from a publish
acknowledgement. An accepted run without a start receipt still requires reconciliation.
Input-source assignment, the remaining durable restart cases, and frontend callers remain
staged integration work before the coordinated cutover; old bodyless Cancel calls
are rejected rather than allowed to bypass the stable-target contract.
Stop signals are idempotent within an execution attempt. The worker enters a
cleanup-only phase before finalization awaits; repeated local or received signals
leave that phase running, including a paused-run cancellation worker. This marker
belongs to the concrete asyncio task, so an older attempt cannot protect a newer
worker accidentally. A cancelled HTTP/control waiter does not propagate another
cancellation into teardown. Forced process shutdown can still cancel cleanup;
durable recovery remains necessary for that case.

Resource removal uses the same durable control facts. Conversation deletion and
topic archive close each current generation in the database transaction that hides
the resource, with `conversation_deleted` as the task stop reason. Workspace,
organization and topic-participant removal revoke only admissions owned by the actor
who lost access; they do not close a shared conversation generation or stop another
actor's work. Topic removal first recalculates conversation access, so an independent
conversation-level grant is preserved. Re-granting access never clears an old
admission's revocation. Routes commit these facts before bounded runtime signalling
and return `cleanup_pending` when process, input or notification reconciliation
remains.

Workspace and account hard deletion are two-phase. A durable
`deletion_pending_at` fence blocks new admissions before stop facts are committed.
The endpoint retains the workspace or user and returns `deleted=false` while any
run, task, command log, input or notice is unsettled. The same endpoint is the retry
surface; no coordinator deletes the resource on its own. Once cleanup is terminal,
the retry removes wakes, source events, task events, command details, tasks and
admissions before the original workspace, conversation or user rows.

Automatic memory reflection for admitted work waits until its original worker has
finished cleanup. It does not keep the run or active slot open. Each model/tool
boundary requires the original attempt's completed Redis metadata, its finished
durable receipt, the same open generation, and the original actor's current access.
Stop, deletion, revoked access, or missing/replaced completion proof prevents new
reflection work. Normal completion alone does not revoke this existing best-effort
postprocessing; cancelled, errored, paused, or unfinished runs cannot authorize it.
These checks do not change prompt bytes or retry a lost reflection.

Reflection memory tools additionally check the original execution identity in
the memory operation's own transaction. Authority locks stay held through reads,
deduplication, capacity eviction and the final save/update; the memory repository
flushes without committing when the caller owns this transaction. Stop and the
memory commit therefore have a single database order, with rollback covering the
whole operation. Authority rows use `FOR NO KEY UPDATE` to serialize revocation
while remaining compatible with unrelated billing/memory foreign-key checks.
No model request runs while these locks are held.
Topic access additionally locks the topic archive gate and the actor's matching
topic/conversation participation rows before locking the conversation. A concurrent
move to another topic or an unreserved new grant requires fresh admission; removing
a participant or archiving a topic cannot commit between authorization and memory.

Admitted memory consolidation uses the same completed-attempt and authority checks.
It waits outside the main run for owner cleanup, rechecks before its model call,
and validates inside the transaction applying the complete extract/merge/archive
batch. Memory operations do not commit individually; a failure rolls the batch
back, and source memory records retain the triggering run ID. Stop or revocation
while the model is pending prevents any later batch write. Normal completion still
permits personal and workspace consolidation without keeping the run open.

## Durable state and human input

`cubeplex/agents/checkpointer.py` wraps CubeLoop's `PostgresCheckpointer` over a shared asyncpg pool. Conversation ID is the agent thread ID, so checkpoints and resumable human-in-the-loop requests survive a process restart. The app opens the shared checkpointer during its lifespan and closes it on shutdown.

The run manager uses CubeLoop's `CheckpointedChannel` for `ask_user` and sandbox confirmation requests. Keep checkpoint writes and the channel on the same thread and run IDs: a paused request must be resumed or cancelled through the same durable state.

Before an attempt, the host restores messages and middleware extra state together with `ExecutionSession.load_checkpoint`. Middleware receives the live `ExecutionSession.state_context`; do not read or write private `Agent._state` or `Agent._extra` fields. Prompt and respond remain separate admission paths, and a respond worker must hold the matching Redis claim token before it can finalize the run.

Live steering enters through `ExecutionSession.submit_input`. Steers received while the host is still preparing an owned attempt wait in a bounded host buffer and are submitted when `AgentStart` opens Session admission. An unmatched cancellation creates a bounded tombstone before or after that admission boundary; it remains until the matching steer arrives or the attempt ends. Durable steering is acknowledged only from an `InputCommitted` event whose durability is `checkpoint`; an in-memory admission receipt alone does not make a database row injected. A checkpoint acknowledgement retries transient database failures and propagates an exhausted retry budget to the required Session consumer. The acknowledgement carries its immutable workspace scope so later pause or finalization reconciliation can repair the durable row even when HITL auto-detach has removed the live Session registration. The next Session registration reconciles claims still owned by that coordinator: checkpoint-proven inputs become injected, while absent inputs return to the queue for the new Session. Each Agent, preparation buffer, and durable delivery registration keeps the claim token of its concrete attempt. Local delivery, pub/sub admission, cancellation, and PostgreSQL claiming or submission all compare that attempt token with the distributed claim. A superseded Session therefore forwards direct requests and stays silent on broadcasts even if it remains open; only the replacement can act on the input. A closed current registration, or the current task during its no-Agent teardown window, can still reject admission authoritatively. Cancellation uses `cancel_input`, while hard run cancellation remains task cancellation.

## Event projection limits

The Session consumer is required, is attached before execution, and has a 256-event capacity. Every projected event is limited to 1 MiB. Host publication has a 5-second deadline and the enclosing CubeLoop delivery deadline is 6 seconds. A timeout, oversized event, or Redis publication error fails the required consumer, stops new agent work, and prevents a successful `DoneEvent`. `ToolResultLimitMiddleware` truncates tool-result text to 20,000 characters (except `load_skill`) in `after_tool_call`, before that event is published, so a runaway `execute` / fetch / MCP payload does not hit the 1 MiB ceiling.

Subagent and citation events use a separate 64-event queue. The same 1 MiB per-event limit makes its maximum queued payload budget 64 MiB, excluding small Python container overhead. Producers wait at most 5 seconds to enqueue; teardown waits at most 5 seconds to enqueue the sentinel and 5 seconds to drain. The success path does not suppress a drain failure. Text deltas are not coalesced and tool events are not dropped to make room.

## Middleware and tools

The middleware stack is assembled per run in `RunManager._build_cubeloop_agent`. Depending on enabled features, it includes CubePlex middleware for attachments, artifacts, citations, memory, sandboxing, costs, and timestamps, plus CubeLoop middleware for compaction, subagents, todo lists, and the tool-result size cap. The stack supplies or transforms tools as well as requests and responses.

Tool and middleware order is intentional: it affects the stable prompt prefix and provider prompt caching. Add or reorder a tool only after reading [prompt-cache-discipline.md](prompt-cache-discipline.md). Middleware-provided tools are removed from the explicit tool list before `cubeloop.Agent` receives it, because CubeLoop adds them itself; passing both copies produces duplicate tool names.

## Infrastructure

| Concern                                    | Shipping implementation                                     |
| ------------------------------------------ | ----------------------------------------------------------- |
| HTTP API                                   | FastAPI                                                     |
| Agent runtime                              | CubeLoop `Agent` + public `ExecutionSession`                 |
| Run coordination and SSE event persistence | Redis + `RunManager`                                        |
| Durable checkpoints and application data   | PostgreSQL + CubeLoop Postgres checkpointer                   |
| Files and artifacts                        | S3-compatible object storage                                |
| Conversation search                        | PostgreSQL PGroonga lexical search with pgvector embeddings |
| Sandboxes                                  | OpenSandbox when enabled                                    |

## Where to make changes

- Agent construction: `cubeplex/agents/graph.py`
- Run lifecycle and middleware composition: `cubeplex/streams/run_manager.py`
- CubeLoop Session boundary and projection limits: `cubeplex/streams/execution_adapter.py`
- Checkpoint pool lifecycle: `cubeplex/agents/checkpointer.py` and `cubeplex/api/app.py`
- CubePlex middleware: `cubeplex/middleware/`
- SSE event storage and replay: `cubeplex/streams/run_events.py`
- API event schemas: `cubeplex/agents/schemas.py`

Keep routes and streaming adapters focused on transport. Put runtime behavior in the agent, middleware, run manager, or checkpointer layer that owns it.
