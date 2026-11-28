"""``chat`` — one-shot model call for artifact HTML.

Same shape as ``services/conversation_title.py``: a single provider request, no
agent loop, no persistence. Cache policy stays unset because a one-shot call has
no prior context to mark (see backend/docs/prompt-cache-discipline.md).

``max_prompt_chars`` / ``max_output_tokens`` are read once at import, so they
also appear in the discovery schema; changing them needs a restart. The other
limits (rate, timeout) are read per call in ``dispatch``.
"""

from __future__ import annotations

import asyncio
from typing import Any, cast

from loguru import logger
from pydantic import BaseModel, Field, field_validator

from cubeplex.config import config
from cubeplex.llm.builder import build_chain_model
from cubeplex.llm.resolver import resolve_model_preset
from cubeplex.llm.snapshot import load_llm_snapshot
from cubeplex.services.artifact_bridge.registry import register_function
from cubeplex.services.artifact_bridge.types import (
    ArtifactCallContext,
    ArtifactCallFailed,
    ArtifactFunction,
)

MAX_PROMPT_CHARS = int(config.get("artifact_bridge.max_prompt_chars", 8000))
MAX_OUTPUT_TOKENS = int(config.get("artifact_bridge.max_output_tokens", 1024))

ARTIFACT_CHAT_SYSTEM = (
    "You are answering a question asked inside an artifact the user is currently "
    "viewing. Artifact: {name} ({artifact_type}). {description}\n"
    "Answer directly and concisely, in the language of the question. Do not "
    "repeat the question and do not mention that you are a model."
)


class ChatInput(BaseModel):
    """One question from the artifact UI."""

    prompt: str = Field(min_length=1, max_length=MAX_PROMPT_CHARS)

    @field_validator("prompt")
    @classmethod
    def _reject_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("prompt must not be blank")
        return value


def _output_limit(bound: Any) -> int:
    """Lowest per-leg ceiling, so a shorter-output fallback leg is not exceeded."""
    members = getattr(bound, "chain", None) or (bound,)
    return int(min(MAX_OUTPUT_TOKENS, *(member.spec.max_tokens for member in members)))


def _system_prompt(ctx: ArtifactCallContext) -> str:
    return ARTIFACT_CHAT_SYSTEM.format(
        name=ctx.artifact.name,
        artifact_type=ctx.artifact.artifact_type,
        description=ctx.artifact.description or "",
    )


async def _chat(ctx: ArtifactCallContext, args: ChatInput) -> dict[str, Any]:
    from cubeloop.providers.base import StreamOptions, TextContent, UserMessage

    if ctx.encryption_backend is None:
        raise ArtifactCallFailed("credential vault unavailable")

    snap = await load_llm_snapshot(ctx.session, ctx.org_id, ctx.encryption_backend)
    preset = resolve_model_preset(snap, None)
    bound = build_chain_model(snap, preset)
    limit = _output_limit(bound)

    system = _system_prompt(ctx)
    messages = [UserMessage(content=[TextContent(text=args.prompt)])]

    if ctx.tracer is not None:
        async with ctx.tracer.oneshot(
            model=bound,
            operation="artifact_chat",
            metadata={
                "artifact_id": ctx.artifact.id,
                "conversation_id": ctx.conversation_id,
                "org_id": ctx.org_id,
            },
        ) as trace_session:
            raw_text = cast(
                str,
                await trace_session.generate(
                    system=system, messages=messages, max_output_tokens=limit
                ),
            )
        return {"text": raw_text}

    # ``bound.generate`` (not ``bound.provider.stream``) so a multi-model preset
    # keeps its failover legs. The abort signal tears the producer down when the
    # dispatch timeout cancels us — without it the upstream request keeps
    # streaming after the caller has given up.
    abort = asyncio.Event()
    try:
        response = await bound.generate(
            messages=messages,
            system_prompt=system,
            options=StreamOptions(signal=abort),
            max_output_tokens=limit,
        )
    except BaseException:
        abort.set()
        raise
    if response.error_message is not None:
        logger.warning("artifact chat provider error: {}", response.error_message)
        raise ArtifactCallFailed("model call failed")
    text = "".join(block.text for block in response.content if isinstance(block, TextContent))
    return {"text": text}


register_function(
    ArtifactFunction(
        name="chat",
        description=(
            "Ask the organization's configured chat model a single question and "
            "get a text answer. Input: {prompt: string}. Output: {text: string}."
        ),
        input_model=ChatInput,
        handler=_chat,
    )
)
