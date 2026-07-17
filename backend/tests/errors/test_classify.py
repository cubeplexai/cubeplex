"""Tests for the cubeplex error code mapper.

Detection (regex patterns, status-code routing, the Volcano-opaque
InvalidParameter heuristic) is tested upstream in cubepi. This file just
tests that cubepi typed exceptions map onto the correct ``ErrorCode``.
"""

from __future__ import annotations

from cubepi.errors import (
    ContextLengthExceeded,
    ProviderAuthFailed,
    ProviderBadRequest,
    ProviderError,
    ProviderUnavailable,
    RateLimited,
)

from cubeplex.errors import ErrorCode, classify_exception, english_fallback


def test_context_length_exceeded_maps_with_token_params() -> None:
    exc = ContextLengthExceeded(
        "ctx exceeded",
        provider="ark",
        model="kimi-k2.6",
        status_code=400,
        tokens_in=262014,
        context_window=256000,
    )
    code, params = classify_exception(exc)
    assert code is ErrorCode.context_length_exceeded
    assert params["model"] == "kimi-k2.6"
    assert params["provider"] == "ark"
    assert params["tokens_in"] == 262014
    assert params["context_window"] == 256000


def test_rate_limited_maps_with_retry_after() -> None:
    exc = RateLimited(
        "rate limited",
        provider="openai",
        model="gpt-4o",
        status_code=429,
        retry_after=12.0,
    )
    code, params = classify_exception(exc)
    assert code is ErrorCode.rate_limited
    assert params["retry_after"] == 12.0


def test_provider_auth_failed_maps() -> None:
    exc = ProviderAuthFailed("bad key", provider="openai", model="gpt-4o", status_code=401)
    code, _ = classify_exception(exc)
    assert code is ErrorCode.provider_auth_failed


def test_provider_unavailable_maps() -> None:
    exc = ProviderUnavailable(
        "service unavailable", provider="openai", model="gpt-4o", status_code=503
    )
    code, _ = classify_exception(exc)
    assert code is ErrorCode.provider_unavailable


def test_provider_bad_request_maps() -> None:
    exc = ProviderBadRequest("model_not_found", provider="openai", model="gpt-4o", status_code=400)
    code, _ = classify_exception(exc)
    assert code is ErrorCode.provider_bad_request


def test_unknown_provider_error_subclass_falls_back_to_bad_request() -> None:
    class _FutureKind(ProviderError):
        pass

    exc = _FutureKind("weird", provider="x", model="y", status_code=418)
    code, _ = classify_exception(exc)
    assert code is ErrorCode.provider_bad_request


def test_non_cubepi_exception_falls_back_to_internal_error() -> None:
    code, params = classify_exception(RuntimeError("boom"), model="gpt-4o", provider="openai")
    assert code is ErrorCode.internal_error
    assert params == {"model": "gpt-4o", "provider": "openai"}


def test_override_does_not_overwrite_exception_own_values() -> None:
    exc = ContextLengthExceeded(
        "ctx exceeded",
        provider="ark",
        model="kimi-k2.6",
        status_code=400,
        tokens_in=200_000,
        context_window=256_000,
    )
    _, params = classify_exception(exc, model="other-model", context_window=128_000)
    assert params["model"] == "kimi-k2.6"
    assert params["context_window"] == 256_000


def test_english_fallback_covers_all_codes() -> None:
    for code in ErrorCode:
        msg = english_fallback(code, {"model": "test-model"})
        assert isinstance(msg, str) and len(msg) > 0
