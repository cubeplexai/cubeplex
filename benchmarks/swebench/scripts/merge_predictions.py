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
    # A resumed run appends new rows without rewriting old ones, so an
    # instance can appear multiple times (an empty-patch row from a
    # rate-limited attempt, then a real row on retry). Keep, per
    # instance_id, the row with the LONGEST model_patch (non-empty wins).
    best: dict[str, dict] = {}
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
            patch = row.get("model_patch") or ""
            prev = best.get(iid)
            if prev is None or len(patch) > len(prev.get("model_patch") or ""):
                best[iid] = row
    with merged.open("w", encoding="utf-8") as out:
        for row in best.values():
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
    nonempty = sum(1 for r in best.values() if (r.get("model_patch") or ""))
    print(f"[merge] wrote {len(best)} predictions ({nonempty} non-empty) to {merged}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
