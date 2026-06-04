"""Error taxonomy and classifier.

The classifier turns provider/tool exceptions into a structured
``(ErrorCode, params)`` pair the SSE layer can carry to the frontend.
The frontend owns the localized strings keyed by ``ErrorCode``; the
backend only emits codes and dynamic params (model, provider, token
counts) plus an English fallback ``message`` for non-Web clients.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    """Coarse error categories surfaced to the user.

    Members are strings so they serialize cleanly into SSE JSON / Redis.
    Add new members at the end; never reuse or renumber.
    """

    context_length_exceeded = "context_length_exceeded"
    rate_limited = "rate_limited"
    provider_auth_failed = "provider_auth_failed"
    provider_unavailable = "provider_unavailable"
    provider_bad_request = "provider_bad_request"
    tool_failed = "tool_failed"
    internal_error = "internal_error"


_CONTEXT_LENGTH_PATTERNS = (
    re.compile(r"maximum context length", re.IGNORECASE),
    re.compile(r"context.{0,10}length.{0,20}exceed", re.IGNORECASE),
    re.compile(r"too many tokens", re.IGNORECASE),
    re.compile(r"prompt is too long", re.IGNORECASE),
    re.compile(r"reduce.{0,10}messages", re.IGNORECASE),
)

_RATE_LIMIT_PATTERNS = (
    re.compile(r"rate ?limit", re.IGNORECASE),
    re.compile(r"quota", re.IGNORECASE),
    re.compile(r"too many requests", re.IGNORECASE),
)


def _status_of(exc: BaseException) -> int | None:
    """Best-effort status code extraction. Handles openai-sdk-style attrs."""

    code = getattr(exc, "status_code", None)
    if isinstance(code, int):
        return code
    resp = getattr(exc, "response", None)
    if resp is not None:
        rc = getattr(resp, "status_code", None)
        if isinstance(rc, int):
            return rc
    return None


def classify_exception(
    exc: BaseException,
    *,
    model: str | None = None,
    provider: str | None = None,
    tokens_in: int | None = None,
    context_window: int | None = None,
) -> tuple[ErrorCode, dict[str, Any]]:
    """Map an exception to ``(ErrorCode, params)``.

    Heuristics (first match wins):
      1. Explicit context-length wording in the message.
      2. tokens_in within 5% of context_window when status is 4xx with no
         clear signal (covers Volcano ARK's opaque ``InvalidParameter``).
      3. 401 / 403 → provider_auth_failed.
      4. 429 / quota wording → rate_limited.
      5. 5xx / TimeoutError / ConnectionError → provider_unavailable.
      6. Other 4xx → provider_bad_request.
      7. Else → internal_error.

    ``params`` always carries the non-None contextual fields so the
    frontend can interpolate ``{model}`` / ``{provider}`` / ``{tokens_in}``
    / ``{context_window}`` keys in its translation strings.
    """

    msg = str(exc) or getattr(exc, "message", "") or ""
    status = _status_of(exc)

    params: dict[str, Any] = {}
    for key, value in (
        ("model", model),
        ("provider", provider),
        ("tokens_in", tokens_in),
        ("context_window", context_window),
    ):
        if value is not None:
            params[key] = value

    for pat in _CONTEXT_LENGTH_PATTERNS:
        if pat.search(msg):
            return ErrorCode.context_length_exceeded, params

    if (
        status == 400
        and tokens_in is not None
        and context_window is not None
        and tokens_in >= int(context_window * 0.95)
    ):
        return ErrorCode.context_length_exceeded, params

    if status in (401, 403):
        return ErrorCode.provider_auth_failed, params

    if status == 429 or any(pat.search(msg) for pat in _RATE_LIMIT_PATTERNS):
        return ErrorCode.rate_limited, params

    if isinstance(exc, (TimeoutError, ConnectionError)):
        return ErrorCode.provider_unavailable, params

    if status is not None and 500 <= status < 600:
        return ErrorCode.provider_unavailable, params

    if status is not None and 400 <= status < 500:
        return ErrorCode.provider_bad_request, params

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
