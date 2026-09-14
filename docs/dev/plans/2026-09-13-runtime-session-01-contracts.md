# Plan 01: Runtime contracts and regression baseline

- Status: Proposed; not executed.
- Goal: Make existing behavior a verifiable contract before changing execution abstractions.
- Architecture: Use CubePlex RunManager and CubeLoop Agent together as the baseline. Test public behavior rather than private implementation details; do not add a second Session implementation.
- Tech stack: Python, pytest, Postgres, Redis, FastAPI, and existing Playwright flows.
- Dependencies: None. Covers [specification](../specs/2026-09-13-runtime-session-design.md) R1–R7.
- PR boundaries: Separate CubeLoop and CubePlex contract/test PRs, following each repository's test layout.

## Unit A: Outcome and terminology mapping

Files: Inspect CubeLoop `cubeloop/agent/_outcome.py`, `agent/agent.py`, `agent/loop.py`, and `agent/types.py`; add `tests/agent/test_execution_contract.py`. Update the layering description in CubePlex `backend/docs/agent-system-design.md`.

Interfaces: Define Thread/Run/attempt/Turn/Step. Record Agent.prompt/respond/resume/abort_pending return values, exceptions, and checkpoint ordering, and map them to ExecutionResult. Private outcomes are not business statuses.

Core logic: Distinguish AgentEnd, attempt completion, logical run completion, and SSE Done. Preserve the distinction between cancel-as-answer and hard cancellation. Establish how completion markers interact with failures; do not infer success from the final text.

Tests: Use the real Agent, in-memory components, and a deterministic external provider boundary. Cover ordinary completion, continuation after a tool business error, provider failure, incomplete tool pairing, cancellation, and repeated cancellation. Reuse existing coverage where sufficient.

## Unit B: Persistence and host boundaries

Files: Extend CubePlex `backend/tests/e2e/test_hitl_pause_resume.py`, `test_steering_message_repository.py`, and `test_stranded_run_recovery.py`; inspect `streams/hitl_resume.py` and `run_events.py`.

Interfaces: Preserve run_id on resume, one successful answer versus one resume_in_flight response under contention, stale_answer, pending-request recovery after TTL expiry, and durable steer_id acknowledgment. Treat checkpoint append and SSE publication as separate outcomes.

Core logic: Some existing HITL route tests stub execution and do not cover the full runtime. Add coverage using real RunManager, Agent, Postgres, and Redis; substitute only the external model boundary. Preserve the non-durable ordinary live-steering path.

Tests: Destroy the Agent after HITL suspension and answer with a new instance; tool results must precede steering. Cover cancellation after queue delivery but before checkpointing, and reconciliation after checkpointing but before acknowledgment. Do not automatically replay executed tools. Answers and steering must remain isolated across workspaces.

## Exit criteria

Map existing coverage and gaps to R1–R7. New characterization tests must pass on the current architecture; diagnose failures separately instead of silently redefining the baseline. Capture comparison fixtures for outbound logical model requests, persisted messages, and SSE. Exclude nonsemantic timestamps while retaining ordering, identities, and prompt bytes. Do not store real credentials.

Run the affected modules. CubePlex database/API cases belong in e2e; full checks for code/test PRs run through pre-push. Confirm CubeLoop's repository-specific test commands before implementation. Complete this plan before Plan 02.
