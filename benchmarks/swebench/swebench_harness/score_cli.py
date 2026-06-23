"""CLI entry: `swebench-score --run <run_name>` -> writes score.json."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from swebench_harness.score import DEFAULT_DATASET, DEFAULT_SPLIT, score


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="swebench-score",
        description="Run the SWE-bench official Docker scorer on a run directory.",
    )
    parser.add_argument(
        "--run",
        required=True,
        help="Run directory name under runs/, e.g. 'max-tier-smoke'.",
    )
    parser.add_argument(
        "--out-root",
        default=str(Path(__file__).resolve().parents[1] / "runs"),
        help="Root directory for run artifacts (default: ./runs/ under benchmarks/swebench).",
    )
    parser.add_argument("--dataset-name", default=DEFAULT_DATASET)
    parser.add_argument("--split", default=DEFAULT_SPLIT)
    parser.add_argument("--max-workers", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument(
        "--cache-level",
        default="env",
        choices=["none", "base", "env", "instance"],
        help="Scorer's Docker image cache level (default: env keeps the env image, "
        "drops the per-instance one).",
    )
    parser.add_argument(
        "--instance-ids",
        nargs="*",
        default=None,
        help="Restrict scoring to these instance ids (must be a subset of predictions.jsonl).",
    )
    args = parser.parse_args(argv)

    run_dir = Path(args.out_root) / args.run
    if not run_dir.exists():
        print(f"error: run directory not found: {run_dir}", file=sys.stderr)
        return 2

    result = score(
        run_dir=run_dir,
        dataset_name=args.dataset_name,
        split=args.split,
        max_workers=args.max_workers,
        instance_ids=args.instance_ids,
        timeout=args.timeout,
        cache_level=args.cache_level,
    )
    print()
    print(
        f"[score] {result.run_name}: "
        f"{result.resolved_count}/{result.submitted_count} attempted resolved "
        f"({result.pct_of_submitted:.1f}%) — "
        f"{result.pct_of_dataset:.2f}% of Verified ({result.dataset_count} total)"
    )
    if result.resolved:
        print(f"[score] resolved: {', '.join(result.resolved[:10])}"
              + (" …" if len(result.resolved) > 10 else ""))
    if result.unresolved:
        print(f"[score] unresolved: {', '.join(result.unresolved[:10])}"
              + (" …" if len(result.unresolved) > 10 else ""))
    if result.error:
        print(f"[score] error: {', '.join(result.error[:10])}"
              + (" …" if len(result.error) > 10 else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
