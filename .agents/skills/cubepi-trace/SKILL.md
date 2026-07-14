---
name: cubepi-trace
description: Use when debugging a cubepi/cubebox agent run — a missing final reply, a tool that misbehaved, a 4xx from the model, wrong token/cache numbers, or "why did the agent do that?". Reads the JSONL traces cubepi writes per run via the `cubepi trace` CLI (ls / view / follow / stats) to inspect the span tree, errors, tool inputs/outputs, and token usage. Triggers on phrases like "查一下这个 run", "trace 一下", "为什么最后一轮没回复", "工具结果不对", "看 trace".
---

# Debugging cubepi runs with `cubepi trace`

cubepi can record every agent run as an OpenTelemetry span tree, written to
local JSONL files. The `cubepi trace` CLI reads those files so you can see
exactly what happened inside a run without re-running it: which LLM calls and
tool calls fired, in what order, what each returned, where it errored, and the
token/cache counts. Reach for this **before** guessing at a bug.

## When this applies

- The final answer is missing, truncated, or the run "ended" with no reply.
- A tool (web_search, web_fetch, execute, MCP tool, …) returned something wrong
  or empty, or the agent looped.
- The model returned a 4xx/5xx (e.g. `BadRequestError`), or the run status is
  `error`.
- Token / cache / cost numbers look wrong.
- You just need to understand the agent's actual trajectory for a given input.
- A background LLM call (e.g. memory consolidation via `Tracer.oneshot()`) isn't
  behaving as expected — oneshot calls produce `invoke_agent` spans too and are
  searchable by metadata.

## Prerequisites (one-time)

Tracing must be **on** and **recording content** for the run you want to
inspect. In cubebox it's config-driven (dynaconf):

- `tracing.enabled: true`
- `tracing.directory: ./cubepi-traces`  (default; relative to the backend cwd)
- `tracing.record_content: true`  (needed to see prompts / tool args / results;
  off = you get the span tree and timings but no content)

