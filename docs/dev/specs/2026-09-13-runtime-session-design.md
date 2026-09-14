# CubeLoop execution lifecycle and CubePlex runtime boundaries

- Date: 2026-09-13
- Status: Proposed. This specification and its implementation plans have not been implemented.
- Goal: Consolidate reusable execution lifecycle behavior in CubeLoop so CubePlex can drive runs through public interfaces while retaining distributed coordination, tenant isolation, and product protocols.
- Baseline: CubePlex `d5b0f03ac04e62762b1918f4c4ee3ee4df2e315c`; CubeLoop `bdf88a08855b1bf3626eaaccc371f965d8b62906`. The CubePlex dependency lock references this CubeLoop commit.
- Constraints: [Agent Runtime](../../../backend/docs/agent-system-design.md), [Prompt Cache](../../../backend/docs/prompt-cache-discipline.md), [Testing](../../testing.md), [HITL](2026-06-02-hitl-checkpointed-respond-design.md), and [durable steering](2026-08-12-hitl-queued-steering-design.md). Earlier specifications describe their original design decisions; current behavior is established by the implementation.

## 1. Current behavior and motivation

CubePlex already provides a hosted runtime around CubeLoop. The refactor clarifies responsibilities within that existing architecture.

| Capability | Current implementation | Refactoring treatment |
|---|---|---|
| Background execution and control entry points | `backend/cubeplex/streams/run_manager.py` | Retain RunManager; reduce dependencies on Agent internals |
| Active-run exclusion, TTL, heartbeat, SSE replay | Redis Lua/CAS/Streams in `streams/run_events.py` | Retain in CubePlex |
| Durable HITL and competing resumes | CubeLoop CheckpointedChannel; CubePlex `streams/hitl_resume.py` | CubeLoop owns execution results; CubePlex owns claim tokens and status projection |
| Durable steering during pauses | `streams/steering_delivery.py`, `repositories/steering_message.py` | Retain Postgres claims and reconciliation; consume public input receipts |
| Ordinary live steering | RunManager control messages and CubeLoop in-memory queues | Preserve existing non-durable behavior |
| Durable history, run completion, fork | CubeLoop `checkpointer/base.py` and its backends | Reuse the existing protocol; no additional ThreadStore |
| Execution outcomes and cancellation cleanup | CubeLoop `agent/_outcome.py`, `Agent._run_with_lifecycle` | Consolidate a public execution interface over existing behavior |
| Memory and model snapshots | CubePlex `middleware/memory.py`, `llm/snapshot.py` | Retain product snapshots; add a read-only request view |
| Event conversion and subagent presentation | `agents/stream.py`, `agents/schemas.py`, `streams/subagent_events.py` | Preserve the UI protocol; unify consumption of execution results |

The concrete maintenance problem is access across internal boundaries and duplicated decisions. RunManager writes `agent._state.messages` and binds `agent._extra`; prompt and respond paths repeat resource setup and cleanup. CubeLoop already has `RunOutcome`, but the host still interprets pending requests, observed events, and error_message to determine status. These observations do not establish a runtime defect. The objective is to give correctness rules a single owner and testable public interfaces.

## 2. Approaches considered

1. Move RunManager into CubeLoop. This provides broad reuse but brings Redis, workspaces, SSE, IM, and product middleware into the framework. Rejected.
2. Split large CubePlex modules only. This improves readability with limited local risk, but other hosts would still need to understand private Agent state and infer outcomes. Useful as an implementation technique.
3. Incrementally consolidate the CubeLoop execution lifecycle and adapt CubePlex to it. Selected. Establish outcome, suspension, input, and event contracts first, replace callers next, and address subagent extensions separately.

Execution continues to use a plain async loop. This design adds no graph executor, standalone daemon, or message-broker framework.

## 3. Terminology and ownership

- Thread: A durable conversation. CubePlex conversation_id maps to CubeLoop thread_id.
- Run: One logical user request, potentially spanning multiple HITL pauses and resumes with the same run_id.
- Execution attempt: One worker invocation of prompt or respond for a run. Each invocation gets a new attempt_id for correlation. It does not replace Redis claim_token or confer authorization.
- Turn: The existing CubeLoop boundary around a model response and its associated tool processing. Existing TurnStart/TurnEnd semantics remain unchanged.
- TurnExecutionContext: The resolved request and tool-execution bindings used within a Turn. It is context data, not a separate execution entity or lifecycle.
- ExecutionSession: An Agent-owned execution lifecycle object that can drive sequential attempts. It is not a persistent cross-worker session.

