"""OpenAI-Compatible Chat Model with Reasoning Support

Extends ChatOpenAI to extract reasoning_content field from Chat Completions API.
This is useful for OpenAI-compatible endpoints that return reasoning in the response.
"""

import time
from collections.abc import AsyncIterator, Iterator
from typing import Any

from langchain_core.callbacks import (
    AsyncCallbackManagerForLLMRun,
    CallbackManagerForLLMRun,
)
from langchain_core.language_models import LanguageModelInput
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGenerationChunk, ChatResult
from langchain_openai import ChatOpenAI


class ChatOpenAICompatible(ChatOpenAI):
    """OpenAI-compatible chat model with reasoning_content extraction.

    This class extends ChatOpenAI to extract reasoning_content from the API
    response. Many OpenAI-compatible endpoints (like DeepSeek, DouBao, Qwen)
    return reasoning in the message.reasoning_content field.

    The reasoning_content will be available in:
    - response.additional_kwargs["reasoning_content"]

    Example:
        ```python
        from cubebox.llm import ChatOpenAICompatible

        llm = ChatOpenAICompatible(
            model="doubao-seed-1.6-lite-thinking",
            base_url="https://gateway.chat.sensedeal.vip/v1",
            api_key="your-key",
        )

        response = llm.invoke("What is 3^3?")

        # Access reasoning content
        reasoning = response.additional_kwargs.get("reasoning_content")
        if reasoning:
            print(f"Reasoning: {reasoning}")
        print(f"Answer: {response.content}")
        ```
    """

    # Per-stream state, reset in _stream / _astream
    _stream_metadata_emitted: bool = False
    _reasoning_start: float | None = None  # monotonic
    _reasoning_end: float | None = None  # monotonic

    def _reset_stream_state(self) -> None:
        self._stream_metadata_emitted = False
        self._reasoning_start = None
        self._reasoning_end = None

    def _create_chat_result(
        self,
        response: Any,
        generation_info: dict[str, Any] | None = None,
    ) -> ChatResult:
        """Create ChatResult from API response, extracting reasoning_content."""
        result = super()._create_chat_result(response, generation_info)

        # Extract reasoning_content if available (only for non-dict responses)
        if not isinstance(response, dict) and hasattr(response, "choices"):
            for i, res in enumerate(response.choices):
                message = result.generations[i].message if i < len(result.generations) else None
                if isinstance(message, AIMessage):
                    if hasattr(res.message, "reasoning_content") and res.message.reasoning_content:
                        message.additional_kwargs["reasoning_content"] = (
                            res.message.reasoning_content
                        )
                    # MiniMax: reasoning_details is [{text: "..."}]
                    elif (
                        hasattr(res.message, "reasoning_details") and res.message.reasoning_details
                    ):
                        texts = [
                            d["text"]
                            for d in res.message.reasoning_details
                            if isinstance(d, dict) and "text" in d
                        ]
                        if texts:
                            message.additional_kwargs["reasoning_content"] = "".join(texts)

        return result

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        self._reset_stream_state()
        yield from super()._stream(messages, stop=stop, run_manager=run_manager, **kwargs)

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        self._reset_stream_state()
        async for chunk in super()._astream(messages, stop=stop, run_manager=run_manager, **kwargs):
            yield chunk

    def _convert_chunk_to_generation_chunk(
        self,
        chunk: dict[str, Any],
        default_chunk_class: type,
        base_generation_info: dict[str, Any] | None,
    ) -> ChatGenerationChunk | None:
        """Convert chunk to generation chunk, extracting reasoning_content."""
        generation_chunk = super()._convert_chunk_to_generation_chunk(
            chunk, default_chunk_class, base_generation_info
        )

        if generation_chunk is None:
            return None

        choices = chunk.get("choices", []) or chunk.get("chunk", {}).get("choices", [])
        if not choices:
            return generation_chunk

        choice = choices[0]
        delta = choice.get("delta") or {}
        finish_reason = choice.get("finish_reason")

        # --- Extract reasoning_content (or reasoning_details for MiniMax) ---
        reasoning_delta: str | None = None
        if delta.get("reasoning_content"):
            reasoning_delta = delta["reasoning_content"]
        elif delta.get("reasoning_details"):
            # MiniMax: reasoning_details is [{text: "delta_text"}]
            # Each chunk contains only the new incremental text
            for detail in delta["reasoning_details"]:
                if isinstance(detail, dict) and "text" in detail:
                    text = detail["text"]
                    if text:
                        reasoning_delta = (reasoning_delta or "") + text

        # --- Track reasoning chunk timing (monotonic clock) ---
        has_reasoning = bool(reasoning_delta)
        if has_reasoning:
            now = time.monotonic()
            if self._reasoning_start is None:
                self._reasoning_start = now
            self._reasoning_end = now

        # --- Put reasoning into additional_kwargs ---
        if has_reasoning and isinstance(generation_chunk.message, AIMessageChunk):
            generation_chunk.message.additional_kwargs["reasoning_content"] = reasoning_delta

        # --- On finish: stamp reasoning_duration_ms ---
        # Only on the last chunk to avoid LangChain merge_dicts garbling.
        # created_at is handled by TimestampMiddleware at the agent level.
        if finish_reason is not None and not self._stream_metadata_emitted:
            if self._reasoning_start is not None:
                if isinstance(generation_chunk.message, AIMessageChunk):
                    end = self._reasoning_end or time.monotonic()
                    duration_ms = int((end - self._reasoning_start) * 1000)
                    generation_chunk.message.response_metadata["reasoning_duration_ms"] = (
                        duration_ms
                    )
            self._stream_metadata_emitted = True

        return generation_chunk

    def _get_request_payload(
        self,
        input_: LanguageModelInput,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> dict:  # type: ignore[type-arg]
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        if "max_completion_tokens" in payload:
            payload["max_tokens"] = payload.pop("max_completion_tokens")
        return payload

    @property
    def _llm_type(self) -> str:
        """Return identifier for this model type."""
        return "openai-compatible"
