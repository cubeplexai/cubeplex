#!/usr/bin/env python3
"""Orchestrate a full SWE-bench Verified run sharded across N cubeplex workspaces.

Each shard is a separate workspace (= its own sandbox PVC / pod), running a
slice of the 500 instances serially via `swebench-run`. Shards run in
parallel. Every shard:
  - uses --skip-done, so a re-launch resumes (only unfinished instances run)
  - uses --stop-on-rate-limit, so a 429/quota on the shared LLM endpoint
    aborts that shard (exit 3) instead of burning the rest of its slice

Workspaces are expected to be pre-bootstrapped (one env file per shard) — see
bootstrap_many.py. Pass the directory holding shard-0.env … shard-{N-1}.env.

Artifacts: runs/<run_name>/shard-<i>/ per shard. Merge predictions with
merge_predictions.py before scoring.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PKG_ROOT = HERE.parent  # benchmarks/swebench


def _load_env_file(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip()
    return env


def _all_instance_ids() -> list[str]:
    # Import lazily so the heavy datasets import only happens here.
    sys.path.insert(0, str(PKG_ROOT))
    from swebench_harness.dataset import load_verified_instances

    return [inst.instance_id for inst in load_verified_instances()]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Sharded full SWE-bench Verified runner.")
    ap.add_argument("--env-dir", required=True, type=Path,
                    help="Directory with shard-0.env … shard-{N-1}.env (from bootstrap_many.py).")
    ap.add_argument("--shards", type=int, required=True,
                    help="Number of shards (must match the env files present).")
    ap.add_argument("--model-key", default="max")
    ap.add_argument("--run-name", default="full-500")
    ap.add_argument("--egress-proxy", default="http://192.168.1.215:7892")
    ap.add_argument("--instances", nargs="*", default=None,
                    help="Override the instance set (default: all 500 Verified).")
    ap.add_argument("--out-root", default=str(PKG_ROOT / "runs"))
    args = ap.parse_args(argv)

    ids = args.instances or _all_instance_ids()
    print(f"[full] {len(ids)} instances across {args.shards} shards", flush=True)

    # Round-robin shard assignment keeps each shard's repo mix balanced (so no
    # single shard is all-django), which evens out wall-clock across shards.
    shard_ids: list[list[str]] = [[] for _ in range(args.shards)]
    for i, iid in enumerate(ids):
        shard_ids[i % args.shards].append(iid)

    procs: list[tuple[int, subprocess.Popen]] = []
    swebench_run = str(PKG_ROOT / ".venv" / "bin" / "swebench-run")
    for s in range(args.shards):
        env_file = args.env_dir / f"shard-{s}.env"
        if not env_file.exists():
            print(f"[full] ERROR missing {env_file}", file=sys.stderr)
            return 2
        shard_env = {**os.environ, **_load_env_file(env_file)}
        shard_env["CUBEPLEX_BENCH_EGRESS_PROXY"] = args.egress_proxy
        run_name = f"{args.run_name}/shard-{s}"
        log_path = Path(args.out_root) / args.run_name / f"shard-{s}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            swebench_run,
            "--model-key", args.model_key,
            "--run-name", run_name,
            "--out-root", args.out_root,
            "--egress-proxy", args.egress_proxy,
            "--skip-done",
            "--stop-on-rate-limit",
            "--instances", *shard_ids[s],
        ]
        logf = open(log_path, "a")  # noqa: SIM115 — handle lives for the subprocess lifetime
        p = subprocess.Popen(cmd, env=shard_env, stdout=logf, stderr=subprocess.STDOUT)
        procs.append((s, p))
        print(f"[full] shard {s}: pid={p.pid} n={len(shard_ids[s])} log={log_path}", flush=True)
        time.sleep(2)  # stagger starts so sandbox creates don't all hit at once

    # Wait for all shards. Report exit codes (3 = stopped on rate-limit).
    rate_limited = []
    for s, p in procs:
        rc = p.wait()
        tag = "RATE-LIMIT-STOP" if rc == 3 else ("OK" if rc == 0 else f"EXIT-{rc}")
        print(f"[full] shard {s} finished: {tag}", flush=True)
        if rc == 3:
            rate_limited.append(s)

    if rate_limited:
        print(f"[full] shards stopped on rate-limit: {rate_limited}. "
              f"Re-run the same command after the quota resets — --skip-done resumes.",
              flush=True)
    print(f"[full] all shards done. Merge with: "
          f"python scripts/merge_predictions.py --run {args.run_name} --shards {args.shards}",
          flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