A Run contains Turns; an execution attempt identifies a worker invocation, not another sampling boundary. A Turn may include transport retries or model fallback before producing its response. These do not create a separate Step entity, step_id, or additional TurnStart/TurnEnd events.

CubeLoop owns in-memory execution state, checkpoint writes, and tool-result pairing. CubePlex owns distributed admission, claim fencing, authorization, Redis projections, and business completion callbacks. Session does not repeat the host's distributed claim operation.

## 4. Public ExecutionSession contract

The following signatures describe proposed interfaces, not existing APIs. They belong in `cubeloop/session/`. Agent owns one instance; existing Agent.prompt/respond/abort entry points share that implementation. Ordinary library users can continue using Agent.

- `await session.execute(request: ExecutionRequest) -> ExecutionResult`
- `session.request_cancel() -> None`: An idempotent signal. The host retains responsibility for escalation to task.cancel after its timeout.
- `await session.request_detach() -> None`: Supported only at existing durable HITL safe points, not in the middle of arbitrary tools.
- `session.submit_input(input: InputEnvelope) -> InputReceipt`
- `session.cancel_input(input_id: str) -> InputReceipt`
- `await session.load_checkpoint() -> CheckpointData | None`: Idle-only, mutually exclusive with execute. Install both messages and extra before execution can start; restore extra in place into the live mapping. The returned checkpoint may be read-only or copied. A load failure must not leave partially installed state. An empty message list does not mean extra can be skipped.
- `session.state_context`: A typed accessor to the same live extra mapping, or a stable write-through proxy. Middleware reads and writes must reach checkpointed state; do not return a detached copy or replace the mapping during restore.

ExecutionRequest is a discriminated union: prompt carries messages, respond carries question_id and answer, and continue uses existing safe continuation behavior. Shared fields are run_id and attempt_id. Explicit cancellation of pending HITL retains the existing cancel-as-answer/abort_pending entry points and remains distinct from a hard stop. Plan 01 maps all current public methods to the shared lifecycle.

Overlapping execute raises ExecutionBusy, a RuntimeError subclass, before accepting the request or changing live state. It produces neither ExecutionResult nor ExecutionFinished for the rejected request. Agent execution entry points use the same admission rule. This deliberately changes respond from waiting on the current run lock to rejecting overlap; prompt/resume already reject overlap. Plan 01 records the old behavior, and Plan 02 tests and documents the new contract. CubePlex still uses its existing distributed admission and HTTP conflict responses.

ExecutionResult contains run_id, attempt_id, outcome, pending_request, error, checkpoint_committed, and history_consistent. Its outcome is `completed | suspended | cancelled | failed | incomplete`. This is a new result type; conversion from the private RunOutcome must account for the actual exit cause:

- Existing complete becomes completed only after tool-result completeness checks and any required completion marker succeed.
- Existing suspended becomes suspended with the committed pending request.
- Existing incomplete becomes incomplete.
- Existing abandoned is classified by its cause as explicit cancellation or failure, never assumed successful.
- A provider error becomes failed even when the provider does not propagate an exception.

The lifecycle records the exit cause when observed, rather than reconstructing it from the final cancellation flag. Provider stop_reason=error, state.error_message, or an execution exception records failure; an accepted cancellation or task cancellation records cancellation unless a failure was already recorded. A later cancellation cannot relabel an earlier failure. Errors directly caused by cancelling work remain cancellation; an independent fatal error remains failure. Unexplained abandoned/aborted exits fail closed, with a diagnostic, rather than being guessed to be user cancellation. Hosts consume the public result, not these private discriminators.

Finalization validates the result after recording that cause. Missing tool pairs or unconfirmed required checkpoint writes yield incomplete, with history_consistent/checkpoint_committed reflecting what was verified; other fatal finalization errors yield failed. Retain the original cause in structured error details. A settled result is not reclassified by late cancellation.