Env override form: `CUBEBOX_TRACING__ENABLED=true`,
`CUBEBOX_TRACING__RECORD_CONTENT=true`, `CUBEBOX_TRACING__DIRECTORY=...`.
cubebox's `config.development` already enables this. Files land at
`<directory>/<YYYY-MM-DD>/<trace_id>.jsonl` — sharded by `trace_id`, so one
file holds the whole trace, including any nested subagent runs (they inherit
the parent's trace). A trace that crosses UTC midnight is split across two date
dirs; the CLI merges them. (`cubepi.run_id` is still recorded as a per-span
attribute if you need to distinguish individual runs within a trace.)

Run the CLI from the cubebox backend dir so it picks up the venv that has
cubepi installed (the `trace-cli` extra provides it):

```bash
uv run cubepi trace --help
```

`--dir` defaults to `./cubepi-traces`; pass `--dir <path>` if your traces live
elsewhere (e.g. a worktree).

## The fast path (this is 90% of debugging)

```bash
# 1. List recent traces, newest first. The `trace_id` column is the id you
#    pass to `view`; the `input` column shows the user's message so you can
#    find the right one; `status` flags errors.
uv run cubepi trace ls

# 2. View one trace as a span tree. A trace id PREFIX is enough (ls truncates
#    ids). Errors are printed inline under the failing span — no flags needed.
uv run cubepi trace view 1cd97cdb
```

`view` output looks like (each node ends with a short `span_id` for locating
the raw span in the JSONL):

```
trace
└── invoke_agent  14425.8ms  [0x1cd97cdb]
    ├── cubepi.turn  1283.1ms  [0x5cfda93e]
    │   ├── chat deepseek-v4-flash  1208.7ms  tok 6845/68  [0x0d130229]
    │   └── execute_tool subagent  9610.2ms  subagent  [0x38bdd10a]
    │       └── invoke_agent  9601.0ms  [0x8094f99b]   ← subagent run, nested
    │           └── cubepi.turn  9598.4ms  [0x57c5cfc7]
    │               ├── chat deepseek-v4-flash  1190.3ms  [0x8205ca6b]
    │               └── execute_tool web_search  6500.2ms  web_search  [0xca4e59fc]
    └── cubepi.turn  491.9ms  ERROR  [0xce25f242]
        └── chat deepseek-v4-flash  427.2ms  ERROR  [0x0bff68ec]
            └── error: Error code: 400 - ... `tool_use` ids were found without
                `tool_result` blocks immediately after: call_01_...
```

Read the tree top-down: `invoke_agent` (one run) → `cubepi.turn` (one agent
loop turn) → `chat <model>` (an LLM call, with `tok <input>/<output>`) and
`execute_tool <name>` (a tool call). A **subagent** appears as
`execute_tool subagent` with the subagent's own `invoke_agent → cubepi.turn →
…` nested directly beneath it — its chat and tool spans live inside that
subtree, not flat under the parent turn. The `[0x…]` suffix is the span_id
(grep it in the raw JSONL to inspect that exact span). An `ERROR` marker plus
the inline `error:` line usually tells you the root cause directly.

## Going deeper

```bash
# Expand the actual gen_ai content (prompts, tool args, tool results) —
# useful when the error isn't enough and you need to see what was sent.
uv run cubepi trace view <run> --content

# Expand ALL span attributes (verbose; includes full request bodies/tool
# schemas — large). Use when --content isn't enough.
uv run cubepi trace view <run> -v

# Watch a run live as spans complete (poll). Good for a run in progress.
uv run cubepi trace follow <run>

# Aggregate across runs: latency p50/p95, error rate, tokens — by model or tool.
uv run cubepi trace stats --by model
uv run cubepi trace stats --by tool --since 2026-05-20

# Filter by run metadata (cubebox stamps conversation_id / user_id / org_id /
# workspace_id on the root span). Repeatable = AND, exact match. Works on
# `ls` and `stats` — find all traces for a conversation, or that user's stats.
uv run cubepi trace ls --meta conversation_id=conv_123
uv run cubepi trace ls --meta user_id=usr_9 --meta org_id=org_1
uv run cubepi trace stats --by model --meta user_id=usr_9

# Show metadata as ls columns (display, not filter):
uv run cubepi trace ls --show-meta conversation_id,user_id
```

## Tracing one-shot LLM calls (`Tracer.oneshot`)

`Tracer.oneshot()` instruments a single prompt→text LLM call (no agent loop)
and writes it as a full `invoke_agent` trace. Cubebox uses this for background
tasks like memory consolidation. The span tree is flat — no `cubepi.turn`
wrapper since there's no loop:

```
trace
└── invoke_agent  820ms  [0x3f2a1b...]
    └── chat deepseek-v3  815ms  tok 3200/180  [0x9c45d2...]
```

The root span carries:
- `cubepi.oneshot.operation` — the operation name (e.g. `"consolidate_memory"`)
- `cubepi.metadata.*` — caller-supplied metadata (e.g. `conversation_id`)

Find oneshot traces by operation or conversation:

```bash
# All consolidation runs  (operation is exposed as cubepi.metadata.oneshot_operation
# so the --meta filter can reach it; cubepi.oneshot.operation is also set for dashboards)
uv run cubepi trace ls --meta oneshot_operation=consolidate_memory

# Oneshot traces for a specific conversation (alongside its agent runs)
uv run cubepi trace ls --meta conversation_id=conv-1fwZQ8u3ZDukx3

# Stats on consolidation token usage
uv run cubepi trace stats --by model --meta oneshot_operation=consolidate_memory
```

Cubebox wires this in `run_manager.py`; the consolidation pass calls:

```python
async with tracer.oneshot(
    provider=provider,
    model=model,
    operation="consolidate_memory",
    metadata={"conversation_id": conv_id, "user_id": user_id},
) as session:
    raw = await session.generate(
        system=CONSOLIDATION_SYSTEM,
        messages=[UserMessage(...)],
        max_output_tokens=1500,
    )
```

## Replaying a failing LLM call (`trace convert`)

When the span tree + content aren't enough and you need to replay the exact API
call — same messages, same tools, same parameters — use `trace convert` to
reconstruct the request body from a recorded `chat` span (requires
`record_content=True`):

```bash
# Reconstruct the last LLM call as an OpenAI JSON body
uv run cubepi trace convert <run_id>

# Pick a specific call by span_id prefix (copy [0x…] from `view`)
uv run cubepi trace convert <run_id> --span 0xbb7eb1

# Shell-executable curl (uses $BASE_URL / $API_KEY env vars)
uv run cubepi trace convert <run_id> --span 0xbb7eb1 --format curl

# Anthropic Messages API shape
uv run cubepi trace convert <run_id> --format anthropic
```

The `[0x…]` suffix from each `chat` node in `view` output is the span_id — paste
it directly as `--span 0x<prefix>`. No need to count turns.

## Debugging streaming failures (`record_stream`)

For issues where the span tree doesn't show the problem — missing tool call
arguments, duplicate tool executions, truncated output — enable stream recording
in the tracer:

```python
Tracer(record_content=True, record_stream=True, stream_dir="./cubepi-traces", …)
```

This writes `<stream_dir>/<run_id>.stream.jsonl` — one JSON line per raw
`StreamEvent`. Check the file after a failing run:

```bash
# See all toolcall events for a run
grep '"type": "toolcall' cubepi-traces/<date>/<run_id>.stream.jsonl | python -m json.tool

# Check for duplicate toolcall_end (double finish_reason bug pattern):
grep '"toolcall_end"' cubepi-traces/<date>/<run_id>.stream.jsonl | wc -l   # expect 1
```

Key fields: `t` (elapsed seconds), `type`, `ci` (content index), `accumulated`
(running arg chars), `args_chars` (final count at end). An `args_chars: 0` at
`toolcall_end` means no argument chunks ever arrived — the model sent an empty
tool call.

If the CLI view still isn't enough, the files are plain JSONL — one span per
line — so you can parse them directly (e.g. with `python -c`/`jq`) to pull a
specific attribute. Useful attribute keys:

- `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`,
  `gen_ai.usage.cache_read.input_tokens`, `gen_ai.usage.cache_creation.input_tokens`
- `gen_ai.tool.name`, `gen_ai.tool.call.result` (the tool's output),
  `gen_ai.input.messages`, `gen_ai.output.messages`
- error detail lives in a span **event** named `gen_ai.client.operation.exception`
  (`exception.type`, `exception.message`, `exception.stacktrace`); the span's
  `status.description` carries a truncated copy.

## Reading token / cache numbers

Mind the convention — it differs between the trace and cubebox's UI:

- **In the trace**, `gen_ai.usage.input_tokens` is the **inclusive total prompt**
  (the recorder reconciles to OTel semconv by recording
  `input + cache_read + cache_creation`; see `recorder.py
  _set_usage_anthropic_like` / `_set_usage_openai_like`).
  `gen_ai.usage.cache_read.input_tokens` is the portion served from cache and is
  a **subset** of input_tokens. So from trace fields:
  **cache hit rate = `cache_read / input_tokens`** (always ≤ 100%). Do NOT add
  cache_read to the denominator here — that double-counts it.
- **In cubebox's UI / cost layer**, `input_tokens` is the **uncached** new input
  (cost.py bills input and cache_read separately), so there the rate is
  `cache_read / (input + cache_read)`. Both yield the same true rate; only the
  meaning of `input_tokens` differs. Don't mix the two formulas across layers.

## Tips

- **Prefix match**: `view`/`follow`/`stats` accept a unique trace-id prefix;
  copy the visible chars from the `trace_id` column in `ls`. An ambiguous
  prefix lists candidates.
- **Find the trace** by the `input` column in `ls` rather than guessing ids.
- **Subagent missing from the tree?** It should nest under
  `execute_tool subagent`. If a subagent's spans look absent, confirm the run
  was recorded with a cubepi new enough to shard by `trace_id` (older runs
  sharded by `run_id`, so a subagent's spans landed in a separate file).
- **Backend vs frontend**: the trace shows what the backend/agent did. If the
  trace shows correct data but the UI is wrong, the bug is in the frontend
  (SSE handling / rendering) — the trace won't see that; use a browser instead.

## Source and contributing

Skill source and cubepi source: https://github.com/cubeplexai/cubepi  
If the docs or skill are unclear, read the source — it's the authoritative reference.  
Bugs or missing coverage: open an issue or PR at https://github.com/cubeplexai/cubepi/issues
