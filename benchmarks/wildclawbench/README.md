# cubeplex × WildClawBench harness

Drive cubeplex over its HTTP API on [WildClawBench](https://github.com/internlm/WildClawBench)'s
fixed 60-task suite, to get a number comparable to its reference harnesses
(OpenClaw / Claude Code / Codex / Hermes) **under the same model** — evidence that
cubeplex's harness extracts ≥ their capability.

Full design + rationale (why WildClawBench over Claw-SWE-Bench / ClawBench, the
OpenClaw-ecosystem caveat, phased plan, risks):
**`docs/dev/specs/2026-06-26-wildclawbench-integration-design.md`** (also snapshotted
to `~/benchmarks/wildclawbench/INTEGRATION-DESIGN.md` alongside the downloaded images).

## Status — Phase 0 done (2026-06-25), image-gated phases pending

Phase 0 (no docker image needed) is complete and verified:

- `wcb_harness/dataset.py` — parses WildClawBench task `.md`. **Verified: matches
  their own `src/utils/task_parser.py` field-for-field on all 60 tasks (0 diffs).**
- `wcb_harness/transcript.py` — cubeplex SSE → OpenClaw-JSONL transcript. **Verified
  against a real cubeplex SSE trace: their `load_transcript` reads it, and
  `extract_usage_from_jsonl`'s summed per-turn usage equals the SSE `done` event's
  session totals.**
- `tests/test_phase0.py` — locks in both validations (run below).

Findings from the real source/tasks:
- 27/60 tasks declare skills; the dominant one is `agent-browser` (an npm CLI +
  SKILL.md, installed via a `npm install -g agent-browser` warmup) — cubeplex-friendly
  (warmup installs the tool, cubeplex's SkillsMiddleware loads the SKILL.md).
- Grading is just "run the task's `grade(transcript, workspace_path)` in an env that
  has `/tmp_workspace`, the transcript, the task's Env vars, and the grade() deps".
  cubeplex's sandbox (on the WildClawBench image) IS such an env → grading can run
  **in the same sandbox via cubeplex `execute`**, no second container. See design §3.

Pending (need the downloaded images / a running cubeplex):
- Phase 1: load+inspect `wildclawbench-ubuntu`/`-hermes` images (execd bake-in,
  bundled-skill layout, deps), push to registry, smoke-test cubeplex on the image.
- Phase 2: one Code-Intelligence task end-to-end (incl. grading).
- Phase 3: a skills task. Phase 4: all 60, compare vs the 4 reference harnesses.

## Run the Phase 0 checks

```bash
# needs the WildClawBench source repo cloned (images NOT required):
#   git clone --depth 1 https://github.com/internlm/WildClawBench ~/benchmarks/wildclawbench/repo
WCB_REPO=~/benchmarks/wildclawbench/repo \
CUBEPLEX_SSE=<path/to/any/non-empty/sse.jsonl> \
python benchmarks/wildclawbench/tests/test_phase0.py
```

(`pyyaml` + `python-dotenv` required; reuse the swebench `.venv`.)

## Layout

```
benchmarks/wildclawbench/
  wcb_harness/
    dataset.py      task .md parser (mirrors WildClawBench's, verified)
    transcript.py   cubeplex SSE → OpenClaw JSONL (verified)
    # client.py / runner.py / grade.py — Phase 1+, reuse swebench client
  scripts/          # Phase 1+: bootstrap (image), run_all
  tests/test_phase0.py
```
