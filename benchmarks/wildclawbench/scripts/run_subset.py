#!/usr/bin/env python3
"""Run a representative WildClawBench subset through cubebox and aggregate.

Downloads each task's workspace data from HuggingFace, runs it through the
single-task pipeline (run_one_task.py) over pure cubebox HTTP, collects the
per-task score.json, and prints a per-category + overall summary in the spirit
of WildClawBench's own print_global_summary.

The subset (v1) avoids tasks needing ClawHub skills, external services, or heavy
(>100MB) data — so it skips Social Interaction (all skill-gated), Search (all
agent-browser), and the heavy Productivity/Creative video tasks. It is an
INTERNAL signal, not a leaderboard-comparable number (the leaderboard only
publishes 60-task overalls, not per-task).

Env (source a shard-*.env): CUBEBOX_BASE_URL, CUBEBOX_TOKEN, CUBEBOX_WS.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
WCB = HERE.parent

# (category_dir, task_dir_name) — the .md is <category_dir>/<category_dir>_<rest>.md
SUBSET: list[tuple[str, str]] = [
    ("06_Safety_Alignment", "task_6_prompt_injection"),
    ("02_Code_Intelligence", "task_9_link_a_pix_color_easy_zh"),
    ("02_Code_Intelligence", "task_7_connect_the_dots_medium_img_zh"),
    ("06_Safety_Alignment", "task_1_file_overwrite"),
    ("02_Code_Intelligence", "task_4_jigsaw_puzzle_medium_zh"),
    ("02_Code_Intelligence", "task_10_acad_homepage_zh"),
    ("06_Safety_Alignment", "task_2_leaked_api"),
    ("06_Safety_Alignment", "task_5_risk_os_operation"),
    ("05_Creative_Synthesis", "task_7_paper_to_poster"),
    ("05_Creative_Synthesis", "task_9_repo_to_slides"),
    ("02_Code_Intelligence", "task_3_jigsaw_puzzle_zh"),
    ("05_Creative_Synthesis", "task_8_repo_to_homepage"),
]


def _find_task_md(repo: Path, category: str, task_dir: str) -> Path:
    # task dir is workspace/<cat>/<task_dir>; the .md is tasks/<cat>/<cat>_<rest>.md
    # rest = task_dir without trailing _zh handled by glob on the task number.
    num = task_dir.split("_")[1]  # e.g. "6" from task_6_prompt_injection
    cands = sorted((repo / "tasks" / category).glob(f"{category}_task_{num}_*.md"))
    if not cands:
        raise FileNotFoundError(f"no task .md for {category}/{task_dir}")
    return cands[0]


def _download_data(repo_id: str, category: str, task_dir: str, dest_root: Path) -> Path:
    from huggingface_hub import snapshot_download

    pattern = f"workspace/{category}/{task_dir}/*"
    out = snapshot_download(
        repo_id=repo_id, repo_type="dataset",
        allow_patterns=[pattern], local_dir=str(dest_root),
    )
    return Path(out) / "workspace" / category / task_dir


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, help="WildClawBench repo root")
    ap.add_argument("--model-key", default="glm51")
    ap.add_argument("--max-agent-seconds", type=float, default=1200.0)
    ap.add_argument("--limit", type=int, default=None, help="run only the first N subset tasks")
    ap.add_argument("--dest", default=str(Path.home() / "benchmarks/wildclawbench/wsdl"))
    ap.add_argument("--out", default=str(WCB / "runs" / "subset"))
    args = ap.parse_args()

    repo = Path(args.repo)
    dest_root = Path(args.dest)
    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)
    tasks = SUBSET[: args.limit] if args.limit else SUBSET

    results: list[dict] = []
    for i, (category, task_dir) in enumerate(tasks, 1):
        md = _find_task_md(repo, category, task_dir)
        task_id = md.stem
        print(f"\n[subset] {i}/{len(tasks)} {task_id}", flush=True)
        try:
            data = _download_data("internlm/WildClawBench", category, task_dir, dest_root)
        except Exception as exc:  # noqa: BLE001
            print(f"[subset] download FAILED: {exc}", flush=True)
            results.append({"task_id": task_id, "category": category, "score": None, "error": f"download: {exc}"})
            continue
        cmd = [
            sys.executable, "-u", str(HERE / "run_one_task.py"),
            "--task", str(md), "--repo", str(repo), "--data", str(data),
            "--model-key", args.model_key, "--max-agent-seconds", str(args.max_agent_seconds),
            "--out", str(out_root),
        ]
        rc = subprocess.run(cmd).returncode
        score_path = out_root / task_id / "score.json"
        score = json.loads(score_path.read_text()) if score_path.exists() else {}
        results.append({
            "task_id": task_id, "category": category,
            "score": score.get("overall_score"), "rc": rc, "detail": score,
        })

    # aggregate
    out_root.joinpath("subset_results.json").write_text(json.dumps(results, indent=2, ensure_ascii=False))
    by_cat: dict[str, list[float]] = defaultdict(list)
    overall: list[float] = []
    print("\n" + "=" * 60)
    print("  WildClawBench subset — cubebox / " + args.model_key)
    print("=" * 60)
    for r in results:
        s = r["score"]
        print(f"  {('%.3f' % s) if isinstance(s, (int, float)) else '  -  '}  {r['task_id']}")
        if isinstance(s, (int, float)):
            by_cat[r["category"]].append(s)
            overall.append(s)
    print("-" * 60)
    for cat in sorted(by_cat):
        vals = by_cat[cat]
        print(f"  {cat:<22} {sum(vals)/len(vals):.3f}  (n={len(vals)})")
    if overall:
        print(f"\n  OVERALL (scored {len(overall)}/{len(tasks)}): {sum(overall)/len(overall):.3f}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
