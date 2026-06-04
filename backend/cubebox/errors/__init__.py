"""Error taxonomy + cubepi-typed-error mapper for the cubebox SSE/UI layer.

Classification (regex patterns, status-code routing, the Volcano-opaque
InvalidParameter heuristic) lives upstream in ``cubepi.errors``. This
module just maps cubepi's typed exceptions onto the user-facing
``ErrorCode`` taxonomy and supplies an English fallback string for
non-Web clients.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from cubepi.errors import (
    ContextLengthExceeded,
    ProviderAuthFailed,
    ProviderBadRequest,
    ProviderError,
    ProviderUnavailable,
    RateLimited,
)


class ErrorCode(StrEnum):
    """Coarse error categories surfaced to the user."""

    context_length_exceeded = "context_length_exceeded"
    rate_limited = "rate_limited"
    provider_auth_failed = "provider_auth_failed"
    provider_unavailable = "provider_unavailable"
    provider_bad_request = "provider_bad_request"
    tool_failed = "tool_failed"
    internal_error = "internal_error"


def _params_from(exc: ProviderError, override: dict[str, Any] | None = None) -> dict[str, Any]:
    params: dict[str, Any] = {}
    if exc.model is not None:
        params["model"] = exc.model
    if exc.provider is not None:
        params["provider"] = exc.provider
    tokens_in = getattr(exc, "tokens_in", None)
    if tokens_in is not None:
        params["tokens_in"] = tokens_in
    context_window = getattr(exc, "context_window", None)
    if context_window is not None:
        params["context_window"] = context_window
    retry_after = getattr(exc, "retry_after", None)
    if retry_after is not None:
        params["retry_after"] = retry_after
    if override:
        for k, v in override.items():
            if v is not None and k not in params:
                params[k] = v
    return params


def classify_exception(
    exc: BaseException,
    *,
    model: str | None = None,
    provider: str | None = None,
    tokens_in: int | None = None,
    context_window: int | None = None,
) -> tuple[ErrorCode, dict[str, Any]]:
    """Map an exception to ``(ErrorCode, params)``.

    Detection lives upstream in ``cubepi.errors``; this is a flat isinstance
    dispatch over the typed subclasses. Non-cubepi exceptions classify as
    ``internal_error``. keyword args are used only as fallback when the
    exception doesn't carry them.
    """

    override = {
        "model": model,
        "provider": provider,
        "tokens_in": tokens_in,
        "context_window": context_window,
    }

    if isinstance(exc, ContextLengthExceeded):
        return ErrorCode.context_length_exceeded, _params_from(exc, override)
    if isinstance(exc, RateLimited):
        return ErrorCode.rate_limited, _params_from(exc, override)
    if isinstance(exc, ProviderAuthFailed):
        return ErrorCode.provider_auth_failed, _params_from(exc, override)
    if isinstance(exc, ProviderUnavailable):
        return ErrorCode.provider_unavailable, _params_from(exc, override)
    if isinstance(exc, ProviderBadRequest):
        return ErrorCode.provider_bad_request, _params_from(exc, override)
    if isinstance(exc, ProviderError):
        return ErrorCode.provider_bad_request, _params_from(exc, override)

    params: dict[str, Any] = {k: v for k, v in override.items() if v is not None}
    return ErrorCode.internal_error, params


def english_fallback(code: ErrorCode, params: dict[str, Any]) -> str:
    """English copy for non-Web clients. Frontend has its own i18n."""

    model = params.get("model") or "the model"
    if code is ErrorCode.context_length_exceeded:
        return f"Conversation exceeds {model}'s context window. Start a new chat or switch models."
    if code is ErrorCode.rate_limited:
        return f"Rate limit reached for {model}. Try again shortly."
    if code is ErrorCode.provider_auth_failed:
        return f"Authentication with the {model} provider failed. Check your API key."
    if code is ErrorCode.provider_unavailable:
        return f"The {model} provider is unavailable. Try again shortly."
    if code is ErrorCode.provider_bad_request:
        return f"The request to {model} was rejected. See details for the raw error."
    if code is ErrorCode.tool_failed:
        return "A tool call failed during this turn. See details for the raw error."
    return "An unexpected error occurred. See details for the raw error."


__all__ = ["ErrorCode", "classify_exception", "english_fallback"]
