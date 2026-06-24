"""Wrap the SWE-bench official scorer.

Reads a `predictions.jsonl` under `runs/<run_name>/`, runs the official
Docker-based evaluator from the `swebench` PyPI package, and writes a
`score.json` summary into the same run directory.

The official scorer drops its own per-instance logs under
`logs/run_evaluation/<run_id>/<model>/...` AND a flat
`<model>.<run_id>.json` summary in the current working directory; we
absorb both into the run's artifact directory so a single
`runs/<run_name>/` tree contains everything for that submission.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from swebench_harness.patch_filter import clean_patch

DEFAULT_DATASET = "princeton-nlp/SWE-bench_Verified"
DEFAULT_SPLIT = "test"

# A local materialised copy of the Verified dataset. swebench's scorer loads
# the dataset by name; passing a .jsonl path makes it read this file instead
# of hitting the HF Hub. The HF unauthenticated path is rate-limited and
# intermittently returns an EMPTY dataset, which makes the scorer think every
# instance_id is "not found in dataset" and abort. Materialise once with:
#   python -c "from datasets import load_dataset; import json; \
#     [print(json.dumps(dict(r))) for r in load_dataset('princeton-nlp/SWE-bench_Verified', split='test')]" \
#     > runs/SWE-bench_Verified.jsonl
_LOCAL_DATASET = Path(__file__).resolve().parents[1] / "runs" / "SWE-bench_Verified.jsonl"


@dataclass(slots=True)
class ScoreResult:
    run_name: str
    submitted_count: int   # how many predictions we sent to the scorer
    dataset_count: int     # full dataset size (500 for Verified)
    resolved: list[str]
    unresolved: list[str]
    error: list[str]
    no_generation: list[str]
    completed: list[str]
    raw_report_path: Path | None

    @property
    def resolved_count(self) -> int:
        return len(self.resolved)

    @property
    def pct_of_submitted(self) -> float:
        return (self.resolved_count / self.submitted_count) * 100 if self.submitted_count else 0.0

    @property
    def pct_of_dataset(self) -> float:
        return (self.resolved_count / self.dataset_count) * 100 if self.dataset_count else 0.0


def _clean_predictions_in_place(path: Path) -> Path:
    """Rewrite `predictions.jsonl` with patches scrubbed of known noise.

    Returns a sibling `.cleaned.jsonl` path the scorer should consume.
    """
    out = path.with_suffix(".cleaned.jsonl")
    with path.open() as src, out.open("w") as dst:
        for line in src:
            row = json.loads(line)
            row["model_patch"] = clean_patch(row.get("model_patch", ""))
            dst.write(json.dumps(row, ensure_ascii=False) + "\n")
    return out


def score(
    *,
    run_dir: Path,
    dataset_name: str = DEFAULT_DATASET,
    split: str = DEFAULT_SPLIT,
    max_workers: int = 4,
    instance_ids: list[str] | None = None,
    timeout: int = 1800,
    cache_level: str = "env",
    namespace: str = "none",
) -> ScoreResult:
    """Score a single run directory. Blocks for the full scorer pipeline.

    ``namespace='none'`` (the default here) forces SWE-bench to BUILD the
    per-instance evaluation images locally from the base image instead of
    PULLING the prebuilt ``docker.io/swebench/sweb.eval.*`` images. On a
    network with flaky docker.io access this is the difference between
    reliable scoring and a wall of TLS-handshake-timeout errors. The base
    image build pulls ubuntu via whatever mirror the Docker daemon is
    configured with, which is far more reliable here.
    """
    # Resolve to absolute up front: the scorer subprocess runs with
    # cwd=run_dir, so any relative path (predictions, kwargs file) would
    # break against that working directory.
    run_dir = run_dir.resolve()
    predictions = run_dir / "predictions.jsonl"
    if not predictions.exists():
        raise FileNotFoundError(f"missing predictions: {predictions}")

    cleaned = _clean_predictions_in_place(predictions)
    run_id = run_dir.name  # the scorer needs a unique id; use the run name

    # Prefer the local materialised dataset over the HF name, unless the caller
    # explicitly overrode dataset_name. Avoids the HF-throttle-returns-empty
    # failure mode (every instance_id "not found in dataset").
    effective_dataset = dataset_name
    if dataset_name == DEFAULT_DATASET and _LOCAL_DATASET.exists():
        effective_dataset = str(_LOCAL_DATASET)

    # Invoke run_evaluation.main IN-PROCESS via _score_runner (subprocessed for
    # isolation). The `python -m swebench.harness.run_evaluation` CLI path
    # intermittently loads an empty dataset at line 521 and aborts with "Some
    # instance IDs not found in dataset!" — calling main(**kwargs) directly
    # with an explicit instance_ids list is reliable. Run from inside the run
    # dir so the scorer's incidental per-model summary JSON lands with our
    # artifacts.
    kwargs = {
        "dataset_name": effective_dataset,
        "split": split,
        "instance_ids": list(instance_ids) if instance_ids else None,
        "predictions_path": str(cleaned),
        "max_workers": max_workers,
        "force_rebuild": False,
        "cache_level": cache_level,
        "clean": False,
        "open_file_limit": 4096,
        "run_id": run_id,
        "timeout": timeout,
        "namespace": namespace,
        "rewrite_reports": False,
        "modal": False,
        "instance_image_tag": "latest",
        "env_image_tag": "latest",
        "report_dir": None,
    }
    kwargs_file = run_dir / ".score_kwargs.json"
    kwargs_file.write_text(json.dumps(kwargs), encoding="utf-8")
    runner = Path(__file__).resolve().parent / "_score_runner.py"
    cmd = [sys.executable, str(runner), str(kwargs_file)]

    print(f"[score] in-process run_evaluation: dataset={effective_dataset} "
          f"n_ids={len(instance_ids) if instance_ids else 'all'} namespace={namespace}",
          flush=True)
    proc = subprocess.run(cmd, cwd=str(run_dir))
    if proc.returncode != 0:
        print(f"[score] scorer exited {proc.returncode}", file=sys.stderr)

    # Scorer's final summary is named `<model_name>.<run_id>.json` and is
    # written to cwd. Find the most recently produced one inside run_dir.
    report = _locate_report(run_dir, run_id)
    result = _parse_report(report, run_name=run_id) if report else ScoreResult(
        run_name=run_id,
        submitted_count=0,
        dataset_count=0,
        resolved=[],
        unresolved=[],
        error=[],
        no_generation=[],
        completed=[],
        raw_report_path=None,
    )

    out_path = run_dir / "score.json"
    out_path.write_text(
        json.dumps(
            {
                "run_name": result.run_name,
                "submitted_count": result.submitted_count,
                "dataset_count": result.dataset_count,
                "resolved_count": result.resolved_count,
                "pct_of_submitted": round(result.pct_of_submitted, 2),
                "pct_of_dataset": round(result.pct_of_dataset, 2),
                "resolved": result.resolved,
                "unresolved": result.unresolved,
                "error": result.error,
                "no_generation": result.no_generation,
                "completed": result.completed,
                "raw_report": str(result.raw_report_path) if result.raw_report_path else None,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return result


def _locate_report(run_dir: Path, run_id: str) -> Path | None:
    """Find the scorer's summary JSON under run_dir."""
    candidates = sorted(run_dir.glob(f"*.{run_id}.json"), key=lambda p: p.stat().st_mtime)
    if candidates:
        return candidates[-1]
    # Older versions put it under logs/.../report.json
    logged = list(run_dir.glob(f"logs/run_evaluation/{run_id}/**/report.json"))
    if logged:
        return sorted(logged, key=lambda p: p.stat().st_mtime)[-1]
    return None


def _parse_report(report_path: Path, *, run_name: str) -> ScoreResult:
    data = json.loads(report_path.read_text())

    def as_list(key: str) -> list[str]:
        v = data.get(key, [])
        return [str(x) for x in v] if isinstance(v, list) else []

    submitted_count = int(
        data.get("submitted_instances", 0) or len(as_list("submitted_ids"))
    )
    dataset_count = int(data.get("total_instances", 0)) or submitted_count

    return ScoreResult(
        run_name=run_name,
        submitted_count=submitted_count,
        dataset_count=dataset_count,
        resolved=as_list("resolved_ids") or as_list("resolved"),
        unresolved=as_list("unresolved_ids") or as_list("unresolved"),
        error=as_list("error_ids") or as_list("error"),
        no_generation=as_list("empty_patch_ids") or as_list("no_generation"),
        completed=as_list("completed_ids") or as_list("completed"),
        raw_report_path=report_path,
    )


# Kept here too so callers using clean_patch directly don't need to also
# import patch_filter — single import for "anything score-time".
__all__ = ["score", "ScoreResult", "clean_patch"]
