# Plan 04: Read-only request StepContext

- Status: Proposed; not executed.
- Goal: Keep a model request and its resulting tool calls bound to consistent context and tools.
- Architecture: Capture the logical request after existing middleware transformations. Provider encoding stays in adapters; tool execution retains the step's routing bindings.
- Tech stack: Python typed immutable views and CubeLoop providers/deferred/tracing.
- Dependencies: Plan 03. Covers [specification](../specs/2026-09-13-runtime-session-design.md) R5.
- PR: CubeLoop request context; no product prompt, tool scheduling, or permission-model changes.

## Unit A: Request capture

Files: Add `cubeloop/session/step_context.py`; update _stream_assistant_response in `agent/loop.py`, `agent/types.py`, and `providers/fallback.py`. Add `tests/agent/test_step_context.py`.

Interfaces: StepContext as defined in specification section 7, with read-only model/reasoning/messages/system/tools views. Step_id identifies a logical request: same-model transport retries reuse it, while fallback creates a new step linked to the attempt.

Core logic: Run existing transform/convert hooks before capture. Preserve tool order, historical content, and cache-marker ownership. Protect nested mutable data without copying clients or executors. Share immutable history items to avoid copying long histories at each step. Snapshots and identifiers stay outside model input and durable extra state.

Tests: The transformed request matches provider input. Later state mutation cannot change an already-captured step. Retries preserve content and fallback uses the new model view. Long-history execution does not retain a full history copy per step. Use fixed provider payloads without live model calls.

## Unit B: Tool bindings and deferred discovery

Files: Update `agent/tools.py`, `deferred/middleware.py`, `deferred/_dispatch_tool.py`, and `deferred/_expand_tool.py`; extend existing deferred/provider tests.

Interfaces: Tool invocation carries step_id and its resolved binding. Controlled step-local extension appends bindings without replacing existing names. Before_tool_call retains execution-time checks; host policy_revision is only an identifier.

Core logic: Later registry changes cannot replace an already-resolved call's executor. Deferred dispatch can resolve unexpanded tools within the step; expansion affects subsequent requests. Execution-time authorization checks continue to reject revoked permissions.

Tests: Expand then call, dispatch of unexpanded tools, duplicate-name conflicts, and schema updates interleaved with existing calls. Test permission revocation during approval waits using real middleware and a substituted external side effect. Preserve tool-call/result order.

## Exit criteria

Existing Anthropic/OpenAI/OpenAI Responses provider request tests pass. Plan 05 runs CubePlex prompt-cache E2E after integration. StepContext does not promise full durable replay. Update existing CubeLoop middleware/provider documentation to distinguish memory snapshots from request snapshots.
