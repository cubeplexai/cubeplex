# Plan 02: CubeLoop public execution lifecycle

- Status: Proposed; not executed.
- Goal: Give hosts explicit execution results without requiring access to private Agent state or inference from exception behavior.
- Architecture: Agent owns ExecutionSession, and all public execution entry points share its lifecycle. Reuse the existing sampling loop, Checkpointer, and HITL channel.
- Tech stack: Python asyncio, dataclasses/typed unions, and existing CubeLoop checkpoint backends.
- Dependencies: Plan 01. Covers [specification](../specs/2026-09-13-runtime-session-design.md) R2, R3, R7.
- PR: One CubeLoop execution-lifecycle concern; no CubePlex integration or storage schema changes.

## Unit A: Results and state

Files: Add `cubeloop/session/types.py`, `cubeloop/session/session.py`, and `cubeloop/session/__init__.py`. Update execution calls in `agent/agent.py` and `agent/_outcome.py`. Keep the sampling algorithm in `agent/loop.py`.

Interfaces: ExecutionRequest is the prompt/respond/continue union; ExecutionResult uses the specification's fields. Execute, request_cancel, and request_detach share state. Agent exposes its session while existing methods retain their signatures and delegate to the same implementation. Establish attempt_id before execute.

Core logic: Consolidate _run_with_lifecycle, result validation, checkpoint completion, and suspension finalization under one owner. Overlapping execution returns busy. Cancellation stops new work, cleans up already-produced results, and then reports the outcome. Persistence failures must not be hidden behind a successful AgentEnd.

Tests: Add `tests/agent/test_execution_session.py` using the real Agent and an in-memory checkpointer. Verify Agent and Session calls have equivalent behavior and each attempt finishes once. Cover cancellation racing completion, provider errors without exceptions, completion-marker failures, and detach rejection outside safe points.

## Unit B: Public state access

Files: Use the modules above; inspect `checkpointer/base.py`, `hitl/binding.py`, and `hitl/channel.py`. Extend existing `tests/checkpointer/` and `tests/e2e/`.

Interfaces: Idle-only load_checkpoint and public state_context access to existing extra state. Product middleware continues owning its context data; Session does not require artifact, organization, or sandbox types.

Core logic: Load history once per initialization and never replace it during an active attempt. Preserve message metadata and run_id. HITL channel and Agent share the same checkpointer/thread/run identity. Resume keeps run_id and creates a new attempt_id.

Tests: Resume persisted HITL in a new Agent instance without rerunning completed sibling tools. Existing fork, resume, checkpoint-extra, and tool-pairing tests must continue passing. Follow CubeLoop's layout for database tests.

## Exit criteria

Agent has one lifecycle implementation. Session works with in-memory components and imports neither Redis, FastAPI, nor CubePlex. Existing APIs and the new result API do not start separate engines. Failures, cancellation, and incomplete execution produce explicit results. Update existing CubeLoop execution/resume API documentation and follow repository conventions for new module documentation. Publish a commit that Plan 03 can pin.
