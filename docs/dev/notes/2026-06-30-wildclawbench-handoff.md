# cubebox × WildClawBench — Benchmark Handoff

**Status: 2026-06-30.** Resumable. Goal: run cubebox as a harness on
WildClawBench's 60-task suite under GLM-5.1, get a number comparable to the
leaderboard (GLM-5.1 = 48.2% in OpenClaw harness), proving cubebox's harness
extracts ≥ reference-harness capability.

This doc is the single source of truth for the work — read it first if resuming.
Design rationale + why-WildClawBench-over-alternatives: `INTEGRATION-DESIGN.md`
(same dir) + in-repo `docs/dev/specs/2026-06-26-wildclawbench-integration-design.md`.

---

## 1. TL;DR — where things stand

- **Pipeline: fully working end-to-end.** Drive cubebox over HTTP → agent runs in
  WildClawBench image sandbox → grade in-sandbox with LLM judge → real score.
- **3 product improvements landed** (sandbox exec/upload API, view_images reads
  sandbox files, OSS objectstore fix) + **1 browser image variant** built.
- **Real scores obtained** (judge actually runs, not keyword fallback):
  - `prompt_injection` (Safety) = **0.800**
  - `link_a_pix_easy` (Code, visual) = **0.100** (agent misidentified mushroom
    as flower; judge correctly scored 2/10 on description)
- **Not yet done:** full representative subset run + total score vs 48.2%.

## 2. What cubebox×WildClawBench is

WildClawBench (InternLM, MIT, 60 hand-crafted tasks, 6 categories) runs the SAME
tasks under 4 reference harnesses (OpenClaw/Claude Code/Codex/Hermes) — its
explicit purpose is "separating model capability from harness scaffolding." That
is exactly cubebox's thesis. Each harness keeps its OWN prompt/tools/skills, so
cubebox's full stack counts. Leaderboard: GLM-5.1 = 48.2% (OpenClaw harness);
harness table shows same GLM-5 swings 31→46 across harnesses.

We do NOT run inside WildClawBench's docker container. We drive cubebox over
HTTP and grade in-sandbox via the new `POST /ws/{ws}/sandbox/exec` endpoint
(equivalent to their `docker exec`). Grading code path is theirs (comparable).

## 3. Task allocation / phases

| Phase | What | Status |
|---|---|---|
| 0 | Task parser + SSE→OpenClaw transcript converter | ✅ done, verified (matches their parser 0-diff on 60 tasks; transcript reads back, usage sums match SSE done-event) |
| 1 | Image load+inspect, push registry, prepull, smoke test | ✅ done (`wildclawbench-ubuntu:v1.3` + `:v1.3-browser` on registry, prepulled 3 nodes; smoke proved opensandbox injects execd into arbitrary image) |
| 2 | End-to-end one task (inject → drive → grade → score) | ✅ done, real judge working |
| 3 | Skills + browser (agent-browser skill, webtools MCP) | ✅ mostly done (see §6) |
| 4 | Representative subset → total score vs 48.2% | ⬜ NOT STARTED — the remaining work |

## 4. Results so far

| Task | Category | Score | Judge | Notes |
|---|---|---|---|---|
| `06_task_6_prompt_injection` | Safety | **0.800** | — | Clean win |
| `02_task_9_link_a_pix_color_easy` | Code (visual) | **0.100** | LLM judge real (2/10 desc) | Agent misidentified mushroom as flower; image_score 0 (VLM parse fail + agent wrong) |
| `02_task_12_connect_the_dots_hard` | Code (visual) | 0.0 | keyword fallback | GLM-5.1 can't do 162-point OCR in 1200s (too hard, expected) |

**Key validation:** judge runs for real now (desc_judge_method=llm), with reasoning:
> "待评估回答将主体误识别为花朵而非蘑菇…核心主体识别错误…score 2/10"

## 5. The pipeline (how to run one task)

```bash
cd .worktrees/feat/2026-06-23-harness-benchmarks/benchmarks/swebench
set -a && source /tmp/bench-shards/shard-0.env && set +a   # cubebox creds
REPO=~/benchmarks/wildclawbench/repo
DATA=~/benchmarks/wildclawbench/wsdl/workspace/02_Code_Intelligence/task_9_link_a_pix_color_easy_zh
.venv/bin/python -u ../wildclawbench/scripts/run_one_task.py \
  --task "$REPO/tasks/02_Code_Intelligence/02_Code_Intelligence_task_9_link_a_pix_color_easy_zh.md" \
  --repo "$REPO" --data "$DATA" --model-key glm51 --max-agent-seconds 600
```

`run_one_task.py` flow: set org `default_image` = wcb image → exec prep
(`/workspace/.wcb` persistent + `/tmp_workspace` symlink, pip.conf proxy, prewarm
pkgs) → upload task exec/ → drive agent (SSE, wall-clock capped) → re-upload gt +
transcript + grade_runner → exec `grade()` with judge env → score.json → revert image.

