#!/usr/bin/env python3
"""Merge per-shard predictions.jsonl into one run-level predictions.jsonl.

After merging, score the merged dir with:
  swebench-score --run <run_name>
(the scorer reads runs/<run_name>/predictions.jsonl)
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
PKG_ROOT = HERE.parent


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True, help="Run name, e.g. full-500")
    ap.add_argument("--shards", type=int, required=True)
    ap.add_argument("--out-root", default=str(PKG_ROOT / "runs"))
    args = ap.parse_args(argv)

    run_dir = Path(args.out_root) / args.run
    merged = run_dir / "predictions.jsonl"
    seen: set[str] = set()
    n = 0
    with merged.open("w", encoding="utf-8") as out:
        for s in range(args.shards):
            shard_pred = run_dir / f"shard-{s}" / "predictions.jsonl"
            if not shard_pred.exists():
                print(f"[merge] shard {s}: no predictions.jsonl (skipping)")
                continue
            for line in shard_pred.read_text().splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                iid = row.get("instance_id")
                if iid in seen:
                    continue  # last-writer would dup; keep first
                seen.add(iid)
                out.write(json.dumps(row, ensure_ascii=False) + "\n")
                n += 1
    print(f"[merge] wrote {n} predictions to {merged}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
