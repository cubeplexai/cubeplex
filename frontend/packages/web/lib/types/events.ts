// Web-package SSE event types not yet declared in `@cubeplex/core`.
//
// `FailoverEvent` mirrors the backend `FailoverEvent` Pydantic schema in
// `backend/cubeplex/agents/schemas.py` and the SSE envelope emitted by
// `cubeplex.streams.run_manager` when a `FallbackBoundModel` chain leg fails
// and the next leg takes over. `next_ref === null` signals chain exhaustion
// (no further leg to fail over to); UI must avoid rendering the literal
// string "null" in that case.
export interface FailoverEvent {
  type: 'model_failover'
  timestamp: string
  data: {
    failed_ref: string
    next_ref: string | null
    reason: string
  }
  agent_id: string | null
}
