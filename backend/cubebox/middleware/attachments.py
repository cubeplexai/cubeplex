"""AttachmentHintMiddleware — injects the [Attachments] hint at model-call time.

User messages with file attachments are persisted as plain text plus an
`additional_kwargs.attachments_meta` list (sandbox path, kind, size, etc.).
The LLM still needs to *see* the file paths and tool hints in-prompt — this
middleware materialises that hint on each model call without writing the
augmented content back into the checkpoint.

Why: keeping the persisted HumanMessage equal to what the user actually typed
means `convert_to_api_messages` doesn't need to strip a hint suffix, and old
attachment strings (`view_images` / `file_read` instructions) can be evolved
without rewriting historical messages.
"""

from collections.abc import Awaitable, Callable, Sequence
from typing import Any, cast

from langchain.agents.middleware.types import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
)
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import BaseTool


def _attachments_meta(msg: Any) -> list[dict[str, object]]:
    if not isinstance(msg, HumanMessage):
        return []
    meta = (msg.additional_kwargs or {}).get("attachments_meta") or []
    if not isinstance(meta, list):
        return []
    return [block for block in meta if isinstance(block, dict)]


def _augment_with_hint(msg: HumanMessage, meta: list[dict[str, object]]) -> HumanMessage:
    """Return a new HumanMessage with the [Attachments] hint appended.

    Builds a fresh object rather than mutating the original — `request.messages`
    in LangGraph is the same list as `state["messages"]`, so in-place edits
    would leak into the checkpointed history.
    """
    # Lazy import: cubebox.agents.__init__ imports graph.py, which imports this
    # module. Pulling render_attachments_hint at module-load time would close the
    # cycle.
    from cubebox.agents.convert import render_attachments_hint

    base = msg.content if isinstance(msg.content, str) else str(msg.content)
    hint = render_attachments_hint(meta)
    return HumanMessage(
        content=base + hint,
        additional_kwargs=msg.additional_kwargs,
        response_metadata=msg.response_metadata,
        id=msg.id,
        name=msg.name,
    )


class AttachmentHintMiddleware(AgentMiddleware[Any, Any, Any]):
    """Inject [Attachments] hint into HumanMessages before the LLM sees them."""

    tools: Sequence[BaseTool] = []

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any] | AIMessage:
        if not any(_attachments_meta(m) for m in request.messages):
            return await handler(request)

        new_messages: list[Any] = []
        for m in request.messages:
            meta = _attachments_meta(m)
            if meta:
                new_messages.append(_augment_with_hint(cast(HumanMessage, m), meta))
            else:
                new_messages.append(m)
        return await handler(request.override(messages=new_messages))
