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

from cubebox.im.feishu.card_model import (
    ArtifactItem,
    CardState,
    PendingInput,
    SubAgentRow,
    ToolStep,
)

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

# Citation markers: ASCII [N], [N-M] and full-width 【N-M】. The ASCII
# variant uses a negative lookahead so `[1](https://...)` markdown links
# in cubepi output don't get treated as citation markers and rewritten
# into broken `[1](resolved_url)(https://...)` double-parens.
_ASCII_CITATION_RE = re.compile(r"\[(\d+(?:-\d+)?)\](?!\()")
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


def _summary_execute(args: dict[str, Any]) -> str:
    # cubepi's sandbox.execute tool takes `command` (shell) or `script` (script body).
    return _truncate(str(args.get("command") or args.get("script") or args.get("cmd", "")))


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
    "execute": ToolDisplay(icon="🖥️", summarize=_summary_execute),
    "web_fetch": ToolDisplay(icon="🌐", summarize=_summary_web_fetch),
    "web_search": ToolDisplay(icon="🔎", summarize=_summary_web_fetch),
    "update_memory": ToolDisplay(icon="🧠", summarize=_summary_update_memory),
    "recall_memory": ToolDisplay(icon="🧠", summarize=_summary_recall_memory),
}


def _header(state: CardState) -> dict[str, Any]:
    if state.error:
        template = "red"
        subtitle = "运行失败"
    elif state.finalized:
        template = "green"
        subtitle = f"已完成 · {state.elapsed_ms / 1000:.1f}s" if state.elapsed_ms else "已完成"
    else:
        template = "blue"
        subtitle = "运行中…"
    return {
        "title": {"tag": "plain_text", "content": state.bot_name},
        "subtitle": {"tag": "plain_text", "content": subtitle},
        "template": template,
    }


def _markdown_element(state: CardState) -> dict[str, Any]:
    content = optimize_markdown_style(
        state.streaming_content,
        citation_index=state.citation_index,
    )
    if state.error and not state.streaming_content.endswith(state.error):
        content = f"{content}\n\n```text\n⚠️ {state.error}\n```"
    return {
        "tag": "markdown",
        "element_id": "streaming_content",
        "content": content,
    }


def _tool_panel_header_title(state: CardState) -> str:
    step_count = len(state.tool_steps)
    if step_count == 0:
        return "工具调用"
    any_failed = any(s.status == "failed" for s in state.tool_steps)
    any_running = any(s.status == "running" for s in state.tool_steps)
    total_ms = sum(s.elapsed_ms for s in state.tool_steps)
    duration = f" · {total_ms / 1000:.1f}s" if total_ms > 0 else ""
    if any_running:
        return f"运行中 · {step_count} step{duration}"
    if any_failed:
        return f"失败 · {step_count} step{duration}"
    return f"已完成 · {step_count} step{duration}"


def _format_result_block(step: ToolStep) -> str:
    if step.status == "failed":
        body = step.error or "(no error message)"
        return f"```text\n{body[:2000]}\n```"
    raw = step.result
    if raw is None:
        return ""
    if isinstance(raw, (dict, list)):
        try:
            body = json.dumps(raw, ensure_ascii=False, indent=2)
            return f"```json\n{body[:2000]}\n```"
        except (TypeError, ValueError):
            pass
    return f"```text\n{str(raw)[:2000]}\n```"


def _render_tool_step(step: ToolStep) -> list[dict[str, Any]]:
    display = TOOL_DISPLAY.get(step.name) or default_display(step.name)
    summary = display.summarize(step.args)
    title_md = f"{display.icon} **{step.name}**"
    if summary:
        title_md += f" · {summary}"
    parts: list[dict[str, Any]] = [
        {"tag": "div", "text": {"tag": "lark_md", "content": title_md}},
    ]
    if step.elapsed_ms > 0:
        parts.append(
            {
                "tag": "div",
                "margin": "0px 0px 0px 22px",
                "text": {"tag": "plain_text", "content": f"{step.elapsed_ms}ms"},
            }
        )
    result_block = _format_result_block(step)
    if result_block:
        parts.append(
            {
                "tag": "div",
                "margin": "0px 0px 0px 22px",
                "text": {"tag": "lark_md", "content": result_block},
            }
        )
    return parts


def _render_sub_agent_row(row: SubAgentRow) -> dict[str, Any]:
    return {
        "tag": "div",
        "text": {
            "tag": "lark_md",
            "content": f"🤖 sub-agent **{row.name}** · 已调用 {row.tool_count} 个工具",
        },
    }


