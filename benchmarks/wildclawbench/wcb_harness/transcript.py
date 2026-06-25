"""Convert cubebox's SSE event stream into WildClawBench's OpenClaw JSONL transcript.

WildClawBench grades by running each task's `grade(transcript, workspace_path)`
against a transcript loaded from an OpenClaw-format `.jsonl` file. Their usage
accounting (`extract_usage_from_jsonl`) and any transcript-inspecting grader read
this exact shape, so the conversion must be faithful.

Target schema (one JSON object per line), derived from
`src/agents/hermesagent/compat_transcript.py` and `src/utils/grading.py`:

  assistant turn:
    {"type": "message",
     "message": {"role": "assistant",
                 "content": "<str>" | [ {"type":"text","text":...},
                                        {"type":"tool_use","name":...,"input":{...},"id":...} ],
                 "usage": {"input":N,"output":N,"cacheRead":N,"cacheWrite":N,
                           "totalTokens":N,"cost":{"total":F}}}}
  user turn:
    {"type": "message", "message": {"role":"user", "content":"<str>"}}
  tool result:
    {"type": "toolResult", "toolResult": {"content":"<str>", "tool_call_id":"<str>"}}

cubebox SSE events consumed (see runner.py / SWE-bench sse.jsonl):
  text_delta   data.content (stream of partial assistant text)
  tool_call    data.{tool_call_id,name,arguments}
  tool_result  data.{tool_call_id,name,content,is_error}
  usage        data.{input_tokens,output_tokens,cache_read_tokens,cache_write_tokens}
               — emitted once per assistant turn; we use it to CLOSE a turn.
  done         data.usage.session totals (ignored here; per-turn usage is enough)
"""

from __future__ import annotations

import json
from typing import Any, Iterable


def _convert_usage(d: dict[str, Any]) -> dict[str, Any]:
    inp = int(d.get("input_tokens", 0) or 0)
    out = int(d.get("output_tokens", 0) or 0)
    cr = int(d.get("cache_read_tokens", 0) or 0)
    cw = int(d.get("cache_write_tokens", 0) or 0)
    return {
        "input": inp,
        "output": out,
        "cacheRead": cr,
        "cacheWrite": cw,
        # totalTokens = non-cache billable tokens; cache counts are tracked
        # separately by extract_usage_from_jsonl, so don't double-count them here.
        "totalTokens": inp + out,
        "cost": {"total": 0.0},  # cubebox SSE carries no price; fill post-hoc if needed
    }


def _stringify(content: Any) -> str:
    if isinstance(content, str):
        return content
    return json.dumps(content, ensure_ascii=False)


def sse_to_openclaw_records(
    events: Iterable[dict[str, Any]],
    *,
    prompt: str | None = None,
) -> list[dict[str, Any]]:
    """Convert an ordered iterable of cubebox SSE event dicts to OpenClaw records.

    If `prompt` is given, a leading user message is emitted (the SSE stream does
    not echo the user's prompt back).
    """
    records: list[dict[str, Any]] = []
    if prompt is not None:
        records.append({"type": "message", "message": {"role": "user", "content": prompt}})

    blocks: list[dict[str, Any]] = []
    text_parts: list[str] = []

    def flush_text() -> None:
        if text_parts:
            blocks.append({"type": "text", "text": "".join(text_parts)})
            text_parts.clear()

    def flush_assistant(usage: dict[str, Any] | None) -> None:
        flush_text()
        if not blocks and usage is None:
            return
        # OpenClaw content is a string when there's only plain text, else a block list.
        if len(blocks) == 1 and blocks[0]["type"] == "text":
            content: Any = blocks[0]["text"]
        else:
            content = list(blocks)
        msg: dict[str, Any] = {"role": "assistant", "content": content}
        if usage is not None:
            msg["usage"] = usage
        records.append({"type": "message", "message": msg})
        blocks.clear()

    for ev in events:
        etype = ev.get("type")
        data = ev.get("data") or {}
        if etype == "text_delta":
            text_parts.append(data.get("content", "") or "")
        elif etype == "tool_call":
            flush_text()
            blocks.append(
                {
                    "type": "tool_use",
                    "name": data.get("name", ""),
                    "input": data.get("arguments", {}) or {},
                    "id": data.get("tool_call_id", ""),
                }
            )
        elif etype == "usage":
            # A usage event marks the end of an assistant turn.
            flush_assistant(_convert_usage(data))
        elif etype == "tool_result":
            # Tool results follow the assistant turn that called them.
            records.append(
                {
                    "type": "toolResult",
                    "toolResult": {
                        "content": _stringify(data.get("content", "")),
                        "tool_call_id": data.get("tool_call_id", ""),
                    },
                }
            )
        # tool_call_delta (streaming args) and done are intentionally ignored:
        # the final tool_call event carries the complete arguments.

    # Flush any trailing assistant text not closed by a usage event.
    flush_assistant(None)
    return records


def write_openclaw_jsonl(records: list[dict[str, Any]], path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def convert_sse_file(sse_path: str, out_path: str, *, prompt: str | None = None) -> int:
    """Read a cubebox sse.jsonl, write an OpenClaw transcript .jsonl. Returns line count."""
    events: list[dict[str, Any]] = []
    with open(sse_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    records = sse_to_openclaw_records(events, prompt=prompt)
    write_openclaw_jsonl(records, out_path)
    return len(records)


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="cubebox SSE → OpenClaw JSONL transcript")
    ap.add_argument("sse", help="path to cubebox sse.jsonl")
    ap.add_argument("out", help="output OpenClaw transcript .jsonl path")
    ap.add_argument("--prompt", default=None, help="optional leading user prompt")
    args = ap.parse_args()
    n = convert_sse_file(args.sse, args.out, prompt=args.prompt)
    print(f"wrote {n} records → {args.out}")
