# cubebox SWE-bench harness

Drives cubebox over its public HTTP API to produce
[SWE-bench Verified](https://www.swebench.com/) predictions in the
official `predictions.jsonl` format.

Design rationale + phased plan live in
[`docs/dev/specs/2026-06-23-harness-benchmarks-design.md`](../../docs/dev/specs/2026-06-23-harness-benchmarks-design.md).
This is the Phase 1 plumbing — it produces predictions but does NOT run
the SWE-bench official scorer. Scoring is a separate step
(`swebench` Python package, see SWE-bench docs).

## Quick start

```bash
cd benchmarks/swebench
uv venv && source .venv/bin/activate
uv pip install -e .

# 1. One-shot bootstrap: registers a benchmark-only user, mints an API
#    key, sets the org SandboxPolicy to network_default_action=allow,
#    writes the resulting env vars to .env.benchmark.
python scripts/bootstrap.py --base-url http://127.0.0.1:8000

# 2. Load credentials.
set -a && source .env.benchmark && set +a

# 3. Smoke-test on a single instance.
swebench-run --instances psf__requests-1142 --model-key flash

# 4. Or sample the first N instances from the dataset.
swebench-run --limit 5 --model-key flash
```

If you already have an API key + workspace (e.g. you minted one in the
cubebox settings UI), skip bootstrap and just `export CUBEBOX_TOKEN=…`
yourself. But the SandboxPolicy must allow outbound HTTPS — see the
spec's "Phase 1 prerequisites" section.

Artifacts land under `runs/<YYYYMMDDTHHMMSSZ>-mini/`:

```
meta.json              ← config + cubebox identity for this run
predictions.jsonl      ← SWE-bench scorer input: {instance_id, model_name_or_path, model_patch}
summary.json           ← per-task timings, token usage, error counts
tasks/<instance_id>/
  prompt.txt           ← exact user message sent to cubebox
  sse.jsonl            ← raw SSE stream, one event per line
  patch.diff           ← extracted from the sandbox (empty if agent failed)
  summary.json         ← task-level metadata
  exception.txt        ← only on hard failure
```

## Scoring (manual, Phase 1)

Feed `predictions.jsonl` into the SWE-bench Docker harness:

```bash
pip install swebench
python -m swebench.harness.run_evaluation \
  --predictions_path runs/<run-name>/predictions.jsonl \
  --max_workers 4 \
  --run_id <run-name>
```

The Docker harness runs the test patch + your model patch in isolated
containers per instance and writes a per-instance pass/fail report.
Plan to wire this into the CLI in Phase 2.

## What the harness does NOT do

- It does NOT run the SWE-bench Docker scorer (yet).
- It does NOT retry failed instances. Phase 1 is pass@1 to match the
  leaderboard convention.
- It does NOT parallelise. Phase 1 runs serially; concurrency comes in
  Phase 2 when we have a baseline.
- It does NOT install/manage cubebox. Bring your own running instance
  + API key.
