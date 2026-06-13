"""Pure CardKit JSON 2.0 rendering for cubebox Feishu output.

`render(state)` will be the only public IO-free entry point once Task 4
lands. For Task 2, only `optimize_markdown_style` is implemented.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

# Demote H1/H2 → H4/H5 (cardkit renders larger headings full-width and
# breaks the card layout). H3–H6 all collapse to H5 to keep visual rhythm
# consistent.
_H1_RE = re.compile(r"^(#)\s", re.MULTILINE)
_H2_RE = re.compile(r"^(##)\s", re.MULTILINE)
_H3_PLUS_RE = re.compile(r"^(#{3,6})\s", re.MULTILINE)

# Markdown table detection (header row + separator row).
_TABLE_RE = re.compile(
    r"(^\s*\|[^|\n]+\|.*\n\s*\|[-:\s|]+\|.*(?:\n\s*\|.*)*)",
    re.MULTILINE,
)

# Fenced code block — protected from rewrites.
_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)

# Image reference. CardKit only accepts `img_xxx` keys; any URL / path / data-uri
# image must be dropped before send to avoid error 200570.
_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
_VALID_IMAGE_KEY_RE = re.compile(r"^img_[A-Za-z0-9_-]+$")

# Citation markers: ASCII [N], [N-M] and full-width 【N-M】.
_ASCII_CITATION_RE = re.compile(r"\[(\d+(?:-\d+)?)\]")
_CN_CITATION_RE = re.compile(r"【(\d+(?:-\d+)?)】")


def optimize_markdown_style(
    text: str,
    *,
    citation_index: dict[str, tuple[str, str]] | None = None,
) -> str:
    """Sanitize cubepi markdown for Feishu CardKit's markdown element.

    Demotes headings, spaces tables, strips invalid image refs, and
    rewrites citation markers to inline links. Code blocks are protected
    from all rewrites.
    """
    citations = citation_index or {}

    # Stash code blocks behind sentinels so rewrites never touch them.
    fences: list[str] = []

    def _stash_fence(m: re.Match[str]) -> str:
        fences.append(m.group(0))
        return f"\x00FENCE{len(fences) - 1}\x00"

    body = _FENCE_RE.sub(_stash_fence, text)

    # Demote H3+ first so the H1→#### rewrite isn't re-matched by the H3+
    # rule on a second pass.
    body = _H3_PLUS_RE.sub("##### ", body)
    body = _H1_RE.sub("#### ", body)
    body = _H2_RE.sub("##### ", body)

    body = _TABLE_RE.sub(lambda m: f"<br>\n{m.group(1)}\n<br>", body)

    def _rewrite_image(m: re.Match[str]) -> str:
        target = m.group(2).strip()
        if _VALID_IMAGE_KEY_RE.match(target):
            return m.group(0)
        return m.group(1) or ""

    body = _IMAGE_RE.sub(_rewrite_image, body)

    def _resolve_first(label: str) -> str | None:
        first_id = label.split("-", 1)[0]
        entry = citations.get(first_id)
        return entry[0] if entry is not None else None

    def _rewrite_ascii_citation(m: re.Match[str]) -> str:
        label = m.group(1)
        url = _resolve_first(label)
        return f"[{label}]({url})" if url else m.group(0)

    def _rewrite_cn_citation(m: re.Match[str]) -> str:
        label = m.group(1)
        url = _resolve_first(label)
        return f"[{label}]({url})" if url else m.group(0)

    body = _ASCII_CITATION_RE.sub(_rewrite_ascii_citation, body)
    body = _CN_CITATION_RE.sub(_rewrite_cn_citation, body)

    # Restore code blocks verbatim.
    for i, fence in enumerate(fences):
        body = body.replace(f"\x00FENCE{i}\x00", fence)
    return body


_ARG_SUMMARY_CAP = 80


def _truncate(value: str, *, cap: int = _ARG_SUMMARY_CAP) -> str:
    if len(value) <= cap:
        return value
    return value[: cap - 1] + "…"


def summarize_args(args: dict[str, Any]) -> str:
    """Default args summary: JSON-flatten and truncate."""
    if not args:
        return ""
    try:
        compact = json.dumps(args, ensure_ascii=False, separators=(", ", ": "))
    except (TypeError, ValueError):
        compact = str(args)
    return _truncate(compact)


@dataclass(slots=True, frozen=True)
class ToolDisplay:
    """Per-tool rendering hints."""

    icon: str
    summarize: Callable[[dict[str, Any]], str]


def _default_summary(args: dict[str, Any]) -> str:
    return summarize_args(args)


def default_display(name: str) -> ToolDisplay:
    """Display for unregistered tools — generic icon + JSON summary."""
    _ = name  # accepted for future per-name fallback heuristics
    return ToolDisplay(icon="⚙️", summarize=_default_summary)


def _summary_read_file(args: dict[str, Any]) -> str:
    return _truncate(str(args.get("path", "")))


def _summary_write_file(args: dict[str, Any]) -> str:
    return _truncate(str(args.get("path", "")))


def _summary_bash(args: dict[str, Any]) -> str:
    return _truncate(str(args.get("cmd") or args.get("command", "")))


def _summary_web_fetch(args: dict[str, Any]) -> str:
    return _truncate(str(args.get("url", "")))


def _summary_update_memory(args: dict[str, Any]) -> str:
    return _truncate(str(args.get("key", "")))


def _summary_recall_memory(args: dict[str, Any]) -> str:
    return _truncate(str(args.get("query") or args.get("key", "")))


TOOL_DISPLAY: dict[str, ToolDisplay] = {
    "read_file": ToolDisplay(icon="📄", summarize=_summary_read_file),
    "write_file": ToolDisplay(icon="📝", summarize=_summary_write_file),
    "bash": ToolDisplay(icon="🖥️", summarize=_summary_bash),
    "web_fetch": ToolDisplay(icon="🌐", summarize=_summary_web_fetch),
    "web_search": ToolDisplay(icon="🔎", summarize=_summary_web_fetch),
    "update_memory": ToolDisplay(icon="🧠", summarize=_summary_update_memory),
    "recall_memory": ToolDisplay(icon="🧠", summarize=_summary_recall_memory),
}


__all__ = [
    "TOOL_DISPLAY",
    "ToolDisplay",
    "default_display",
    "optimize_markdown_style",
    "summarize_args",
]
