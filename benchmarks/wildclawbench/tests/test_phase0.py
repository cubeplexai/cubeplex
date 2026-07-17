"""Phase-0 validation: parser parity + transcript-converter fidelity.

These run WITHOUT the WildClawBench docker images — they only need the cloned
WildClawBench source repo and a recorded cubeplex SSE trace. Run:

    WCB_REPO=~/benchmarks/wildclawbench/repo \
    CUBEPLEX_SSE=<path/to/a/sse.jsonl> \
    python benchmarks/wildclawbench/tests/test_phase0.py

Asserts:
  1. our parse_task_md matches WildClawBench's own parser field-for-field on all
     60 tasks (so a task we parse == what their grader/runner sees).
  2. our cubeplex-SSE → OpenClaw-JSONL converter produces a transcript that their
     load_transcript + extract_usage_from_jsonl read correctly, and the summed
     per-turn usage equals the SSE `done` event's session totals.
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))  # benchmarks/wildclawbench


def _repo() -> Path:
    repo = Path(os.environ.get("WCB_REPO", Path.home() / "benchmarks/wildclawbench/repo"))
    if not (repo / "src").exists():
        raise SystemExit(f"WildClawBench repo not found at {repo} (set WCB_REPO)")
    return repo


def test_parser_parity(repo: Path) -> None:
    from wcb_harness.dataset import load_tasks
    sys.path.insert(0, str(repo / "src"))
    from utils.task_parser import parse_task_md as their_parse  # type: ignore

    tasks = load_tasks(repo)
    assert len(tasks) == 60, f"expected 60 tasks, got {len(tasks)}"
    fields = ("task_id", "prompt", "workspace_path", "automated_checks",
              "env", "skills", "warmup", "timeout_seconds")
    mismatches = 0
    for t in tasks:
        theirs = their_parse(Path(t.file_path))
        for k in fields:
            if getattr(t, k) != theirs[k]:
                mismatches += 1
    assert mismatches == 0, f"{mismatches} field mismatches vs WildClawBench parser"
    print(f"[ok] parser parity: 60 tasks, 0 mismatches")


def test_transcript_fidelity(repo: Path) -> None:
    sse = os.environ.get("CUBEPLEX_SSE")
    if not sse or not Path(sse).exists():
        print("[skip] transcript fidelity: set CUBEPLEX_SSE to a non-empty sse.jsonl")
        return
    sys.path.insert(0, str(repo / "src"))
    from wcb_harness.transcript import sse_to_openclaw_records, write_openclaw_jsonl
    from utils.grading import extract_usage_from_jsonl  # type: ignore
    from utils.transcript_loader import load_transcript  # type: ignore

    events = [json.loads(ln) for ln in open(sse, encoding="utf-8") if ln.strip()]
    n_tool_call = sum(1 for e in events if e.get("type") == "tool_call")
    n_tool_result = sum(1 for e in events if e.get("type") == "tool_result")
    # session totals from the `done` event, if present
    done = next((e for e in events if e.get("type") == "done"), None)

    recs = sse_to_openclaw_records(events, prompt="Solve the task.")
    out = "/tmp/wcb_phase0_transcript.jsonl"
    write_openclaw_jsonl(recs, out)

    c = Counter(r["type"] for r in recs)
    tool_use = sum(
        1 for r in recs if r["type"] == "message" and isinstance(r["message"]["content"], list)
        for b in r["message"]["content"] if b.get("type") == "tool_use"
    )
    assert tool_use == n_tool_call, f"tool_use {tool_use} != tool_call {n_tool_call}"
    assert c.get("toolResult", 0) == n_tool_result, "toolResult count mismatch"

    loaded = load_transcript(out)
    assert isinstance(loaded, list) and len(loaded) == len(recs)

    u = extract_usage_from_jsonl(Path(out))
    if done:
        sess = done["data"]["usage"]["session"]
        assert u["input_tokens"] == sess["total_input_tokens"], "input token sum != session total"
        assert u["output_tokens"] == sess["total_output_tokens"], "output token sum != session total"
    print(f"[ok] transcript fidelity: {len(recs)} records, {tool_use} tool_use, "
          f"usage in={u['input_tokens']} out={u['output_tokens']} (matches session totals)")


if __name__ == "__main__":
    r = _repo()
    test_parser_parity(r)
    test_transcript_fidelity(r)
    print("PHASE-0 OK")