ExecutionResult also carries delivery_errors, a list of typed diagnostics containing consumer identity, event seq, and reason=timeout/closed/error. These report notification delivery separately from execution outcome. The internal ExecutionFinished carries the settled execution facts; the execute return may additionally report failure to deliver that final notification. Hosts use the returned diagnostics without changing or re-emitting the settled execution facts.

No checkpoint tables are added. Existing completed_at retains its run-history completion meaning; business success is determined by execution results and host state.

## 5. Lifecycle and finalization

| Current attempt state | Input or event | Result |
|---|---|---|
| idle | execute | running after admission |
| running/cancelling/finalizing | execute | Raise ExecutionBusy without accepting another attempt |
| running | request_cancel | cancelling; prevent new model requests or tool calls from starting |
| running | Durable HITL and detach | Commit pending state and existing results, then suspend |
| running | Model finishes with no pending input | Check tool pairing and persistence, then complete |
| running/cancelling | Fatal provider/tool error | failed; ordinary tool business errors can still be returned to the model |
| cancelling | Cleanup succeeds | cancelled |
| running/cancelling | Finalization | finalizing; reject new execution until result validation and finalization finish |
| finalizing | Checkpoint or cleanup failure | incomplete for unconfirmed persistence or pairing; otherwise failed |
| finished attempt | Repeated cancellation or late input | Do not reopen the attempt; return its settled result or a closed receipt |

Each attempt emits at most one ExecutionFinished. Suspension ends an attempt but leaves the logical Run open. A run can have multiple suspended attempts followed by a completed attempt. The UI DoneEvent closes the current SSE execution segment; it does not necessarily mean the logical Run succeeded.

Cancellation stops new model/tool work and preserves results already produced. For each persisted tool call still missing a result, retain the existing _complete_cancelled_tool_calls behavior: append a synthetic cancellation result with the originating call/run identity. Never replace a real result. Failed pairing or persistence is visible in the result rather than hidden behind successful cancellation.

Framework finalization order: record model/tool facts → pair outstanding calls → required checkpoint writes and completion marker → settle ExecutionResult and issue the internal ExecutionFinished notification → return from execute. The host then completes its projection and resource cleanup. ExecutionFinished is not an SSE DoneEvent. Section 8 specifies the distinct prompt/respond ordering; there is no single CAS-before-Done rule. Redis and Postgres do not share an atomic transaction; this design does not promise end-to-end exactly-once execution.

If checkpointing succeeds but Redis publication fails, retain durable facts and report the projection failure. Do not rerun tools to republish events. Recovery retains CubePlex's existing bootstrap and stale-repair behavior. Hard crashes do not automatically resume arbitrary tools, and cancellation cannot undo actions already sent to external systems.

## 6. Event and input ordering

Reuse existing CubeLoop AgentEvent payloads within an execution envelope containing run_id, attempt_id, monotonically increasing attempt-local seq, and optional turn_id/tool_call_id. Provider retries and fallback are distinguished by tracing spans, not a new execution identity in this envelope. Correlation fields do not enter model messages or the cache prefix. Existing text, tool, and HITL payloads remain intact.

Add two public lifecycle notifications: InputCommitted and ExecutionFinished. InputCommitted is emitted only after the corresponding UserMessage checkpoint append succeeds. Sessions without a checkpointer report an in-memory commit with durability=memory. ExecutionFinished has one producer in the shared lifecycle; hosts must not interpret ordinary AgentEndEvent as durable success.

InputEnvelope contains input_id, message, and mode=steer/follow_up. InputReceipt distinguishes queued/committed/cancelled/closed. Queued does not mean durably accepted. CubePlex still commits paused steering to Postgres before acknowledging its HTTP request.

Keep steering and follow-up logically separate, initially reusing their existing queues. Steering is consumed at existing safe points after tool results or final text; follow-up is consumed only after the inner turn loop ends. Each retains its own one-at-a-time/all policy. Agent.steer, cancel_steer, and follow_up use the corresponding queue; a common submission API must not merge their drain semantics.

Resume ordering remains: completed sibling tool results → resumed HITL tool result → steering consumption at a safe point → next model request. Input insertion must preserve tool-call/result pairing. Duplicate durable deliveries are reconciled using steer_id in persisted messages. Queue delivery alone cannot acknowledge injected status before checkpointing. Claim expiry and crash reconciliation remain CubePlex responsibilities.