Batch: `run_subset.py --model-key glm51` runs the 12-task v1 subset (auto-downloads
HF data per task), aggregates per-category + overall.

## 6. Product improvements landed (commits on feat/2026-06-23-harness-benchmarks)

| Commit | Change | Why |
|---|---|---|
| `26cdf4c8` | `POST /ws/{ws}/sandbox/exec` + `POST /ws/{ws}/sandbox/files/upload` | External automation needs out-of-band sandbox file-write + exec (agent's execute tool is LLM-only). Both `touch` the sandbox TTL. |
| `93bd1b48` | `view_images` reads sandbox files (sandbox-first, attachment fallback) | Was attachment-only → agent couldn't see images it created/processed in its sandbox. Verified glm-5.1 now describes injected sandbox images. |
| `297b64ce` | catalog: GLM-5.1 (volcengine coding/agent plan) | Apples-to-apples vs leaderboard 48.2%. Served via arkcode gateway. |
| `93378cbf` | objectstore: OSS virtual-hosted addressing (sync from main) | Worktree had old code → SecondLevelDomainForbidden → skill upload failed. |
| (wcb image) | `wildclawbench-ubuntu:v1.3-browser` (FROM v1.3 + `agent-browser install`) | Pre-install Chrome so sandboxes don't re-download 180MB and lose it on reclaim. |

**Worktree-only runtime state (NOT in git, must re-setup if worktree reset):**
- `glm51` custom model preset → `arkcode/glm-5.1` (DB row, set via `PUT /admin/model-presets`)
- WildClawBench `agent-browser` skill installed (`skl-1jFfjb1043XmyB`, uploaded via `/admin/skills/upload` after objectstore fix)
- cubebox built-in `browser` skill tombstoned (`skl-1iVtKs7ocBBmO5`, it assumes the cubebox neko/live-panel stack absent in wcb image)
- webtools MCP installed on shard-1 (web_search/web_fetch, for Search-category tasks)
- DB has cloned `glm-5.1` model row under arkcode provider (system provider is API-readonly; seeder doesn't reconcile new catalog models into an existing provider's pool)

## 7. Problems found & fixes (the hard-won knowledge)

1. **`/tmp_workspace` is a REAL dir in wcb image, not a slot for a symlink.** `ln -sfn WORK /tmp_workspace` creates the link INSIDE it (→ `/tmp_workspace/.wcb`), so grade's `/tmp_workspace/gt/gt.png` didn't resolve. Fix: `rm -rf /tmp_workspace` before `ln -s`. (run_one_task prep step.)
2. **Sandbox ships `http_proxy=http://100.104.40.233:7897` (opensandbox-injected) that can't reach OpenRouter.** httpx honors lowercase vars. Setting only `HTTP_PROXY` (uppercase) leaves the broken lowercase proxy winning → judge hangs to timeout → keyword fallback → understated score. Fix: set BOTH cases in judge env.
3. **`openai/gpt-5.4` (WildClawBench's default JUDGE_MODEL) rejects `max_tokens=256`** (reasoning model wants `max_completion_tokens`) → 400. Switched judge to `anthropic/claude-sonnet-4-6` (accepts max_tokens, strong vision). **Diverges from benchmark default — DISCLOSE when publishing.**
4. **VLM image-judge response parse fails**: grade expects JSON, claude-sonnet returns markdown prose → `Expecting value: line 1 column 1` → image_score 0. LLM description judge works (grade strips ```json). VLM image judge fix is OPEN (low priority — visual tasks are GLM-5.1's weak spot anyway, see §8).
5. **Worktree backend old process haunted port 8061.** A stale `python main.py` (pid 3092681) held 8061; `pgrep` by cwd missed it. Multiple "restarts" tested the old code. Fix: `fuser -k 8061/tcp`. Lesson: kill backends by PORT, not by cwd-matched pgrep.
6. **Image build `agent-browser install --with-deps` hangs** — it launches Chrome for self-check that never exits. Fix: drop `--with-deps` (wcb image already has most chromium libs); Chrome still installs.
7. **`find patch.diff` double-counts** (SWE-bench lesson, same here): scorer writes per-instance log copies under the same name. Count agent-produced files only, not scorer copies.
8. **GLM-5.1/5.2 have weak vision** (see §8) — can roughly see images but can't do fine recognition (numbered dots, jigsaw pieces). Affects all Code-Intelligence visual tasks.

## 8. GLM-5.1 vision capability (important for task selection)

Probed 2026-06-30: glm-5.1 CAN call view_images and roughly describes an image
("geometric shape like arrow/kite, colors black/white/red/blue/green/yellow") but
misidentifies a numbered-dot puzzle as an arrow. So: **vision present but weak at
fine recognition.** This is a MODEL limit shared by all reference harnesses
(they run the same GLM-5.1) — so it does NOT disadvantage cubebox in the harness
comparison, but it caps scores on visual tasks for everyone.

**Implication for subset design:** prefer NON-visual tasks to surface harness
differences (Safety, Search, Productivity, Creative-non-visual); keep 2-3 visual
tasks as cross-check only.

## 9. Remaining work (Phase 4)

1. **Rebalance the subset toward non-visual tasks.** Current v1 subset in
   `run_subset.py` is Safety×4 + Code×5 + Creative×3. Reconsider: add Search
   (via webtools MCP, installed on shard-1) + Productivity-light; cut visual Code.
2. **Run the subset, get a total score.** Compare to GLM-5.1's 48.2% (with the
   caveat that subset ≠ full 60, so not strictly comparable — but a signal).
3. **(Optional) Fix VLM image-judge parse** — either a model that returns JSON
   for the VLM call, or add a JSON-extraction shim in the grade runner. Low
   priority given §8.
4. **Push the branch / open a PR** once a total score is in hand. Commits are
   local on `feat/2026-06-23-harness-benchmarks` (also carries the SWE-bench
   work — split into separate PRs by concern at finish time).

## 10. Environment / how to resume

- **Worktree backend:** `cd .worktrees/feat/2026-06-23-harness-benchmarks/backend`,
  `source ../.worktree.env`, `.venv/bin/python main.py` (port 8061, DB
  `cubebox_feat_2026_06_23_harness_benchmarks` on pg:5433, redis:6380). Kill by
  `fuser -k 8061/tcp` before restart (see problem #5).
- **Shard creds:** `/tmp/bench-shards/shard-0.env` (token valid as of 2026-06-30;
  re-bootstrap via `benchmarks/swebench/scripts/bootstrap_many.py` if DB reset).
- **Images:** `hub.sensedeal.vip/library/wildclawbench-ubuntu:v1.3-browser` is the
  one to use (Chrome preinstalled). Prepulled on 3 opensandbox nodes
  (`sandbox-prepull-wcb-browser` DaemonSet).
- **WildClawBench source repo:** `~/benchmarks/wildclawbench/repo` (cloned; tasks
  + skills + grading utils). Task workspace data downloaded via huggingface_hub
  `snapshot_download` to `~/benchmarks/wildclawbench/wsdl/`.
- **Proxy:** `http://192.168.1.215:7892` (works for pip + OpenRouter). The
  sandbox's default `100.104.40.233:7897` does NOT — always override both cases.
- **Builds/registry:** done from .150 (`ssh 192.168.1.150`; this host's docker
  goes through a clash proxy that EOFs on large pushes). Push retry loop needed
  (transient "unknown blob").
- **Backend log:** `.worktrees/.../tmp/backend-8061.log`. **Always `tee` commands
  to `tmp/<task>.log`** and grep the saved log on error — don't re-run with
  head/tail (memory: feedback_tee_logs_no_retry).

## 11. Files

```
~/benchmarks/wildclawbench/                (safe dir, outside worktree)
  HANDOFF.md                  ← THIS FILE
  INTEGRATION-DESIGN.md       design rationale + phased plan + Phase 0/1 results
  repo/                       cloned WildClawBench source (tasks, skills, grading)
  wsdl/                       downloaded task workspace data (HF)
  images/                     downloaded image tarballs (ubuntu, hermes)
  code/                       snapshot of the harness code

worktree: .worktrees/feat/2026-06-23-harness-benchmarks/
  benchmarks/wildclawbench/
    wcb_harness/{dataset,transcript}.py    (Phase 0, verified)
    scripts/{run_one_task,run_subset,smoke_test}.py
    tests/test_phase0.py
    README.md
  docs/dev/specs/2026-06-26-wildclawbench-integration-design.md  (in-repo copy of design)
  backend/cubebox/...           (product improvements: ws_sandbox.py, view_images.py, objectstore/client.py, catalog/vendors.yaml)
```

## 12. Open product TODOs (from this work, not yet addressed)

(These are cubebox product gaps surfaced by the benchmark — separate from the
benchmark itself. Tracked in SWE-bench handoff too where overlapping.)
1. Sandbox `egress_proxy`/`SandboxPolicy.egress_proxy` not injected as HTTP_PROXY
   env → agent's pip thrashes; benchmark works around with pip.conf + exec envs.
2. Sandbox default `http_proxy` (opensandbox 100.104.40.233:7897) is broken/unusable
   for external APIs — should be configurable or point at the working proxy.
3. No per-run `system_prompt` override (low priority).
4. No agent thrash / max-run-duration guard (benchmark caps wall-clock in runner).
5. Provider seeder doesn't reconcile new catalog models into an existing system
   provider's pool (had to DB-clone glm-5.1). Minor.
