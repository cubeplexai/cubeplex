"""AttachmentHintMiddleware port to cubepi (M3.a.1).

Reads attachments from cubepi.UserMessage.metadata["attachments"] (set by
wire_input_to_cubepi_user_message in convert.py) and renders them as a
[Attachments] text section appended to that message's text content.

Design note: every UserMessage with attachments is augmented — not just the
last one — so every historical turn's hint is reproduced byte-identically
on replay. The hint is appended in-place on a fresh TextContent block (or
merged into the last existing TextContent) so the original UserMessage is
never mutated.
"""

from __future__ import annotations

import asyncio
from typing import cast

from cubepi.agent.types import AgentContext
from cubepi.middleware.base import Middleware
from cubepi.providers.base import Message, TextContent, UserMessage


class AttachmentHintMiddleware(Middleware):
    """Inject [Attachments] hint into UserMessages before the LLM sees them."""

    async def transform_context(
        self,
        messages: list[Message],
        *,
        ctx: AgentContext,
        signal: asyncio.Event | None = None,
    ) -> list[Message]:
        del ctx, signal
        out: list[Message] = []
        for msg in messages:
            if isinstance(msg, UserMessage):
                raw_attachments = msg.metadata.get("attachments") if msg.metadata else None
                attachments = (
                    cast(list[dict[str, object]], raw_attachments)
                    if isinstance(raw_attachments, list)
                    else None
                )
                if attachments:
                    msg = _augment_with_hint(msg, attachments)
            out.append(msg)
        return out


def _format_size(n: int) -> str:
    if n < 1024:
        return f"{n}B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f}KB"
    return f"{n / (1024 * 1024):.1f}MB"


def render_attachments_hint(blocks: list[dict[str, object]]) -> str:
    """Render file_attachment blocks as an [Attachments] text section.

    Inlined from the deleted cubebox.agents.convert module (M6) since the
    cubepi runtime is the only consumer of this helper.
    """
    if not blocks:
        return ""
    lines = ["", "[Attachments]"]
    for b in blocks:
        kind = b.get("kind")
        filename = b.get("filename", "(unnamed)")
        size_raw = b.get("size_bytes", 0)
        size = int(size_raw) if isinstance(size_raw, int | float) else 0
        path = b.get("sandbox_path", "")
        if kind == "image":
            w = b.get("width")
            h = b.get("height")
            lines.append(
                f"- {filename} (image, {w}x{h}, {_format_size(size)})\n"
                f"  path: {path}\n"
                f"  hint: call view_images(paths=[...]) to inspect"
            )
        elif kind == "document":
            lines.append(
                f"- {filename} (document, {_format_size(size)})\n"
                f"  path: {path}\n"
                f"  hint: call file_read(path) to inspect"
            )
        else:
            lines.append(f"- {filename} ({_format_size(size)})\n  path: {path}")
    return "\n".join(lines)


def _augment_with_hint(msg: UserMessage, attachments: list[dict[str, object]]) -> UserMessage:
    """Return a fresh UserMessage with the [Attachments] hint appended.

    Builds a new object rather than mutating the original so that the
    persisted message history is never contaminated with the ephemeral hint.
    The hint is appended to the last TextContent block when one exists.
    """
    rendered = render_attachments_hint(attachments)
    if not rendered:
        return msg

    new_content = list(msg.content)
    appended = False
    for i in range(len(new_content) - 1, -1, -1):
        item = new_content[i]
        if isinstance(item, TextContent):
            new_content[i] = TextContent(text=item.text + rendered)
            appended = True
            break
    if not appended:
        new_content.append(TextContent(text=rendered))

    return UserMessage(
        content=new_content,
        timestamp=msg.timestamp,
        metadata=dict(msg.metadata),
    )
