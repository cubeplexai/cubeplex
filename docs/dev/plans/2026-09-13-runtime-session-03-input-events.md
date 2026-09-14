# Plan 03: Input, HITL, and event commit ordering

- Status: Proposed; not executed.
- Goal: Let hosts confirm input persistence through public receipts and receive explicit attempt results.
- Architecture: Session sequences events and manages local input queues. Checkpointer commits precede InputCommitted notifications; CubePlex retains ownership of durable delivery.
- Tech stack: Bounded asyncio queues, CubeLoop AgentEvent, and existing HITL/checkpointer components.
- Dependencies: Plan 02. Covers [specification](../specs/2026-09-13-runtime-session-design.md) R3, R4.
- PR: CubeLoop input and event contracts; no generic remote InputSource or message broker.

## Unit A: Identified input receipts

Files: Add `cubeloop/session/input.py`; update queue safe points in `session/types.py`, `agent/agent.py`, and `agent/loop.py`. Add `tests/agent/test_session_input.py`.

Interfaces: InputEnvelope(input_id, message, mode), InputReceipt(status, durability), submit_input, and cancel_input. Preserve Agent.steer/cancel_steer entry points over the shared queue. Input_id maps one-to-one to the host's steer_id without replacing sender metadata.

Core logic: Queued means accepted into memory. Committed follows UserMessage insertion at a safe point. HITL resume completes the tool result before consuming input. Input to a finished attempt returns closed without creating a run. Bound in-process deduplication caches; cross-process deduplication relies on committed input identity in checkpoints.

Tests: Cover safe points during tool execution, a final response without tools, and HITL resume. Repeated input_id must inject once. Cancellation of queued versus committed input returns distinct receipts. Preserve one-at-a-time/all queue modes.

## Unit B: Event sequencing and persistence notifications

Files: Update `cubeloop/agent/types.py`, `agent/agent.py`, and `session/session.py`; add `cubeloop/session/events.py` and `tests/agent/test_session_events.py`; inspect `tracing/recorder.py`.

Interfaces: Envelope seq orders events within one attempt. InputCommitted carries input_id and durability; ExecutionFinished carries ExecutionResult. Preserve existing delta/tool/HITL payloads.

Core logic: Sequence parallel tool events centrally. Preserve existing tool-result order in model history rather than rebuilding it from event arrival order. Required-consumer failures fail execution; observer failures do not override results. Use capacity 256 and backpressure, retaining terminal/tool/committed notifications. If persistence succeeds but notification fails, hosts reconcile from checkpoints without rerunning tools.

Tests: Slow consumers, interleaved tools, observer exceptions, checkpoint exceptions, and required-consumer exceptions. Only committed input receives durable receipts. Real-database tests cover duplicate HITL answers through a new channel instance without repeated execution of recorded results.

## Exit criteria

Tests establish when InputCommitted fires. Hosts can distinguish suspended and completed attempts and acknowledge steering without inspecting private message changes. Tool pairing and existing public event listeners preserve behavior. Document the distinction between queued in memory and durably accepted; make no exactly-once claim for external side effects.
