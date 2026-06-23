# Harness Benchmarks: Evaluating cubebox (not cubepi, not the model)

**Status**: Design
**Date**: 2026-06-23
**Author**: xfgong

## Summary

Run public agent benchmarks against **cubebox's HTTP API** (not cubepi
directly, not the model in isolation), so the resulting score reflects
the cubebox harness — middleware, tool design, planning loop, sandbox
integration, memory, context management — under a fixed model. The
marketing claim we want to be able to make is:

> "On the same Claude (or DeepSeek, or whichever) model that
> OpenHands / SWE-agent / Cursor used, cubebox scores **X%** on
> SWE-bench Verified, **Y points above** the published harness baseline."

The benchmark grid is intentionally small and pointed at workloads where
the harness (not the model) drives most of the score variance:

- **SWE-bench Verified** — engineering / repo-level edits. Industry
  standard, biggest spread between harnesses (~20+ point range on the
  same model).
- **τ-bench** — multi-turn customer-service simulator with strict
  policies. Maps directly to cubebox's streaming conversational
  positioning. Less crowded leaderboard so a SOTA same-model result is
  achievable.

Benchmarks that primarily test the **model** (GPQA, MMLU-Pro, BFCL,
HumanEval, LiveCodeBench) are explicitly excluded — winning them says
"good model choice," not "good harness."

