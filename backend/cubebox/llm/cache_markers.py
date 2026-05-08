"""Provider-specific prompt-cache marker insertion.

The middleware produces a provider-neutral logical request. This module
takes that request plus the active provider id and returns the same
request with cache_control markers inserted (Anthropic) or unchanged
(OpenAI / OpenAI-compatible).

This is the ONLY layer that should know about provider-specific cache
mechanics. Putting cache_control logic in middleware is a layering
violation.
"""

from typing import Any, Literal

from langchain_core.messages import AIMessage, BaseMessage, SystemMessage

ProviderKind = Literal["anthropic", "openai", "unknown"]


def detect_provider(model_id: str) -> ProviderKind:
    """Best-effort provider detection from a `provider/model-id` string."""
    if "/" in model_id:
        prefix = model_id.split("/", 1)[0].lower()
        if "anthropic" in prefix or "claude" in prefix:
            return "anthropic"
        if prefix in {"openai", "azure-openai", "deepseek", "qwen", "groq"}:
            return "openai"
    return "unknown"


def apply_cache_markers(
    *,
    system_message: SystemMessage | None,
    messages: list[BaseMessage],
    provider: ProviderKind,
) -> tuple[SystemMessage | None, list[BaseMessage]]:
    """Insert cache_control markers when needed.

    For Anthropic: mark the system message and the last completed assistant
    message with cache_control: ephemeral.

    For OpenAI / unknown: pass through. OpenAI auto-caches based on the byte
    stream, so structural stability (not markers) is what matters.
    """
    if provider != "anthropic":
        return system_message, messages

    new_system = _mark_anthropic(system_message) if system_message else None
    new_messages = _mark_last_assistant_anthropic(messages)
    return new_system, new_messages


def _mark_anthropic(msg: SystemMessage) -> SystemMessage:
    """Add cache_control: ephemeral to the system content. Idempotent."""
    new_content: list[str | dict[Any, Any]]
    if isinstance(msg.content, str):
        new_content = [
            {
                "type": "text",
                "text": msg.content,
                "cache_control": {"type": "ephemeral"},
            }
        ]
    elif isinstance(msg.content, list):
        new_content = list(msg.content)  # shallow copy
        if new_content and isinstance(new_content[-1], dict):
            new_content[-1] = {
                **new_content[-1],
                "cache_control": {"type": "ephemeral"},
            }
    else:
        return msg
    return SystemMessage(content=new_content, additional_kwargs=msg.additional_kwargs)


def _mark_last_assistant_anthropic(messages: list[BaseMessage]) -> list[BaseMessage]:
    """Place a cache_control marker on the last AIMessage of completed turns.

    "Completed" here means: not the message currently being generated. Since
    Cubebox builds the request before calling the model, every AIMessage in
    the messages list is by definition completed.
    """
    out = list(messages)
    for i in range(len(out) - 1, -1, -1):
        m = out[i]
        if isinstance(m, AIMessage):
            if isinstance(m.content, str):
                marked = AIMessage(
                    content=[
                        {
                            "type": "text",
                            "text": m.content,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                    additional_kwargs=m.additional_kwargs,
                    tool_calls=m.tool_calls,
                )
            elif isinstance(m.content, list) and m.content:
                new_blocks = list(m.content)
                if isinstance(new_blocks[-1], dict):
                    new_blocks[-1] = {
                        **new_blocks[-1],
                        "cache_control": {"type": "ephemeral"},
                    }
                marked = AIMessage(
                    content=new_blocks,
                    additional_kwargs=m.additional_kwargs,
                    tool_calls=m.tool_calls,
                )
            else:
                continue
            out[i] = marked
            break
    return out
