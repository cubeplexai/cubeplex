# Plan 01: Runtime contracts and regression baseline

- Status: Proposed; not executed.
- Goal: Make existing behavior a verifiable contract before changing execution abstractions.
- Architecture: Use CubePlex RunManager and CubeLoop Agent together as the baseline. Test public behavior rather than private implementation details; do not add a second Session implementation.
- Tech stack: Python, pytest, Postgres, Redis, FastAPI, and existing Playwright flows.
- Dependencies: None. Covers [specification](../specs/2026-09-13-runtime-session-design.md) R1–R7.
- PR boundaries: Separate CubeLoop and CubePlex contract/test PRs, following each repository's test layout.

## Unit A: Outcome and terminology mapping

Files: Inspect CubeLoop `cubeloop/agent/_outcome.py`, `agent/agent.py`, `agent/loop.py`, and `agent/types.py`; add `tests/agent/test_execution_contract.py`. Update the layering description in CubePlex `backend/docs/agent-system-design.md`.

Interfaces: Define Thread/Run/attempt/Turn and distinguish TurnExecutionContext from execution identities. A Run contains Turns; retries and fallback remain within the current Turn, with no separate Step lifecycle or step_id. Record Agent.prompt/respond/resume/abort_pending return values, exceptions, and checkpoint ordering, and map them to ExecutionResult. Private outcomes are not business statuses.

Core logic: Distinguish AgentEnd, attempt completion, logical run completion, and SSE Done. Preserve the distinction between cancel-as-answer and hard cancellation. Establish how completion markers interact with failures; do not infer success from the final text.

Tests: Use the real Agent, in-memory components, and a deterministic external provider boundary. Cover ordinary completion, continuation after a tool business error, provider failure, incomplete tool pairing, cancellation, and repeated cancellation. Reuse existing coverage where sufficient.

Record current prompt/resume overlap rejection versus respond lock waiting. Plan 02 intentionally replaces the latter with ExecutionBusy; keep proposed-contract tests separate from baseline characterization. Cover provider failure followed by cancellation, cancellation-induced exceptions, unexplained abandoned exits, and checkpoint/pairing failure during cancellation. Record the original cause separately from the final validated outcome. Characterize steering and follow-up drain points and independent queue policies.

## Unit B: Persistence and host boundaries

Files: Extend CubePlex `backend/tests/e2e/test_hitl_pause_resume.py`, `test_steering_message_repository.py`, and `test_stranded_run_recovery.py`; inspect `streams/hitl_resume.py` and `run_events.py`.

Interfaces: Preserve run_id on resume, one successful answer versus one resume_in_flight response under contention, stale_answer, pending-request recovery after TTL expiry, and durable steer_id acknowledgment. Treat checkpoint append and SSE publication as separate outcomes.

Core logic: Some existing HITL route tests stub execution and do not cover the full runtime. Add coverage using real RunManager, Agent, Postgres, and Redis; substitute only the external model boundary. Preserve the non-durable ordinary live-steering path.

Tests: Destroy the Agent after HITL suspension and answer with a new instance; tool results must precede steering. Cover cancellation after queue delivery but before checkpointing, and reconciliation after checkpointing but before acknowledgment. Do not automatically replay executed tools. Answers and steering must remain isolated across workspaces.

Include stale pending without a new HITL event, pending equal to the answered question, and a genuine follow-up question; only the last remains paused. Verify pinned-memory/todo extra restoration and stable live extra references. Capture prompt Done-before-terminal-update and respond claim-fenced-update-before-Done ordering, with last-turn citations/subagent text and paused preserved. These are host contracts, not a direct ExecutionFinished-to-SSE mapping.

## Exit criteria

Map existing coverage and gaps to R1–R7. New characterization tests must pass on the current architecture; diagnose failures separately instead of silently redefining the baseline. Capture comparison fixtures for outbound logical model requests, persisted messages, and SSE. Exclude nonsemantic timestamps while retaining ordering, identities, and prompt bytes. Do not store real credentials.

Run the affected modules. CubePlex database/API cases belong in e2e; full checks for code/test PRs run through pre-push. Confirm CubeLoop's repository-specific test commands before implementation. Complete this plan before Plan 02.
