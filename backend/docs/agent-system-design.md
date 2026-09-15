# Agent Runtime

**Read before modifying:** agent construction, middleware, tool assembly, run streaming, checkpoints, or event types.

CubePlex runs agents with [CubeLoop](https://github.com/cubeplexai/cubeloop). The authoritative execution path is `cubeplex/streams/run_manager.py`; `cubeplex/agents/graph.py` is the small factory that creates a `cubeloop.Agent` from the already-resolved model, tools, middleware, checkpointer, and thread ID.

## Runtime flow

1. A workspace-scoped conversation route creates or resumes a run through `RunManager`.
2. `RunManager` resolves the workspace's model configuration, prompt, enabled tools, and sandbox configuration.
3. It builds the CubeLoop middleware stack and passes it with the stable tool order to `create_cubeplex_agent`.
4. `execution_adapter.execute_session` attaches the required event consumer before calling the public CubeLoop `ExecutionSession.execute` API. `RunManager` maps agent events to CubePlex events and writes them to the Redis run stream consumed by SSE clients.
5. CubeLoop returns an explicit `ExecutionResult`; the host maps its outcome and durable checkpoint facts to completed, cancelled, failed, or paused run state. Host event-delivery diagnostics are recorded separately so they do not replace failed or cancelled outcomes. A checkpoint-durable HITL suspension remains resumable when projection fails; for a same-question suspension, the host repairs the answered pending request before surfacing the delivery error.
6. On the prompt path, CubePlex drains projected events and emits the sole public `DoneEvent` before marking Redis run metadata terminal, so the SSE consumer cannot exit before the terminal event is stored.
7. On the respond path, CubePlex atomically leases the resume claim before durable pending cleanup, so stale recovery cannot hand the same answer to another worker while PostgreSQL and Redis are being reconciled. The lease expires at the normal stale-run threshold, allowing recovery if the finalizing worker dies. A successful stale transition revokes the expired claim before releasing the active-run slot, so the old worker can no longer commit terminal metadata. Startup recovery performs its database and scheduler repairs only after that stale transition succeeds. CubePlex then drains projected events, commits terminal metadata under a live claim, and emits `DoneEvent`. CubeLoop's internal `ExecutionFinished` event is never projected to SSE.

`RunManager` owns background execution and Redis persistence. Redis holds active-run coordination, control signals, event streams, and their expiry; it is not a replacement for the durable conversation state in Postgres.

## Durable state and human input

`cubeplex/agents/checkpointer.py` wraps CubeLoop's `PostgresCheckpointer` over a shared asyncpg pool. Conversation ID is the agent thread ID, so checkpoints and resumable human-in-the-loop requests survive a process restart. The app opens the shared checkpointer during its lifespan and closes it on shutdown.

The run manager uses CubeLoop's `CheckpointedChannel` for `ask_user` and sandbox confirmation requests. Keep checkpoint writes and the channel on the same thread and run IDs: a paused request must be resumed or cancelled through the same durable state.

Before an attempt, the host restores messages and middleware extra state together with `ExecutionSession.load_checkpoint`. Middleware receives the live `ExecutionSession.state_context`; do not read or write private `Agent._state` or `Agent._extra` fields. Prompt and respond remain separate admission paths, and a respond worker must hold the matching Redis claim token before it can finalize the run.

## Middleware and tools

The middleware stack is assembled per run in `RunManager._build_cubeloop_agent`. Depending on enabled features, it includes CubePlex middleware for attachments, artifacts, citations, memory, sandboxing, costs, and timestamps, plus CubeLoop middleware for compaction, subagents, and todo lists. The stack supplies or transforms tools as well as requests and responses.

Tool and middleware order is intentional: it affects the stable prompt prefix and provider prompt caching. Add or reorder a tool only after reading [prompt-cache-discipline.md](prompt-cache-discipline.md). Middleware-provided tools are removed from the explicit tool list before `cubeloop.Agent` receives it, because CubeLoop adds them itself; passing both copies produces duplicate tool names.

## Infrastructure

| Concern                                    | Shipping implementation                                     |
| ------------------------------------------ | ----------------------------------------------------------- |
| HTTP API                                   | FastAPI                                                     |
| Agent runtime                              | CubeLoop `Agent` + public `ExecutionSession`                 |
| Run coordination and SSE event persistence | Redis + `RunManager`                                        |
| Durable checkpoints and application data   | PostgreSQL + CubeLoop Postgres checkpointer                   |
| Files and artifacts                        | S3-compatible object storage                                |
| Conversation search                        | PostgreSQL PGroonga lexical search with pgvector embeddings |
| Sandboxes                                  | OpenSandbox when enabled                                    |

## Where to make changes

- Agent construction: `cubeplex/agents/graph.py`
- Run lifecycle and middleware composition: `cubeplex/streams/run_manager.py`
- CubeLoop Session boundary and projection limits: `cubeplex/streams/execution_adapter.py`
- Checkpoint pool lifecycle: `cubeplex/agents/checkpointer.py` and `cubeplex/api/app.py`
- CubePlex middleware: `cubeplex/middleware/`
- SSE event storage and replay: `cubeplex/streams/run_events.py`
- API event schemas: `cubeplex/agents/schemas.py`

Keep routes and streaming adapters focused on transport. Put runtime behavior in the agent, middleware, run manager, or checkpointer layer that owns it.