def _tool_panel(state: CardState) -> dict[str, Any]:
    elements: list[dict[str, Any]] = []
    for sub in state.sub_agents:
        elements.append(_render_sub_agent_row(sub))
    for step in state.tool_steps:
        elements.extend(_render_tool_step(step))
    return {
        "tag": "collapsible_panel",
        "element_id": "tool_panel",
        "header": {
            "title": {
                "tag": "plain_text",
                "content": _tool_panel_header_title(state),
            },
        },
        "expanded": True,
        "elements": elements,
    }


def _render_artifact(art: ArtifactItem) -> dict[str, Any]:
    if art.artifact_type == "image" and art.image_key:
        return {
            "tag": "img",
            "img_key": art.image_key,
            "alt": {"tag": "plain_text", "content": art.name},
        }
    title_md = f"📎 **{art.name}** <text_tag color='blue'>{art.artifact_type}</text_tag>"
    if art.artifact_type == "html_widget":
        title_md = f"📊 **{art.name}** <text_tag color='purple'>预览</text_tag>"
    rows: list[dict[str, Any]] = [
        {"tag": "div", "text": {"tag": "lark_md", "content": title_md}},
    ]
    if art.description:
        rows.append({"tag": "div", "text": {"tag": "plain_text", "content": art.description[:200]}})
    if art.share_url:
        button_label = "在浏览器中打开" if art.artifact_type == "html_widget" else "查看"
        rows.append(
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": button_label},
                "type": "default",
                "behaviors": [{"type": "open_url", "default_url": art.share_url}],
            }
        )
    return {
        "tag": "interactive_container",
        "elements": rows,
    }


def _artifacts_panel(state: CardState) -> dict[str, Any]:
    return {
        "tag": "collapsible_panel",
        "element_id": "artifacts",
        "header": {
            "title": {
                "tag": "plain_text",
                "content": f"附件 · {len(state.artifacts)}",
            },
        },
        "expanded": True,
        "elements": [_render_artifact(a) for a in state.artifacts],
    }


def _render_pending_input(pending: PendingInput) -> dict[str, Any]:
    if pending.resolved_choice is not None:
        receipt = (
            f"✓ 已选择「{pending.resolved_choice}」"
            + (f" · 由 {pending.resolved_by_open_id} 操作" if pending.resolved_by_open_id else "")
            + (f" · {pending.resolved_at_iso}" if pending.resolved_at_iso else "")
        )
        return {
            "tag": "interactive_container",
            "element_id": "pending_input",
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": pending.question}},
                {"tag": "div", "text": {"tag": "lark_md", "content": receipt}},
            ],
        }
    columns: list[dict[str, Any]] = []
    for choice_key, btn_type in pending.choices:
        value_payload: dict[str, Any] = {
            "action": pending.kind,
            "run_id": pending.run_id,
            "choice": choice_key,
        }
        if pending.question_id:
            value_payload["question_id"] = pending.question_id
        if pending.answer_key:
            value_payload["answer_key"] = pending.answer_key
        columns.append(
            {
                "tag": "column",
                "elements": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": choice_key},
                        "type": btn_type,
                        "behaviors": [{"type": "callback"}],
                        "value": value_payload,
                    }
                ],
            }
        )
    body_elements: list[dict[str, Any]] = [
        {"tag": "div", "text": {"tag": "lark_md", "content": pending.question}},
    ]
    if columns:
        body_elements.append({"tag": "column_set", "columns": columns})
    else:
        body_elements.append({"tag": "div", "text": {"tag": "plain_text", "content": "(等待响应)"}})
    return {
        "tag": "interactive_container",
        "element_id": "pending_input",
        "elements": body_elements,
    }


def render(state: CardState) -> dict[str, Any]:
    """Project `CardState` into a CardKit JSON 2.0 payload.

    Empty panels are dropped so an in-progress run with no tools yet does
    not render an empty `tool_panel` slot.
    """
    elements: list[dict[str, Any]] = [_markdown_element(state)]
    if state.tool_steps or state.sub_agents:
        elements.append(_tool_panel(state))
    if state.artifacts:
        elements.append(_artifacts_panel(state))
    if state.pending_input is not None:
        elements.append(_render_pending_input(state.pending_input))
    return {
        "schema": "2.0",
        "header": _header(state),
        "config": {
            "streaming_mode": not state.finalized,
            "update_multi": True,
            "locales": ["zh_cn", "en_us"],
        },
        "body": {"elements": elements},
    }


__all__ = [
    "TOOL_DISPLAY",
    "ToolDisplay",
    "default_display",
    "optimize_markdown_style",
    "render",
    "summarize_args",
]
