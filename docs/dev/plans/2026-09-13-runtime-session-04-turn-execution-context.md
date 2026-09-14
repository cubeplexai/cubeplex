# Plan 04: Turn execution context

- Status: Proposed; not executed.
- Goal: Keep a model request and its resulting tool calls bound to consistent context and tools.
- Architecture: Capture request views and execution bindings within the existing Turn after middleware transformations. Provider encoding stays in adapters; tool execution retains the concrete context that produced the call. No separate Step lifecycle is introduced.
- Tech stack: Python typed immutable views and CubeLoop providers/deferred/tracing.
- Dependencies: Plan 03. Covers [specification](../specs/2026-09-13-runtime-session-design.md) R5.
- PR: CubeLoop request context; no product prompt, tool scheduling, or permission-model changes.

## Unit A: Request capture

Files: Add `cubeloop/session/turn_execution_context.py`; update _stream_assistant_response in `agent/loop.py`, `agent/types.py`, and `providers/fallback.py`. Add `tests/agent/test_turn_execution_context.py`.

Interfaces: TurnExecutionContext as defined in specification section 7, with turn_id/run_id/attempt_id, read-only model/reasoning/messages/system/tools views, and retained execution bindings. Same-model transport retries reuse the captured context. Fallback captures a new context under the same turn_id and attempt_id. Trace spans distinguish provider requests; no step_id is added.

Core logic: Run existing transform/convert hooks before capture. Preserve tool order, historical content, and cache-marker ownership. Protect nested mutable data without copying clients or executors. Share immutable history items to avoid copying long histories on each capture. Contexts and identifiers stay outside model input and durable extra state. Context replacement must not emit additional TurnStart/TurnEnd events.

Tests: The transformed request matches provider input. Later state mutation cannot change an already-captured request view. Retries reuse the context; fallback captures the new model view without changing turn_id or mutating the previous context. TurnStart/TurnEnd boundaries remain unchanged. Long-history execution does not retain a full history copy per capture. Use fixed provider payloads without live model calls.

## Unit B: Tool bindings and deferred discovery

Files: Update `agent/tools.py`, `deferred/middleware.py`, `deferred/_dispatch_tool.py`, and `deferred/_expand_tool.py`; extend existing deferred/provider tests.

Interfaces: Tool invocation carries turn_id and retains its concrete TurnExecutionContext and resolved binding, rather than looking up the latest context by turn_id. Controlled context-local extension appends bindings without replacing existing names. Before_tool_call retains execution-time checks; host policy_revision is only an identifier.

Core logic: Later registry changes or context replacement cannot replace an already-resolved call's executor. Deferred dispatch can resolve unexpanded tools within the Turn; expansion affects subsequent Turns' advertised catalogs. Execution-time authorization checks continue to reject revoked permissions.

Tests: Expand then call, dispatch of unexpanded tools, duplicate-name conflicts, and schema updates interleaved with existing calls. Test permission revocation during approval waits using real middleware and a substituted external side effect. Preserve tool-call/result order.

## Exit criteria

Existing Anthropic/OpenAI/OpenAI Responses provider request tests pass. Plan 05 runs CubePlex prompt-cache E2E after integration. TurnExecutionContext does not promise full durable replay or introduce an independent lifecycle. Update existing CubeLoop middleware/provider documentation to distinguish memory snapshots from turn execution context.