Separate required execution consumers from best-effort observers and host transport. A required-consumer exception or delivery timeout before settlement fails execution; observer failures do not change the result. Checkpoint writes are awaited by the lifecycle, not delegated to an SSE subscriber. A single sequencer assigns event seq for parallel tool completions. Attach consumers before execute; use a bounded queue with a default capacity of 256 events. Coalesce only adjacent compatible text deltas, preserving content and sequence ranges, never across control/tool boundaries. Coalescing is an optimization, not the overflow policy.

Queue admission, consumer delivery, and shutdown waits must have finite configurable timeouts and remain cancellation-aware. A stopped required consumer must wake blocked producers. Preserve tool results and committed input facts through the checkpoint path; never acknowledge undelivered notifications as delivered. If final notification delivery fails after result settlement, return the settled result with an explicit delivery diagnostic rather than attempting to emit another ExecutionFinished into the blocked queue. Hosts reconcile committed inputs from checkpoints.

CubePlex owns Redis publication and downstream buffering. Do not couple mandatory framework progress directly to Redis XADD, or claim bounded memory by forwarding into an unbounded queue. Plan 05 must specify capacities/byte limits and finite delivery/drain deadlines for the adapter path, including an oversized-event policy. Host publication failure stops new execution if still active and is reported separately from already-committed execution facts. Ownership-fenced heartbeat and cancellation processing must remain runnable during transport stalls; heartbeat renewal stops after the bounded failure/cleanup window, so a wedged publisher cannot retain ownership indefinitely. Default limits and deadlines must be recorded and validated against provider timeouts and stale-run thresholds before integration ships.

## 7. TurnExecutionContext

Preserve middleware order. After transform_context, transform_system_prompt, and convert_to_llm finish, capture a TurnExecutionContext with read-only request views and retained tool-execution bindings. Provider-specific encoding and cache markers remain provider-adapter responsibilities.

Fields include turn_id, run_id, attempt_id, model identity and reasoning configuration, system prompt, message views, ordered tool descriptions, and routing bindings. An optional host policy_revision is for auditing only. Freeze content and schemas without deep-copying sockets, model clients, or tool executors.

Same-model transport retries reuse the captured context. Model fallback captures a new context with the fallback model's resolved configuration under the same turn_id and attempt_id; it does not mutate the previous context. Each tool call retains the concrete context and binding that produced it, rather than looking up the latest context by turn_id. Context replacement does not start or end a Turn; existing loop boundaries remain authoritative.

Deferred tool discovery within a Turn uses controlled context-local registry extension to append bindings. Resolved calls retain their concrete binding; the next Turn captures the expanded catalog. Reject same-name replacement. Freezing must preserve existing expand/dispatch behavior.

The context keeps advertised and invoked tool bindings consistent; it does not freeze authorization. CubePlex still checks current authorization and sandbox policy before executing side effects. Revoked permissions cannot be bypassed through an old context, and new permissions do not automatically expand its tools.

Read-only views should share immutable messages rather than copy the entire history on each capture. Nested schemas and metadata require defensive freezing. Traces record identifiers and summaries by default, without adding credentials or full prompts. This phase does not persist full TurnExecutionContext or promise byte-identical restoration of every configuration after restart. Existing historical memory_snapshot reconstruction guarantees remain required.

## 8. CubePlex migration

RunManager keeps its public entry points. A host adapter combines product configuration, Agent, Session, and resource cleanup for an attempt. Prompt and respond share result handling, but the resume claim-token CAS must not be replaced by an unconditional update_run_meta.

Outcome mapping: completed → completed; suspended → paused_hitl; cancelled → cancelled; failed/incomplete → errored. Stale is a host liveness judgment and stays outside CubeLoop. Cancel-as-answer may continue execution and complete normally; the label of a UI button does not determine the outcome. Plan 01 verifies these mappings against existing RunMeta values.

This table does not replace leftover-pending repair in classify_terminal_status. After successful execution, the current owner clears stale persisted pending when no HITL request was emitted during this attempt, or when the pending question_id is still the answered question_id. Preserve the existing resolved-card handling. A genuine new follow-up pending request remains paused_hitl. Validate request identity and ownership before cleanup; never clear a newer owner's pending request. Failed/incomplete attempts must not become completed through this repair, and cleanup failure must not be reported as successful host finalization.