The cubebox public API has already been verified end-to-end on
2026-06-23 (see [Hello-world API drive verification](#hello-world-verification)
in this doc and the `feat/2026-06-23-api-key` branch): a single Bearer
token authenticates an external harness to create conversations, stream
SSE, observe tool calls, and download sandbox files. **No cubebox or
cubepi code changes are required to run the first round of benchmarks.**

## Goals

- A repeatable benchmark harness that drives **public cubebox HTTP API**
  and produces SWE-bench Verified + τ-bench scores, plus per-task
  artifacts (patch, traces, token counts) sufficient for adversarial
  defense of the result.
- Same-model comparability with at least 2 published baseline harnesses
  per benchmark (e.g. OpenHands, SWE-agent for SWE-bench).
- Phased rollout: 50-task mini run first (sanity + plumbing debug), then
  full suites, then harness optimization, then CI regression.
- Honest disclosure format we ship with every published score (model
  version, temperature, max-tokens, thinking, parallelism, retries).

## Non-goals

- A new sandbox image, a new tool, a new model preset, a new auth
  mechanism. The verification proved we can run with what's in main.
- A web UI for benchmark results. Markdown report + Grafana dashboard
  for ongoing runs is enough.
- A custom evaluator. Both SWE-bench and τ-bench have official scoring
  pipelines and we feed them through unchanged.
- Beating OpenAI / Anthropic in absolute terms. The goal is *harness
  attribution* — same model, our scaffolding does more with it.
- Real-time / streaming benchmarks (latency-sensitive workloads). Out of
  scope for round 1; revisit if cubebox starts marketing low-latency.

## Why these benchmarks (and not others)

A benchmark is "harness-sensitive" when most of the score gap between
top systems comes from scaffolding rather than from the base model. The
4 quadrants below show where each candidate lands:

| | Single-turn | Multi-turn |
|---|---|---|
| **Model-dominated** | GPQA, MMLU-Pro, MATH, HumanEval, LiveCodeBench, **BFCL** (despite the "function calling" name — measures the model's JSON emission, not the harness's tool design) | GAIA (model-dominated until ~level 3) |
| **Harness-dominated** | (rare — single-turn doesn't leave room for orchestration) | **SWE-bench Verified**, **τ-bench**, Terminal-Bench, OSWorld, WebArena |

Where harness drives score:

- **SWE-bench Verified**: same Claude Sonnet, OpenHands ~50%+, Agentless
  ~30%, vanilla single-turn ~5%. ~20–45 point range from harness alone.
- **τ-bench**: policy adherence + tool use under user simulation. Memory
  / planning / retry logic dominate over base model. New enough that
  the leaderboard hasn't been gamed.
- **Terminal-Bench, OSWorld, WebArena**: harness-heavy but expensive to
  run, leaderboards still maturing — defer to round 2.

What we are intentionally excluding from round 1 and why:

- **BFCL** — pure model behavior, no orchestration. Winning it would
  prove "we picked Claude," not "we built a harness."
- **GAIA** — high cost per run, ambiguous attribution. Revisit after
  SWE-bench + τ-bench are stable.
- **Aider polyglot** — useful, but overlaps SWE-bench Verified for
  marketing. Optional secondary signal.

## Hello-world verification

The end-to-end drive has been confirmed against main. Run on
2026-06-23, on the `feat/2026-06-23-api-key` worktree (slot 12, port
8012):

| Capability | Endpoint | Status |
|---|---|---|
| Mint personal token | `POST /api/v1/me/api-keys` | ✅ shipped (PR #270) |
| Bearer-auth user info | `GET /api/v1/auth/me` (`Authorization: Bearer sk-…`) | ✅ |
| Bearer-auth workspace list | `GET /api/v1/workspaces` | ✅ |
| Create conversation | `POST /api/v1/ws/{ws}/conversations` | ✅ |
| Send + stream | `POST .../conversations/{cid}/messages`, `Accept: text/event-stream` | ✅ |
| Tool calls execute | `execute` tool ran 4 shell commands in sandbox | ✅ |
| Sandbox state persists | Files written in step N visible in step N+1 | ✅ |
| Pull artifacts | `GET .../sandbox/files/download?path=/workspace/...` | ✅ |
| Final assistant text | `GET .../conversations/{cid}/messages` returns ordered history | ✅ |

A 4-step toy task (mkdir → write `calc.py` → run + capture output → cat
→ summarise) completed in 45.5 s using the `flash` preset on the
internal LLM gateway, ~10 s/tool-call. Single-turn "say hi" round-trip
~ 0.4 s.

### SSE format (as actually observed)

```
id: 1782191433141-0
data: {"type":"text_delta","timestamp":"…","data":{"content":"Hi","usage":{}},
       "agent_id":null,"agent_name":null,"event_id":"1782191433141-0"}

id: 1782191433203-0
data: {"type":"usage","timestamp":"…","data":{"input_tokens":1424,
       "output_tokens":1,"cache_read_tokens":10240,"cache_write_tokens":0},…}

id: 1782191433446-0
data: {"type":"done","timestamp":"…","data":{"usage":{"turn":{…},
       "session":{"total_input_tokens":…,"total_output_tokens":…,
       "total_cache_read_tokens":…,"total_cache_write_tokens":…},
       "context_window":1000000,"context_tokens":1424}},…}
```

Notes for the harness implementer:

- Event type lives in the JSON `type` field. There is **no** `event:`
  SSE line; `data:` is the only event line, and `id:` is opaque.
- `usage` events fire on every turn with `input / output / cache_read /
  cache_write` token counts split out — feed these directly into the
  per-task cost record. No need to count tokens locally.
- `done` event carries the session totals and `context_tokens` /
  `context_window` — perfect for an alarm when a task is about to
  saturate context.

### Drive pattern (per task)

```python
# One-time, harness startup
token = os.environ["CUBEBOX_TOKEN"]       # sk-… from settings/profile
ws    = os.environ["CUBEBOX_WS"]          # workspace id
H     = {"Authorization": f"Bearer {token}"}

# Per task
cid = POST("/api/v1/ws/{ws}/conversations",
           json={"title": instance_id}).json()["id"]

with POST_stream(
        "/api/v1/ws/{ws}/conversations/{cid}/messages",
        headers={**H, "Accept": "text/event-stream"},
        json={"content": render_task_prompt(task), "thinking": "off",
              "model_key": "claude-sonnet-4-6"}) as stream:
    for evt in parse_sse(stream):
        record(evt)                       # usage, tool_call, text_delta
        if evt["type"] in ("done", "error"):
            break

patch = GET("/api/v1/ws/{ws}/sandbox/files/download"
            f"?path=/workspace/swebench/runs/{instance_id}/patch.diff"
            f"&conversation_id={cid}").content

score_one_instance(instance_id, patch)    # SWE-bench official harness
```

## SWE-bench Verified design

500 tasks, 12 Python repos (django, astropy, matplotlib, seaborn,
flask, requests, xarray, pylint, pytest, scikit-learn, sphinx, sympy).
Each task pins a SHA + a failing test; agent writes a patch; official
SWE-bench harness applies + scores.

### Per-task sandbox layout

```
/workspace/swebench/
  .cache/                            ← bare mirrors, one per repo
    django.git/
    flask.git/
    ...
  runs/
    django__django-11099/            ← worktree per task
      <full source tree at task SHA>
      .venv/                         ← isolated Python deps per task
      patch.diff                     ← agent writes this at the end
```

- **Bare mirror**: agent's first action on first-encounter of a repo is
  `git clone --bare https://github.com/<repo> /workspace/swebench/.cache/<repo>.git`.
  Once cached, subsequent tasks on the same repo are `git worktree add`
  away (seconds, not minutes; ~5 GB total disk vs hundreds of GB if we
  re-clone per task).
- **Per-task venv**: each task installs its own dependencies into
  `.venv` inside the worktree. Prevents `pip install scipy==1.0` in one
  task from breaking pytest in another.
- **`patch.diff` convention**: every task ends with
  `cd /workspace/swebench/runs/<id> && git diff > patch.diff`. Harness
  pulls it via `/sandbox/files/download`.

### Per-task user message (the "task prompt")

The cubebox API doesn't accept a per-run system prompt (`SendMessageRequest`
in `conversations.py` has no field for it). The task instructions live
inside `content` — functionally equivalent for our purposes, and
explicit about the bench-vs-product separation.

```
You are an autonomous engineer fixing a single SWE-bench task.

REPO:      {repo}
COMMIT:    {sha}
INSTANCE:  {instance_id}
FAILING:   {failing_tests}
PROBLEM:
{problem_statement}

WORK INSIDE THIS DIRECTORY (and only this directory):
  /workspace/swebench/runs/{instance_id}

PROCEDURE:
1. If /workspace/swebench/.cache/{repo_slug}.git does not exist, run:
     git clone --bare https://github.com/{owner}/{repo_name} \
       /workspace/swebench/.cache/{repo_slug}.git
2. git --git-dir=/workspace/swebench/.cache/{repo_slug}.git worktree add \
       /workspace/swebench/runs/{instance_id} {sha}
3. cd /workspace/swebench/runs/{instance_id}
4. python -m venv .venv && . .venv/bin/activate
5. pip install -e .   (if the repo has a setup.py / pyproject.toml)
6. Run the failing test(s) to confirm they fail.
7. Read the relevant source, write a fix, re-run the failing test(s)
   plus any related tests until they pass.
8. cd /workspace/swebench/runs/{instance_id} && git diff > patch.diff
9. Confirm `patch.diff` is non-empty.

CONSTRAINTS:
- Do not modify or run code outside /workspace/swebench/runs/{instance_id}.
- Do not edit test files for this task.
- Stop when patch.diff contains a real diff and all targeted tests pass.
```

### Patch extraction

Single request after the agent says done:

```
GET /api/v1/ws/{ws}/sandbox/files/download
    ?path=/workspace/swebench/runs/{instance_id}/patch.diff
    &conversation_id={cid}
```

Empty / missing file → task scored as "no patch" (`0`).

### Why this works without API additions

- Same-workspace + same-user usually shares a sandbox PVC (per
  `sandbox/manager.py:79-83`), but per-task directory isolation
  sidesteps the contamination risk entirely — files in
  `runs/<id-A>/` don't collide with files in `runs/<id-B>/`.
- No per-run system prompt needed because the task prompt IS the user
  message and we never reuse a conversation across tasks.
- No `git diff` endpoint needed because the agent runs `git diff` and
  we read the file.

## τ-bench design

τ-bench ships 2 domains (retail, airline) with policy-doc + tool-set +
user-simulator + scoring. The simulator is an LLM playing a user with
a goal; the agent must obey the policy doc while satisfying the
simulated user.

Mapping to cubebox:

- One conversation per τ-bench task. The agent under test = cubebox.
- The simulated user lives **inside the harness**, not inside cubebox.
- After every cubebox assistant turn (detected by SSE `done`), the
  harness sends the next simulated-user message until the task ends or
  hits a turn cap.
- Policy doc + tool catalog go into the first user message (same
  pattern as SWE-bench's task prompt).

Tools τ-bench expects (mostly "lookup customer", "modify order",
"refund") map to ordinary `execute` shell calls against a stubbed
Python service running in the sandbox — harness ships the stub as a
preamble step. No new cubebox tool needed.

## Concurrency model

cubebox enforces **one active run per conversation** (`run_manager.py:842-860`,
CAS on insert). So parallelism is achieved by N **separate conversations**,
not by N runs on one conversation.

Recommended fleet sizing:

| Conversations in flight | Wall-clock for SWE-bench Verified (500 tasks) | Notes |
|---|---|---|
| 1 (serial) | 35–125 h (1.5–5 days) | Easiest to debug |
| 5 | 7–25 h | Sweet spot; cheap enough to retry stragglers |
| 10 | 4–13 h | Watch sandbox host load; monitor LLM rate limits |
| 20+ | Marginal gains | Likely throttled by LLM gateway, not cubebox |

Per-task isolation (different `/workspace/swebench/runs/<id>/`
directories) means a single user/workspace is sufficient. No need to
pre-provision a user pool.

## Result storage + reproducibility

Per run (one execution of a benchmark suite end-to-end):

```
benchmarks/runs/{YYYY-MM-DDThhmm}-{suite}-{commit}/
  meta.json                # cubebox commit, model, model preset,
                           # thinking, temp, max_tokens, fleet size,
                           # start/end timestamps
  tasks/{instance_id}/
    prompt.txt             # the exact user message
    sse.jsonl              # raw SSE event stream
    patch.diff             # extracted patch (SWE-bench) or transcript
    score.json             # SWE-bench official scoring output
    timings.json           # per-tool-call durations, total token usage
    cubepi-trace/          # symlinked from sandbox-side tracing if on
  summary.json             # aggregate score, P50/P95 latency, $cost
  REPORT.md                # human-readable summary, regressions vs prior
```

The full `sse.jsonl` per task is the source of truth — anyone arguing
about a result can replay it offline. `meta.json` + `score.json` are
machine-readable for trend plots.

## Marketing claim framing

Every published number ships with this disclosure block:

```
SWE-bench Verified, cubebox harness
- Model:               anthropic/claude-sonnet-4-6 (released YYYY-MM-DD)
- Provider:            <bedrock|anthropic|gateway>
- Temperature:         <value> (default 1.0)
- Max output tokens:   <value>
- Thinking:            off | {effort, summary}
- Parallel:            <N> conversations
- Retries:             <N> per task on transient errors, otherwise none
- cubebox commit:      <sha>
- Date:                YYYY-MM-DD
- Score:               XX.X%  (N/500 resolved)
- Cost:                $XX.XX
```

Comparison-ready table (filled in over Phase 2/3):

| Harness | Model | Score | Notes |
|---|---|---|---|
| **cubebox** | claude-sonnet-4-6 | **TBD** | this work |
| OpenHands | claude-sonnet-4-6 | <from leaderboard> | |
| SWE-agent | claude-sonnet-4-6 | <from leaderboard> | |
| Cursor agent mode | claude-sonnet-4-6 | <if disclosed> | |

The headline we want to be able to claim, once Phase 3 has run:

> Same Claude Sonnet 4.6, cubebox scores XX% on SWE-bench Verified — Y
> points above OpenHands, Z points above SWE-agent.

If we lose, we say so internally and figure out why (Phase 3 is harness
optimization). We do not publish until cubebox is at least
"competitive within noise" with the top non-Anthropic baseline.

## Phased plan

### Phase 0 — Foundation (done 2026-06-23)

- ✅ API key feature merged (PR #270 — `sk-` Bearer tokens).
- ✅ Test-env overlay merged (PR #269 — `.test.env` for local rustfs).
- ✅ Hello-world verification: API → conversation → SSE → tool calls →
  patch extraction.
- ✅ SSE format documented above.

### Phase 1 — Mini-SWE-bench sanity (next, 1–2 days)

- Wire the per-task drive pattern in a Python harness
  (`benchmarks/runner/`).
- Run on the SWE-bench Verified **50-task mini subset** (lite shard, no
  django bulk).
- Surface mode-of-failure histogram: which tasks broke and where (no
  git clone, venv install fail, tests-not-running, no patch, wrong
  patch, etc.).
- **Exit criterion**: ≥30 / 50 tasks reach `patch.diff` non-empty.
  Score is secondary; what we're testing is plumbing.

### Phase 2 — Full SWE-bench Verified baseline (2–4 days)

- Run all 500 with **no harness changes** beyond what's in main.
- This is the **baseline score**. It establishes the floor that any
  optimization work in Phase 3 has to beat.
- Publish internally only.

### Phase 3 — Harness optimization (open-ended, the marketing chase)

Possible levers, in rough order of expected impact:

- **Planning loop**: split task into reproduce → localise → patch →
  verify sub-phases instead of one open-ended agent loop.
- **Context management**: aggressive file-content compression when
  context > 60% of window (we already see `context_tokens` /
  `context_window` in SSE).
- **Tool design**: a more efficient `edit_file` (str-replace vs
  full-rewrite) is consistently worth points.
- **Sub-agent**: spawn a sub-conversation for "read this 30-file
  module" so context doesn't pollute the main loop. cubepi supports
  this; cubebox just needs to expose it.
- **Memory**: write learnings to org-scoped memory (`memory.py`) and
  let later tasks read them.

Each lever is one PR + one re-run of Phase 2 + a one-line entry in
`REPORT.md`. Stop when we plateau for 2 consecutive Phase-2 cycles.

### Phase 4 — τ-bench (parallel with Phase 3, ~1 week)

- Same drive pattern + simulator integration.
- Run retail + airline domains.
- Phase-2-style baseline + Phase-3-style optimizations.

### Phase 5 — CI regression (after Phase 3 and 4 plateau)

- Mini-SWE-bench (50 tasks) + τ-bench retail-small (~25 tasks) on every
  release-tagged commit.
- Latency / cost / score deltas tracked in Grafana.
- Block release on >2 point regression.

## Open questions / decisions to make

1. **Which model preset for the headline run?** `flash` is the default
   tier in dev config and uses arkcode DeepSeek-V4. For the headline
   claim we likely want a single high-end vendor that matches what
   competitors publish on (Claude Sonnet 4.6 or 4.x). Decision needed
   before Phase 2 budget is approved.
2. **Internal LLM gateway vs direct vendor?** The internal gateway
   (`192.168.1.150:5001`) hides which underlying provider responds —
   not OK for a published number. Need to pin to direct `anthropic` /
   `bedrock` for the headline run; internal gateway is fine for Phase
   1.
3. **Real-LLM tagging in cubebox tests** — out of scope for this spec
   but adjacent: `@pytest.mark.real_llm` already exists per
   CLAUDE.md. The benchmark harness should NOT live in
   `backend/tests/` — it's its own top-level subproject, since it has
   different lifecycle and dependencies.
4. **Where does the harness code live?** Options: `benchmarks/`
   top-level subproject, separate repo, or `backend/scripts/benchmarks/`.
   Recommendation: top-level `benchmarks/` (new directory at repo
   root), so it can have its own `pyproject.toml`, its own
   `uv.lock`, and not pull in cubebox's full dev deps. Pinned cubebox
   commit becomes a sibling git submodule or just `pip install -e
   ../backend` for local dev.

## Out of scope

- A custom evaluator / re-implementing SWE-bench scoring.
- Latency benchmarks (TTFB / time-to-first-token under load).
- Multi-tenant fairness benchmarks (token billing under concurrent
  pressure).
- Cost-optimised harness variants (cheaper model in early phases,
  expensive only on fail). Useful but a Phase 6 conversation.
- A web UI for benchmark results.

## References

- Hello-world verification trace: this branch, `.test.env`-driven port
  8012; conversation `conv-1iUvRMfa5CuRE6` ran the 4-step toy task
  successfully end-to-end.
- Cubebox API surface audit: see commit log on `feat/2026-06-23-api-key`
  (PR #270) and the API key e2e tests for the Bearer-auth contract.
- SWE-bench Verified: <https://www.swebench.com/>
- τ-bench: Sierra Research repo, MIT licensed.
