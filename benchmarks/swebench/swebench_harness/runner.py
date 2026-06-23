"""Per-task driver.

Per task we:
  1. Create a fresh conversation.
  2. POST the rendered prompt as the user message; stream SSE; record
     every event verbatim to disk.
  3. Pull `patch.diff` from the sandbox.
  4. Append a row to `predictions.jsonl` in the official SWE-bench shape.

If any step fails (network, agent timed out, no patch produced), we
still write a result row — with an empty patch — and continue. Phase 1
goal is *plumbing coverage*, not score; per-task failures must not
sink the run.
"""

from __future__ import annotations

import json
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from swebench_harness.client import CubeboxAPIError, CubeboxClient
from swebench_harness.dataset import SWEBenchInstance
from swebench_harness.prompt import render_prompt


@dataclass(slots=True)
class TaskResult:
    instance_id: str
    conversation_id: str | None
    patch: str
    started_at: float
    finished_at: float
    sse_event_count: int
    tool_call_count: int
    usage: dict[str, int] = field(default_factory=dict)
    error: str | None = None
    last_assistant_text: str = ""

    @property
    def elapsed_seconds(self) -> float:
        return self.finished_at - self.started_at

    def to_prediction(self, model_name: str) -> dict[str, Any]:
        """SWE-bench official `predictions.jsonl` row format."""
        return {
            "instance_id": self.instance_id,
            "model_name_or_path": model_name,
            "model_patch": self.patch,
        }

    def to_summary(self) -> dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "conversation_id": self.conversation_id,
            "patch_bytes": len(self.patch),
            "sse_events": self.sse_event_count,
            "tool_calls": self.tool_call_count,
            "elapsed_seconds": round(self.elapsed_seconds, 2),
            "usage": self.usage,
            "error": self.error,
            "last_assistant_text": self.last_assistant_text[:500],
        }


def run_instance(
    client: CubeboxClient,
    instance: SWEBenchInstance,
    *,
    out_dir: Path,
    model_key: str | None = None,
    thinking: str = "off",
    cleanup_conversation: bool = False,
) -> TaskResult:
    """Drive a single instance end-to-end and write artifacts under
    ``out_dir/tasks/<instance_id>/``.

    Files written, even on failure:
      prompt.txt
      sse.jsonl          (every SSE event the agent emitted)
      patch.diff         (empty bytes if patch was never written)
      summary.json       (timings, token usage, error)
    """
    task_dir = out_dir / "tasks" / instance.instance_id
    task_dir.mkdir(parents=True, exist_ok=True)

    prompt = render_prompt(instance)
    (task_dir / "prompt.txt").write_text(prompt, encoding="utf-8")

    started = time.time()
    conversation_id: str | None = None
    patch_bytes = b""
    sse_event_count = 0
    tool_call_count = 0
    usage: dict[str, int] = {}
    last_text_buf: list[str] = []
    error: str | None = None

    try:
        conversation_id = client.create_conversation(title=instance.instance_id)
        sse_path = task_dir / "sse.jsonl"
        with sse_path.open("w", encoding="utf-8") as sse_file:
            for event in client.send_message_sse(
                conversation_id,
                content=prompt,
                model_key=model_key,
                thinking=thinking,
            ):
                sse_file.write(json.dumps(event, ensure_ascii=False) + "\n")
                sse_event_count += 1
                etype = event.get("type")
                if etype == "tool_call":
                    tool_call_count += 1
                elif etype == "text_delta":
                    data = event.get("data") or {}
                    delta = data.get("content") or data.get("delta") or ""
                    if delta:
                        last_text_buf.append(delta)
                elif etype == "usage":
                    data = event.get("data") or {}
                    for k in ("input_tokens", "output_tokens",
                              "cache_read_tokens", "cache_write_tokens"):
                        if k in data:
                            usage[k] = usage.get(k, 0) + int(data[k])
                elif etype == "done":
                    data = event.get("data") or {}
                    session = (data.get("usage") or {}).get("session") or {}
                    for k in ("total_input_tokens", "total_output_tokens",
                              "total_cache_read_tokens", "total_cache_write_tokens"):
                        if k in session:
                            usage[k.removeprefix("total_")] = int(session[k])
                elif etype == "error":
                    error = json.dumps(event.get("data") or {})[:500]
                    break

        patch_path = f"/workspace/swebench/runs/{instance.instance_id}/patch.diff"
        try:
            patch_bytes = client.download_file(
                path=patch_path, conversation_id=conversation_id
            )
        except CubeboxAPIError as e:
            if e.status == 404:
                error = error or f"patch.diff missing (download 404): {e.body[:200]}"
            else:
                raise

    except Exception as e:  # noqa: BLE001 — Phase 1 wants soft failures
        error = error or f"{type(e).__name__}: {e}"
        (task_dir / "exception.txt").write_text(traceback.format_exc(), encoding="utf-8")

    finally:
        finished = time.time()

    (task_dir / "patch.diff").write_bytes(patch_bytes)
    result = TaskResult(
        instance_id=instance.instance_id,
        conversation_id=conversation_id,
        patch=patch_bytes.decode("utf-8", errors="replace"),
        started_at=started,
        finished_at=finished,
        sse_event_count=sse_event_count,
        tool_call_count=tool_call_count,
        usage=usage,
        error=error,
        last_assistant_text="".join(last_text_buf),
    )
    (task_dir / "summary.json").write_text(
        json.dumps(result.to_summary(), indent=2), encoding="utf-8"
    )

    if cleanup_conversation and conversation_id is not None:
        try:
            client.delete_conversation(conversation_id)
        except CubeboxAPIError:
            pass  # best-effort cleanup

    return result


def append_prediction(predictions_path: Path, result: TaskResult, model_name: str) -> None:
    """Append a single result to predictions.jsonl in the SWE-bench format."""
    predictions_path.parent.mkdir(parents=True, exist_ok=True)
    with predictions_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(result.to_prediction(model_name), ensure_ascii=False) + "\n")
