# cubepi JSONL Tracing in cubeplex — Design

**Date:** 2026-05-20
**Status:** Approved — ready for implementation plan
**Scope:** First cut — JSONL exporter only. No OTLP, no metrics/`Meter`.

## 1. Goal

Wire cubepi 0.4's tracing (`cubepi.tracing.Tracer` + `JsonlSpanExporter`)
into cubeplex's agent run path so that each conversation turn produces an
on-disk OTLP/JSON trace file. Purpose: local debugging — inspect the full
prompt, model response, per-call timing, token usage, and tool args/results
of any run after the fact.

cubepi already ships the tracing machinery (dep pinned as
`cubepi[mcp,postgres,tracing,tracing-otlp]>=0.4.0`). This work is purely the
cubeplex-side integration: build a `Tracer`, attach it to the per-run agent,
gate it behind config.

## 2. Integration Point

`cubeplex/streams/run_manager.py` → `RunManager._run_cubepi_path`.

The agent is built there via `create_cubeplex_agent(...)` (~line 1064) and run
via `await agent.prompt(_user_msg)` (~line 1134). The tracer attaches to that
agent and wraps the `prompt()` call.

## 3. Lifecycle — per-run Tracer

Build a `Tracer` inside `_run_cubepi_path`, attach to that run's agent, flush
+ shut down per run via cubepi's own context managers:

```python
from cubepi.tracing import JsonlSpanExporter, Tracer

if tracing_enabled:
    tracer = Tracer(
        service_name="cubeplex",
        deployment_environment=<config env>,
        agent_name="cubeplex-agent",
        exporters=[JsonlSpanExporter(directory=<config dir>)],
        record_content=<config>,
    )
    async with tracer, tracer.attached(agent):
        await agent.prompt(_user_msg)
else:
    await agent.prompt(_user_msg)
```

`tracer.attached(agent)` (RAII) attaches the recorder on enter and on exit
runs detach + awaits its flush task; the outer `async with tracer` calls
`tracer.shutdown()` (flush + close exporters) on exit. Net effect: every run's
spans are guaranteed written to disk before `_run_cubepi_path` returns.

**Why per-run over a process-level singleton:**
- Agents are already built per-run here — a per-run tracer matches that grain.
- `JsonlSpanExporter` shards output by `run_id` on its own, so there is no
  cross-run file coordination to centralize.
- `async with` gives a deterministic per-run flush-to-disk with no app-startup
  wiring and no shared mutable provider across concurrent runs.

**Trade-off accepted:** rebuilds a `TracerProvider` per run. Cost is
negligible relative to an LLM turn. If it ever matters, promote to a
process-level `Tracer` (built at app startup, `attach`/`detach` per run) —
the call-site shape barely changes.

**Failure isolation:** tracer construction / attach is wrapped so a tracing
fault never breaks a run — on exception, log a warning and run untraced
(mirrors the existing `try/except` + `logger.warning` pattern used for every
optional middleware in `_run_cubepi_path`).

## 4. Config

New `tracing:` block. Resolution mirrors the existing `compaction` / `sandbox`
blocks (read via `config.get("tracing.<key>", <default>)`).

| Key | base (`config.yaml`) | dev (`config.development.yaml`) |
|---|---|---|
| `tracing.enabled` | `false` | `true` |
| `tracing.directory` | `./cubepi-traces` | (inherits) |
| `tracing.record_content` | `false` | `true` |

- `directory` resolves relative to the backend process cwd. Inside a worktree
  the traces land under the worktree's backend dir.
- `deployment_environment` passed to the `Tracer` comes from the existing
  deployment/env config value.
- `record_content=true` in dev so prompts/responses/tool bodies are visible
  while debugging; `false` in base because the bodies can be large and contain
  sensitive content.

## 5. Out of Scope (this cut)

- OTLP exporter (production / collector). Dep is already installed; wiring is a
  later, additive change — add an OTLP exporter to the `exporters=[...]` list
  behind config.
- Metrics / `Meter` histograms.
- W3C trace-context propagation from inbound HTTP requests.
- Redaction hook for `record_content`.

## 6. Testing

Manual E2E in the worktree (ports from `.worktree.env`: backend `:8038`,
frontend `:3038`):

1. Set dev config so tracing is enabled (default-on in dev per §4).
2. Start backend + frontend.
3. Send a chat turn through the UI (one that calls at least one tool, to
   exercise `execute_tool` spans).
4. Confirm `<directory>/<YYYY-MM-DD>/<run_id>.jsonl` exists and contains one
   JSON span per line: an `invoke_agent` root, a `cubepi.turn`, one or more
   `chat` (CLIENT) spans, and `execute_tool` spans for any tool calls.
5. With `record_content=true`, confirm `gen_ai.input.messages` /
   `gen_ai.output.messages` (or `cubepi.llm.raw_*`) attributes are populated.

This is a debugging/observability feature with no deterministic assertion
surface beyond "a well-formed trace file appears," so verification is manual
inspection of the emitted JSONL rather than an automated E2E test.
