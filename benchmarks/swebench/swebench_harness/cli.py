"""Command-line entry: drive N SWE-bench Verified instances through cubeplex."""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
import time
from pathlib import Path

from swebench_harness.client import CubePlexClient, CubePlexConfig
from swebench_harness.dataset import load_verified_instances
from swebench_harness.runner import append_prediction, run_instance


def _utc_stamp() -> str:
    return _dt.datetime.now(_dt.UTC).strftime("%Y%m%dT%H%M%SZ")


def _env_or_die(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(f"error: {name} must be set in the environment", file=sys.stderr)
        sys.exit(2)
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="swebench-run",
        description="Drive cubeplex over its HTTP API to produce SWE-bench Verified predictions.",
    )
    parser.add_argument(
        "--instances",
        metavar="ID",
        nargs="*",
        default=None,
        help="Instance ids to run. If omitted, use the first N from the dataset (see --limit).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Cap the number of instances. Required when --instances is not given.",
    )
    parser.add_argument(
        "--model-key",
        default=None,
        help="cubeplex model_key to use (e.g. 'claude-sonnet-4-6'). Omit to use the workspace default.",
    )
    parser.add_argument(
        "--thinking",
        default="off",
        help="cubeplex `thinking` parameter on each message (default: off).",
    )
    parser.add_argument(
        "--out-root",
        default=str(Path(__file__).resolve().parents[1] / "runs"),
        help="Root directory for run artifacts (default: ./runs/ under benchmarks/swebench).",
    )
    parser.add_argument(
        "--run-name",
        default=None,
        help="Subdirectory name under --out-root. Default: {YYYYMMDDTHHMMSSZ}-mini.",
    )
    parser.add_argument(
        "--model-name",
        default=None,
        help="Name to record in predictions.jsonl `model_name_or_path`. "
        "Default: 'cubeplex-<model_key>-<utc>'.",
    )
    parser.add_argument(
        "--cleanup-conversation",
        action="store_true",
        help="Delete the conversation after each task. Off by default — useful for "
        "debugging via the UI.",
    )
    parser.add_argument(
        "--egress-proxy",
        default=os.environ.get("CUBEPLEX_BENCH_EGRESS_PROXY"),
        help="HTTP(S) proxy URL (e.g. http://192.168.1.215:7892) the agent should "
        "tell git/pip to use. Workaround for sandboxes whose direct outbound to "
        "GitHub is unstable. Defaults to $CUBEPLEX_BENCH_EGRESS_PROXY.",
    )
    parser.add_argument(
        "--skip-done",
        action="store_true",
        help="Skip instances that already have a non-empty patch.diff under the "
        "run dir. Makes a long run resumable: re-launch the same command after "
        "a crash and only the unfinished instances run.",
    )
    parser.add_argument(
        "--max-task-seconds",
        type=float,
        default=2100.0,
        help="Hard wall-clock cap per instance (default 2100s = 35 min). The idle "
        "watchdog only catches a SILENT stream; this catches an agent that "
        "thrashes without converging (e.g. 53 min / 0-byte patch). Set 0 to disable.",
    )
    parser.add_argument(
        "--stop-on-rate-limit",
        action="store_true",
        help="Abort the whole sweep (exit 3) the first time an instance fails with "
        "a 429 / quota / rate-limit error, instead of recording a MISS and "
        "continuing. Use for shared LLM endpoints with hard quotas.",
    )
    args = parser.parse_args(argv)

    if not args.instances and args.limit is None:
        parser.error("Pass --instances or --limit.")

    cfg = CubePlexConfig(
        base_url=_env_or_die("CUBEPLEX_BASE_URL"),
        token=_env_or_die("CUBEPLEX_TOKEN"),
        workspace_id=_env_or_die("CUBEPLEX_WS"),
    )
    client = CubePlexClient(cfg)
    me = client.whoami()
    print(f"[bench] cubeplex: base={cfg.base_url} user={me.get('email','?')} ws={cfg.workspace_id}", flush=True)

    run_name = args.run_name or f"{_utc_stamp()}-mini"
    out_dir = Path(args.out_root) / run_name
    out_dir.mkdir(parents=True, exist_ok=True)
    predictions_path = out_dir / "predictions.jsonl"
    summary_path = out_dir / "summary.json"

    model_name = args.model_name or f"cubeplex-{args.model_key or 'default'}-{_utc_stamp()}"
    (out_dir / "meta.json").write_text(
        json.dumps(
            {
                "started_at": _utc_stamp(),
                "model_key": args.model_key,
                "model_name": model_name,
                "thinking": args.thinking,
                "instances_arg": args.instances,
                "limit": args.limit,
                "cubeplex_base_url": cfg.base_url,
                "cubeplex_user": me.get("email"),
                "cubeplex_workspace": cfg.workspace_id,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    instances = list(
        load_verified_instances(instance_ids=args.instances, limit=args.limit)
    )
    print(f"[bench] resolved {len(instances)} instance(s) to run", flush=True)

    _RATE_LIMIT_MARKERS = ("429", "rate limit", "rate_limit", "ratelimit", "quota",
                           "too many requests", "exhausted", "accountquota",
                           "accountratelimit", "model_failover_exhausted")

    summaries: list[dict[str, object]] = []
    skipped = 0
    t0 = time.time()
    for i, inst in enumerate(instances, 1):
        if args.skip_done:
            existing = out_dir / "tasks" / inst.instance_id / "patch.diff"
            if existing.exists() and existing.stat().st_size > 0:
                skipped += 1
                print(f"[bench] {i}/{len(instances)} {inst.instance_id} SKIP (already done)", flush=True)
                continue
        print(
            f"[bench] {i}/{len(instances)} {inst.instance_id} ({inst.repo}@{inst.base_commit[:8]})",
            flush=True,
        )
        result = run_instance(
            client,
            inst,
            out_dir=out_dir,
            model_key=args.model_key,
            thinking=args.thinking,
            cleanup_conversation=args.cleanup_conversation,
            egress_proxy=args.egress_proxy,
            max_task_seconds=(args.max_task_seconds or None),
        )
        append_prediction(predictions_path, result, model_name)
        summary = result.to_summary()
        summaries.append(summary)
        marker = "OK " if not result.error and len(result.patch) > 0 else "MISS"
        print(
            f"[bench] {marker} {inst.instance_id} "
            f"elapsed={summary['elapsed_seconds']}s "
            f"events={summary['sse_events']} "
            f"tools={summary['tool_calls']} "
            f"patch_bytes={summary['patch_bytes']} "
            f"usage={summary['usage']}",
            flush=True,
        )
        if args.stop_on_rate_limit and result.error:
            low = result.error.lower()
            if any(m in low for m in _RATE_LIMIT_MARKERS):
                print(
                    f"[bench] STOP: rate-limit/quota error on {inst.instance_id}: "
                    f"{result.error[:200]}",
                    flush=True,
                )
                return 3

    elapsed = time.time() - t0
    aggregate = {
        "run_name": run_name,
        "total_instances": len(instances),
        "ran": len(summaries),
        "skipped_already_done": skipped,
        "with_nonempty_patch": sum(1 for s in summaries if int(s["patch_bytes"]) > 0),
        "with_error": sum(1 for s in summaries if s["error"]),
        "elapsed_seconds": round(elapsed, 2),
        "tasks": summaries,
    }
    summary_path.write_text(json.dumps(aggregate, indent=2), encoding="utf-8")
    print(
        f"[bench] done: {aggregate['with_nonempty_patch']}/{len(instances)} "
        f"non-empty patches in {elapsed:.1f}s",
        flush=True,
    )
    print(f"[bench] predictions: {predictions_path}", flush=True)
    print(f"[bench] artifacts:   {out_dir}", flush=True)
    return 0