Only the host finalizer emits the existing SSE DoneEvent, after execute returns and accepted SSE/subagent/citation events have drained, usage aggregation has completed, and data.paused is set from the authoritative status. Neither streamed ExecutionFinished nor AgentEndEvent maps directly to Done. A drain timeout is an explicit projection failure, not a silent successful flush; bound teardown and do not claim complete delivery. Preserve the current prompt order (append Done, then update terminal Redis status) and respond order (claim-fenced terminal update, then derive paused and append Done). Do not add an unconditional update after respond or duplicate Done production. IM and scheduled-task handling remains host-owned.

Preserve HTTP paths, response codes, SSE names and fields, run_id, Redis keys, and the paused-steering data model. New correlation fields initially remain internal to execution and tracing, with no frontend migration requirement. Preserve workspace/org route separation. IM and scheduled-task business callbacks stay in CubePlex.

Upgrade sequence: publish the single CubeLoop implementation → pin the new CubePlex dependency and lock → switch callers → remove replaced private-state access. Agent public entry points remain normal library APIs. Do not maintain parallel legacy/new engines or permanent migration flags. Each PR must run independently with its declared dependency version.

## 9. Deferred work and exclusions

Child execution identity, cancellation, and result delivery form a separate follow-up phase in Plan 06. Existing subagent live events, tool-sharing restrictions, and model selection must remain intact. Cross-process durable mailboxes, arbitrary workflow replay, a new ThreadStore, persisted full TurnExecutionContext, capability-based tool scheduler replacement, and a generic permission engine are outside the core migration.

The default Session must not import Redis, FastAPI, or CubePlex. Lightweight callers can keep using Agent. CubeLoop's existing optional Postgres checkpointer remains available.

## 10. Acceptance criteria and delivery sequence

| Requirement | Observable acceptance | Plans |
|---|---|---|
| R1 Preserve behavior and terminology | Same run_id, existing turn meaning, and HTTP/SSE contracts | 01, 05 |
| R2 One execution lifecycle | Prompt/respond/cancel share execution state; no private-state injection | 02, 05 |
| R3 Durable exits | Checkpoint failure cannot report success; a suspended run resumes in a new instance | 02, 03, 05 |
| R4 Input ordering | Queue acknowledgment follows checkpointing; cancellation/redelivery cannot duplicate input | 03, 05 |
| R5 Request consistency | Stable tool/message views, working deferred tools, unchanged cache prefix | 04, 05 |
| R6 Preserve host coordination | CAS losers do not execute; old owners cannot overwrite new state; scopes remain isolated | 05 |
| R7 Lightweight reuse | In-memory Session works; Agent and Session share one engine | 02 |
| R8 Child execution boundaries | Late child results cannot reopen completed answers; cancellation and cleanup have explicit owners | 06 |

Dependencies: 01 → 02 → 03 → 04 → 05. Plan 06 follows 05 independently. Plan 01 establishes contracts and regression baselines; 02–04 each deliver one CubeLoop concern; 05 integrates CubePlex. Plan 06 can be deferred without blocking core acceptance.

Each plan names files, interfaces, tests, and exit criteria. Implementation uses isolated worktrees in the owning repository and follows that repository's testing and release process. Database/API tests use real Postgres, Redis, and FastAPI with only the external model boundary substituted. Never compare two side-effecting engines through production shadow execution. Deterministic fixtures can compare model requests, checkpoints, and SSE projections before release.

- [Plan 01: Contracts and regression baseline](../plans/2026-09-13-runtime-session-01-contracts.md)
- [Plan 02: Execution lifecycle](../plans/2026-09-13-runtime-session-02-lifecycle.md)
- [Plan 03: Input, HITL, and event commit ordering](../plans/2026-09-13-runtime-session-03-input-events.md)
- [Plan 04: Turn execution context](../plans/2026-09-13-runtime-session-04-turn-execution-context.md)
- [Plan 05: CubePlex integration and cutover](../plans/2026-09-13-runtime-session-05-cubeplex-adapter.md)
- [Plan 06: Subagent lifecycle](../plans/2026-09-13-runtime-session-06-subagents.md)
