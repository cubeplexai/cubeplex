"""Native Anthropic adapter with the saved total output limit."""

from typing import Any

from cubeloop.providers.anthropic import AnthropicProvider as SDKAnthropicProvider
from cubeloop.providers.base import Model


class AnthropicProvider(SDKAnthropicProvider):
    @staticmethod
    def _apply_anthropic_budget_guard(kwargs: dict[str, Any], model: Model) -> None:
        # The saved limit includes thinking; the SDK default adds thinking to it.
        limit = min(model.max_tokens, model.context_window)
        thinking = kwargs.get("thinking", {})
        budget = thinking.get("budget_tokens", 0) if isinstance(thinking, dict) else 0
        if isinstance(budget, int) and budget >= limit:
            raise ValueError("The reasoning budget must fit within the configured output limit")
        kwargs["max_tokens"] = limit
