# cubeplex × WildClawBench — Integration Design (DRAFT, pre-image-exploration)

Status: 2026-06-25, written while the OpenClaw/Hermes images download (~6h @ 1MB/s).
Some specifics are gated on inspecting the images (marked ⛳ OPEN). The architecture
below is grounded in the REAL repo (`internlm/WildClawBench`) source, not summaries.

---

## 0. Goal

Run cubeplex as a harness ("scaffold") on WildClawBench's fixed 60-task suite, to get
a number directly comparable to the 4 reference harnesses (OpenClaw, Claude Code,
Codex CLI, Hermes) **under the same model** — proving cubeplex's harness extracts ≥
their capability. WildClawBench is favorable: it keeps each harness's OWN prompt +
tools + skills (it explicitly "separates model capability from harness scaffolding"),
and cubeplex can use ClawHub skills (the others mostly can't), so cubeplex sits in a
sweet spot: ClawHub skills + its own middleware/memory/MCP.

## 1. What WildClawBench actually is (from source)

- **Tasks**: 60 markdown files, 6 categories. Frontmatter `id/name/category/
  timeout_seconds/modality`. Sections: Prompt (the human input; inputs in
  `/tmp_workspace/`, outputs to `/tmp_workspace/results/`), Expected Behavior
  (human-only), Grading Criteria, **Automated Checks** (an embedded python
  `grade(transcript, workspace_path) -> dict` with an `overall_score` key),
  Workspace Path, **Skills** (ClawHub skill names to inject, may be empty),
  **Env** (env var names sourced from .env), **Warmup** (shell cmds before agent).
- **Adapter contract** (`src/agents/base.py`): `class BaseAgent(ABC)` with
  - props: `expects_gateway: bool`, `transcript_container_path: str`
  - `run_task(spec: AgentTaskSpec) -> AgentExecution`
    (spec: task_id, task dict, workspace_path, prompt, timeout_seconds, output_dir,
     model, optional thinking/models_config/lobster; returns elapsed_time, error,
     optional proc handles)
  - `collect_usage(task_id, output_dir, elapsed_time) -> dict`
  - optional `prepare_grading_transcript()`.
- **Reference harnesses** live at `src/agents/{openclaw,claudecode,codex,hermesagent}/
  runner.py`. Claude Code's runner is the closest template:
  - runs the agent INSIDE a docker container from `self.image`
    (`wildclawbench-claudecode-ubuntu:v0.2`); workspace mounted `:ro`, copied to
    writable `/tmp_workspace`.
  - feeds prompt: `./start.sh --add-dir /tmp_workspace -p {prompt} --model {model}`.
  - skills: `setup_skills(task_id, task.skills, task.skills_path,
    container_skills_root="/root/.claude/skills")` — copies bundled skill dirs in.
  - transcript: `docker cp {task_id}:/claude_code/log/chat.json` →
    `convert_claudecode_chat_to_openclaw_jsonl()`.
  - model via env: OPENROUTER_API_KEY / ANTHROPIC_API_KEY / *_BASE_URL.
    `expects_gateway = False`.
- **Grading** (`src/utils/grading.py`): runs IN the container — `docker cp` a
  `_grade_runner.py` in, `docker exec ... python3 _grade_runner.py`, parse stdout.
  Calls the task's `grade(transcript=load_transcript(...), workspace_path="/tmp_workspace")`.
  The LLM judge (when used, e.g. jigsaw's VLM check) lives INSIDE the task's grade()
  via OPENROUTER_API_KEY + JUDGE_MODEL — not a separate harness stage.
  score.json minimal schema: `{"overall_score": float, ...}`.
- **Skills are bundled in the repo**: `skills/<cat>/<name>/SKILL.md` (ClawHub format).
- **Run**: `bash script/run.sh <harness> --category all --parallel 4 --model <m>`.
  Output: `output/<harness>/<category>/<task_id>/<model_ts_runid>/{score.json,
  chat.jsonl, agent.log, usage.json}`.

## 2. The architectural tension

WildClawBench's model = ONE docker container per task; the agent runs IN it, and
grading `docker exec`s into the SAME container against `/tmp_workspace`.

cubeplex's model = an HTTP service whose agent runs in cubeplex's OWN sandbox
(opensandbox POD), with cubeplex tools (execute/edit_file/file_read, neko browser,
MCP, skills). cubeplex can set `SandboxPolicy.default_image`.

These don't natively align: cubeplex's agent won't run inside WildClawBench's local
docker container, and WildClawBench's grader can't `docker exec` into an opensandbox
pod.

## 3. Chosen approach — "bridge" harness (reuse their grading unchanged)

Implement `src/agents/cubeplex/runner.py : CubePlexAgent(BaseAgent)`. It does NOT run
an agent inside WildClawBench's container. Instead it drives cubeplex over HTTP and
then SYNCS cubeplex's sandbox output back into the WildClawBench container so the
existing grading driver runs unchanged.

`self.image` = `wildclawbench-ubuntu:v1.3` (reused only as the GRADING HOST — it has
the tasks' grade() deps: openai, Pillow, VLM access, etc.). The AGENT runs in
cubeplex's sandbox, on the SAME image, so it has identical tools.

`run_task(spec)`:
1. Ensure a cubeplex workspace whose `SandboxPolicy.default_image` = the wildclawbench
   image (⛳ OPEN: direct-use vs merged-image — §5).
2. Upload `spec.workspace_path` → cubeplex sandbox `/tmp_workspace` (cubeplex file
   upload; ⛳ confirm upload API / or tar+exec).
3. Install `spec.task["skills"]` into the cubeplex sandbox skills path
   (`/root/.claude/skills`): copy the bundled `skills/<cat>/<name>/` dirs (they're
   ClawHub SKILL.md — cubeplex's SkillsMiddleware + ClawhubAdapter already speak this).
4. Run `Warmup` shell cmds in the sandbox (cubeplex execute).
5. Pass `Env` var values (from .env) into the sandbox.
6. Drive cubeplex over HTTP (reuse benchmarks/swebench client): create conversation,
   post `spec.prompt`, stream SSE → save trace.
7. Convert cubeplex SSE → OpenClaw JSONL at `transcript_container_path`
   (⛳ need the OpenClaw JSONL schema — read `src/utils/transcript_loader.py` +
   `src/agents/hermesagent/compat_transcript.py`).
8. After the agent finishes: download cubeplex sandbox final `/tmp_workspace` →
   `docker cp` it INTO the WildClawBench grading container's `/tmp_workspace`, and
   place the converted transcript at `transcript_container_path`.
9. Return `AgentExecution(elapsed_time, error)`.

`collect_usage()` — map cubeplex usage events (input/output/cache tokens) to their
usage dict; cost from the model's price.

Then WildClawBench's `grading.py` runs unchanged (`docker exec` against the grading
container we populated). Output lands in WildClawBench's standard layout → directly
comparable to the 4 reference harnesses' entries.

### REVISED after reading their grading.py — single-sandbox, no bridge needed

`src/utils/grading.py::run_grading` is thin: it builds a `_grade_runner.py`
(= `load_transcript(path)` + the task's `automated_checks` source + `result =
grade(transcript, workspace_path="/tmp_workspace")` + print json), `docker cp`s it
+ `transcript_loader.py` into the container named `task_id`, then
`docker exec task_id python3 _grade_runner.py` with the task's Env vars injected,
and parses the JSON from stdout.

So grading needs only: an environment with (a) `/tmp_workspace` = agent output,
(b) the transcript file, (c) the task Env vars, (d) grade()'s python deps (openai,
Pillow, …, present in the WildClawBench image). **cubeplex's sandbox on the
WildClawBench image IS exactly that environment.** So we run grading right there via
cubeplex `execute` (our stand-in for `docker exec`):
  1. write the OpenClaw transcript to a path in the sandbox,
  2. write `_transcript_loader.py` (copy theirs) + the generated `_grade_runner.py`,
  3. `execute` `python3 _grade_runner.py` with Env vars exported,
  4. parse the JSON → score.json (their layout).

This collapses the design to a SINGLE environment (no WildClawBench docker
container, no cp/exec bridge, no double-sync) while staying byte-identical to their
grading — same grade(), same transcript, same workspace, same deps, same env. We
reimplement ~30 lines of run_grading using `execute`, and the aggregation
(global_avg = mean of per-task overall_score; see their print_global_summary).

Net: a STANDALONE harness (`benchmarks/wildclawbench/`, like benchmarks/swebench/)
that never touches their run.sh — it parses tasks, drives cubeplex, and grades
in-sandbox. Comparability is preserved because the scoring code path is theirs.

## 4. New artifacts (the actual work)

```
benchmarks/wildclawbench/                 (mirrors benchmarks/swebench/)
  wcb_harness/
    dataset.py        parse task .md (frontmatter + Prompt/Skills/Env/Warmup/
                      Workspace/AutomatedChecks)  — straightforward
    client.py         REUSE swebench CubePlexClient (HTTP+SSE) + file upload/download
    transcript.py     cubeplex SSE → OpenClaw JSONL converter   ⛳ schema-gated
    runner.py         CubePlexAgent.run_task / collect_usage (the bridge above)
    skills.py         map task Skills → cubeplex sandbox skills path
  scripts/
    bootstrap.py      cubeplex workspace w/ SandboxPolicy.default_image = wcb image
    run_all.py        iterate tasks (or hook into their run.sh as a new harness)
```
Plus, in the WildClawBench checkout: `src/agents/cubeplex/{__init__.py,runner.py}`
(thin shim that imports our wcb_harness.runner) + a `cubeplex` branch in
`script/run.sh` / `eval/run_batch.py`'s harness switch.

Reused from SWE-bench harness: HTTP+SSE client, the conversation/stream/download
plumbing, the egress-proxy handling, the sandbox-image bootstrap pattern.

## 5. ⛳ Open questions — image exploration results (2026-06-25)

1. **execd bake-in → RESOLVED: injected by opensandbox.** ✅ Our sandbox image is
   `FROM hub.sensedeal.vip/library/ubuntu:24.04` and its Dockerfile installs NO
   execd; sandboxes are created via `opensandbox.Sandbox.create()`. So execd is
   injected by opensandbox at pod creation, NOT baked. → **cubeplex can use
   `wildclawbench-ubuntu:v1.3` directly as `SandboxPolicy.default_image`; no merged
   image needed.** (Still must push it to hub.sensedeal.vip + prepull, ~13.5GB.)
   `wildclawbench-ubuntu:v1.3` contents (Ubuntu 22.04.5): python3, pip, node, npm,
   git, ffmpeg, `agent-browser` (baked at /usr/bin — the dominant skill's CLI, so its
   `npm install -g` warmup is already satisfied), `openclaw` CLI + openclaw built-in
   skills under /usr/lib/node_modules/openclaw/skills, `/root/.openclaw`. Env is clean
   (PATH only — NO PYTHONPATH/PIP_PREFIX poison, unlike our cubeplex image).
   GAPS: `openai`/`Pillow` (grade() deps) NOT in system python → install via task
   Warmup or a thin layer; `chromium` not on PATH (agent-browser may bundle its own);
   SAM3 weights not baked (likely fetched into the workspace by prepare.sh).
2. **Skills**: bundled dirs confirmed (`skills/<cat>/<name>/SKILL.md`). Confirm they
   parse cleanly via cubeplex `parse_skill_md` (metadata.openclaw nesting handled) and
   that their runtime deps are present in the image.
3. **OpenClaw JSONL transcript schema**: exact fields, so the SSE→JSONL converter is
   faithful enough for transcript-inspecting grade() functions.
4. **Image contents**: tools present (browser/neko? bash? email/calendar/video/SAM3),
   python deps for grade() (openai, Pillow, …), entrypoint, conflicting ENTRYPOINT/
   PYTHONPATH (recall our PYTHONPATH-vs-venv issue).
5. **opensandbox**: can it schedule an arbitrary 13GB image as a pod (pull time → the
   504 cold-start issue; prepull DaemonSet like SWE-bench); does cubeplex expose
   file upload + recursive download for `/tmp_workspace`.

## 6. Phased plan

- **Phase 0 (now, no image)**: this design; scaffold `benchmarks/wildclawbench/`
  skeleton by copying swebench structure; write `dataset.py` against the real task
  format; fetch transcript schema. ✅ doable now.
- **Phase 1 (image landed) — DONE 2026-06-26.** ✅ Loaded + inspected
  `wildclawbench-ubuntu:v1.3` (§5 q1 resolved: execd is opensandbox-injected).
  Pushed to `hub.sensedeal.vip/library/wildclawbench-ubuntu:v1.3` (from .150 — this
  host's docker goes through a clash proxy that EOFs on large pushes; .150 reaches
  the registry directly; needed a push retry to clear a transient "unknown blob").
  Prepulled on all 3 opensandbox nodes via DaemonSet `sandbox-prepull-wcb`.
  **Smoke test passed** (`scripts/smoke_test.py`): set org default_image → drove the
  agent's `execute` in a sandbox on the wcb image → got Ubuntu 22.04 + agent-browser/
  openclaw/python3/node/ffmpeg all present + EXECD_OK → reverted image. Confirms
  opensandbox injects execd into the wcb image and cubeplex drives it. No merged image
  needed. (Smoke used `lite` tier to avoid glm-5.2 quota; image revert is automatic.)
- **Phase 2 (one code task E2E)**: pick `02_Code_Intelligence` (no skills, closest to
  SWE-bench; e.g. jigsaw task_3 — needs openai+Pillow, VLM judge via JUDGE_MODEL).
  Full loop: upload workspace → run cubeplex → extract → grade → score.json. This is
  the smallest thing that proves the whole pipeline incl. grading.
- **Phase 3 (skills)**: a task with non-empty Skills (e.g. Productivity arxiv_digest
  → `agentic-paper-digest-skill`). Verify cubeplex installs/uses the ClawHub skill and
  the agent actually invokes it. Add a Search/Social task (browser/MCP).
- **Phase 4 (scale)**: all 60; compare cubeplex vs the 4 harnesses under the same
  model; write up. Watch the 47%-seed-noise warning — use their pass^k / repeat-trial.

## 7. Config / fairness notes

- **Same model**: the comparison only means something if cubeplex drives the SAME model
  the reference entries use (glm-5.x via OpenRouter). cubeplex's `arkagent/glm-5.2`
  (Volcengine plan) vs OpenRouter glm — pin the SAME provider/model or disclose the
  difference. ⛳ decide before Phase 4.
- **LLM judge**: tasks' grade() use JUDGE_MODEL (default gpt-5.4) via OPENROUTER —
  need an OpenRouter key for grading. Use the SAME judge as the reference entries.
- **Disclose**: cubeplex commit, model+provider, thinking, parallelism, which skills
  were available — for a publishable, comparable number.
- **Quota**: if driving glm via the Volcengine plan, the rolling-5h quota applies
  (see SWE-bench handoff); 60 tasks is small though.

## 8. Risk register

- Double-environment sync (cubeplex sandbox ↔ grading container) is the fiddliest bit;
  the standalone-grade fallback (§3) de-risks it.
- Transcript fidelity: if grade() functions inspect tool-calls in a format-specific
  way, the SSE→JSONL converter must match. Mitigate by reading their loader first.
- Image cold-start 504 (13GB) — prepull, as with SWE-bench.
- OpenClaw home-turf bias persists for skill-heavy tasks; the clean comparison is the
  Code subset + whatever cubeplex's own MCP/skills genuinely cover.
