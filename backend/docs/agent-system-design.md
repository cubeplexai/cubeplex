# Agent Runtime

**Read before modifying:** agent construction, middleware, tool assembly, run streaming, checkpoints, or event types.

CubePlex runs agents with [CubePi](https://github.com/cubeplexai/cubepi). The authoritative execution path is `cubeplex/streams/run_manager.py`; `cubeplex/agents/graph.py` is the small factory that creates a `cubepi.Agent` from the already-resolved model, tools, middleware, checkpointer, and thread ID.

## Runtime flow

1. A workspace-scoped conversation route creates or resumes a run through `RunManager`.
2. `RunManager` resolves the workspace's model configuration, prompt, enabled tools, and sandbox configuration.
3. It builds the CubePi middleware stack and passes it with the stable tool order to `create_cubeplex_agent`.
4. The CubePi agent streams events while `RunManager` maps them to CubePlex events and writes them to the Redis run stream consumed by SSE clients.
5. A terminal, cancelled, or paused run updates its Redis metadata; durable agent state remains in the checkpointer.

`RunManager` owns background execution and Redis persistence. Redis holds active-run coordination, control signals, event streams, and their expiry; it is not a replacement for the durable conversation state in Postgres.

## Durable state and human input

`cubeplex/agents/checkpointer.py` wraps CubePi's `PostgresCheckpointer` over a shared asyncpg pool. Conversation ID is the agent thread ID, so checkpoints and resumable human-in-the-loop requests survive a process restart. The app opens the shared checkpointer during its lifespan and closes it on shutdown.

The run manager uses CubePi's `CheckpointedChannel` for `ask_user` and sandbox confirmation requests. Keep checkpoint writes and the channel on the same thread and run IDs: a paused request must be resumed or cancelled through the same durable state.

## Middleware and tools

The middleware stack is assembled per run in `RunManager._build_cubepi_agent`. Depending on enabled features, it includes CubePlex middleware for attachments, artifacts, citations, memory, sandboxing, costs, and timestamps, plus CubePi middleware for compaction, subagents, and todo lists. The stack supplies or transforms tools as well as requests and responses.

Tool and middleware order is intentional: it affects the stable prompt prefix and provider prompt caching. Add or reorder a tool only after reading [prompt-cache-discipline.md](prompt-cache-discipline.md). Middleware-provided tools are removed from the explicit tool list before `cubepi.Agent` receives it, because CubePi adds them itself; passing both copies produces duplicate tool names.

## Infrastructure

| Concern                                    | Shipping implementation                                     |
| ------------------------------------------ | ----------------------------------------------------------- |
| HTTP API                                   | FastAPI                                                     |
| Agent runtime                              | CubePi `Agent`                                              |
| Run coordination and SSE event persistence | Redis + `RunManager`                                        |
| Durable checkpoints and application data   | PostgreSQL + CubePi Postgres checkpointer                   |
| Files and artifacts                        | S3-compatible object storage                                |
| Conversation search                        | PostgreSQL PGroonga lexical search with pgvector embeddings |
| Sandboxes                                  | OpenSandbox when enabled                                    |

## Where to make changes

- Agent construction: `cubeplex/agents/graph.py`
- Run lifecycle and middleware composition: `cubeplex/streams/run_manager.py`
- Checkpoint pool lifecycle: `cubeplex/agents/checkpointer.py` and `cubeplex/api/app.py`
- CubePlex middleware: `cubeplex/middleware/`
- SSE event storage and replay: `cubeplex/streams/run_events.py`
- API event schemas: `cubeplex/agents/schemas.py`

Keep routes and streaming adapters focused on transport. Put runtime behavior in the agent, middleware, run manager, or checkpointer layer that owns it.
