# Plan 06: Subagent execution identity and result delivery

- Status: Proposed follow-up; does not block the core refactor.
- Goal: Make parent/child execution identity, result delivery, and cancellation ownership explicit.
- Architecture: Reuse Session for child attempts. The parent run tracks child handles and delivery state; CubePlex retains subagent UI events and product tool restrictions.
- Tech stack: CubeLoop asyncio/middleware/tracing and the CubePlex event adapter.
- Dependencies: Plan 05 acceptance. Covers [specification](../specs/2026-09-13-runtime-session-design.md) R8.
- PR split: 6A implements CubeLoop child lifecycle; 6B adapts CubePlex events. No cross-process agent mailbox.

## Unit 6A: Parent/child ownership

Files: Update CubeLoop `cubeloop/middleware/subagents.py`; add `cubeloop/session/children.py`; reuse session types/events. Extend `tests/tracing/test_subagent_nesting.py` and add `tests/agent/test_child_session.py`.

Interfaces: ChildHandle(child_id, parent_run_id, child_run_id, attempt_id) and ChildCompletion(child_id, result, delivery_id). Child_id must map to the existing tool call; a new attempt does not create a new UI agent.

Core logic: Parent cancellation propagates to active child executions; child cancellation does not cancel the parent. Preserve existing middleware wait/block behavior without introducing autonomous background workers. Deliver each child completion to parent history once. After the parent's delivery phase closes, late results become host events for handling; they do not alter a completed answer or start another run. A waiting parent remains cancellable.

Tests: Duplicate completion, parent/child completion races, parent cancellation, and one child failing while a sibling succeeds. Preserve shared-tool, model, and fork_once restrictions. Existing subagent HITL restrictions remain in force.

## Unit 6B: CubePlex projection

Files: Update `backend/cubeplex/streams/subagent_events.py`, `streams/run_manager.py`, and `agents/stream.py` as needed. Add `backend/tests/e2e/test_child_session_projection.py`. Frontend changes are limited to internal projection adjustments; preserve the wire contract.

Interfaces: Map parent/child identities to existing agent_id and tool-result details. Run_id retains its conversation execution ownership. CubePlex still constructs sandbox/MCP/tool-sharing restrictions; Session does not decide cross-tenant sharing.

Core logic: Use one child-lifecycle event source to avoid duplicates from the existing queue and Session. Drain accepted events before parent finalization. Record late results without reopening the parent run. Preserve parent_run_id attribution for costs and traces; child model failover must not appear as parent model failover.

Tests: Live SSE and reloaded history agree on child results, with no duplicate tool messages or charges. Child failure must not misclassify the parent. Child data remains isolated across workspaces. Exercise the real product pipeline with only the external model boundary substituted.

## Exit criteria and later work

Nesting, cancellation, delivery, and UI projection share consistent identities. Background children continuing after parent completion or cross-worker mailboxes require a separate specification covering persistence, scheduling, and authorization. An in-memory queue does not provide those guarantees. Update existing CubeLoop subagent documentation and CubePlex Agent Runtime documentation.
