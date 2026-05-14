"""One-shot text generation adapter over cubepi.Provider.

cubepi providers are stream-first; the compaction summarizer wants
a single prompt → single text reply with a configurable output cap.
This module accumulates ``text_delta`` events into a string and returns
it. Tool calls and structured outputs are ignored — the summarizer
prompt never invites tool use.
"""

from __future__ import annotations

from cubepi import Model
from cubepi.providers.base import Message, Provider


class OneShotLLM:
    """Adapter from ``cubepi.Provider.stream()`` to the ``_OneShotProvider`` Protocol.

    Satisfies the duck-typed ``generate_once`` contract consumed by
    ``cubebox.middleware.compaction.summarizer.summarize``.
    """

    def __init__(self, provider: Provider, model: Model) -> None:
        self._provider = provider
        self._model = model

    async def generate_once(
        self,
        *,
        system: str,
        messages: list[Message],
        max_output_tokens: int,
    ) -> str:
        """Run one non-tool-using completion, return the full text."""
        model = self._model.model_copy(update={"max_tokens": max_output_tokens})
        stream = await self._provider.stream(
            model=model,
            messages=messages,
            system_prompt=system,
        )
        parts: list[str] = []
        async for evt in stream:
            if evt.type == "text_delta":
                if evt.delta:
                    parts.append(evt.delta)
            elif evt.type == "error":
                raise RuntimeError(evt.error_message or "one-shot generation failed")
            elif evt.type == "done":
                break
        return "".join(parts)
