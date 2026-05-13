"""AttachmentHintMiddleware port to cubepi (M3.a.1).

Reads attachments from cubepi.UserMessage.metadata["attachments"] (set by
wire_input_to_cubepi_user_message in convert_pi.py) and renders them as a
[Attachments] text section appended to that message's text content.

Design note: every UserMessage with attachments is augmented — not just the
last one — mirroring the langgraph version which walks all HumanMessages.
The hint is appended in-place on a fresh TextContent block (or merged into
the last existing TextContent) so the original UserMessage is never mutated.
"""

from __future__ import annotations

from cubepi.middleware.base import Middleware
from cubepi.providers.base import Message, TextContent, UserMessage


class AttachmentHintMiddlewarePi(Middleware):
    """Inject [Attachments] hint into UserMessages before the LLM sees them."""

    async def transform_context(
        self, messages: list[Message], *, signal: object = None
    ) -> list[Message]:
        out: list[Message] = []
        for msg in messages:
            if isinstance(msg, UserMessage):
                attachments: list[dict[str, object]] | None = (
                    msg.metadata.get("attachments") if msg.metadata else None
                )
                if attachments:
                    msg = _augment_with_hint(msg, attachments)
            out.append(msg)
        return out


def _augment_with_hint(msg: UserMessage, attachments: list[dict[str, object]]) -> UserMessage:
    """Return a fresh UserMessage with the [Attachments] hint appended.

    Builds a new object rather than mutating the original so that the
    persisted message history is never contaminated with the ephemeral hint.
    The hint is appended to the last TextContent block when one exists,
    matching the behaviour of the langgraph version which does
    ``base + render_attachments_hint(meta)`` on the string content.
    """
    # Lazy import avoids a potential circular-import path through agents/__init__
    from cubebox.agents.convert import render_attachments_hint

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
