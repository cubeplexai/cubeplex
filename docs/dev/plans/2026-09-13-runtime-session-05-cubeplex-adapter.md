# Plan 05: CubePlex integration with the public execution Session

- Status: Proposed; not executed.
- Goal: Remove RunManager's private Agent access and duplicated execution-result inference while preserving product behavior.
- Architecture: RunManager retains admission, authorization, and resources. A host adapter calls Session and projects results; durable steering uses public committed receipts.
- Tech stack: FastAPI, Redis Lua/Streams, Postgres, CubeLoop, and existing SSE/Playwright flows.
- Dependencies: The CubeLoop commit/release from Plan 04, including Plans 02–03. Covers [specification](../specs/2026-09-13-runtime-session-design.md) R1–R6.
- PR split: 5A integrates the lifecycle; 5B integrates input receipts and removes replaced branches. Merge sequentially; each PR must remain deployable without parallel engines.

## Unit 5A: Host execution adapter

Files: Add `backend/cubeplex/streams/execution_adapter.py`; update `streams/run_manager.py` and `agents/graph.py`. Use uv to update `backend/pyproject.toml` and `uv.lock`, without manually editing dependency declarations. Extend `backend/tests/e2e/test_hitl_pause_resume.py` and add `test_execution_session_adapter.py`.

Interfaces: The adapter receives the resolved Agent/Session, RunContext, ExecutionRequest, and host event publisher, and returns ExecutionResult. Organization/workspace fields remain in host RunContext, not required CubeLoop fields. Reuse LLMSnapshot and existing resource builders.

Core logic: Share prompt/respond configuration and finalization while preserving separate admission paths. New runs use create_run; respond validates durable pending state before claim_resume. Only the winner executes; resume finalization requires the matching claim token. Preserve cancel-as-answer separately from hard cancellation. Replace _state.messages/_extra injection with load_checkpoint/state_context. RunManager still owns product callbacks.

Tests: Real RunManager, Agent, Redis, and Postgres. Cover competing answers, TTL expiry, old-claim completion, follow-up HITL, ordinary completion, provider failure, and forced task.cancel. Preserve DoneEvent semantics, same-run_id streaming, and identical prompt/respond cache prefixes.

## Unit 5B: Input and event projection

Files: Update `streams/steering_delivery.py`, `streams/hitl_resume.py`, `agents/stream.py`, and `streams/run_manager.py`. Preserve `streams/run_events.py` key/CAS contracts. Extend `test_steering_message_repository.py` and `test_stranded_run_recovery.py`.

Interfaces: InputCommitted.input_id maps to client_steer_id. Only durability=checkpoint permits a durable row to become injected. Map ExecutionFinished using specification section 8; ordinary AgentEnd must not emit another Done. Add no required HTTP/SSE fields.

Core logic: Retain ownership, leases, and reconciliation. Repair missed notifications from persisted history; ordinary live steering does not create durable rows. Remove only classification/private wiring covered by the new interface and tests. Retain dangling-call repair needed after crashes. Cancellation, drainers, and callbacks must not clean up a newly registered Agent accidentally.

Tests: Failure between checkpoint and acknowledgment, cancellation racing commitment, requeue before pause, and restart reconstruction. Existing frontend steering.spec.ts verifies stable transcript positions after refresh. Answered HITL cards must not reappear. IM and scheduled-task completion callbacks retain run_id and behavior through existing host tests.

## Data, documentation, and rollout

No new tables, migrations, or routes are planned. If implementation requires a persistent-format change, resolve it through a separate data-compatibility design before merging the affected PR. Do not make ad hoc schema edits. New code must read existing checkpoints, pending HITL, and steering rows.

Update `backend/docs/agent-system-design.md` and relevant prompt-cache guidance. Unchanged user behavior requires no guide change. Any actual user-visible change must update `docs/site/docs/guides/conversations/basics.md` in the same PR.

Run targeted E2E, `backend/tests/e2e/memory/test_prompt_cache.py`, and the frontend steering flow. Full checks run through pre-push. Include a pending request created before the upgrade and answered by an upgraded worker. Do not double-run side-effecting production executions.

Use the existing drain procedure to end active worker attempts while retaining durable HITL, then deploy the host with its pinned dependency. Do not switch an active Session in place. Rollback restores the application and dependency versions together, recreates workers, and reads the existing persistent format. Interrupted tools still use existing stale repair; rollback does not promise automatic reexecution.

## Exit criteria

RunManager retains distributed coordination and uses the public Session for execution. No _state/_extra access remains. Existing HTTP, SSE, prompt-cache, HITL, steering, and scope assertions pass. Record verification commands/results, the dependency commit, removed branches, and retained recovery behavior. Plans 01–05 complete the core refactor independently of Plan 06.
