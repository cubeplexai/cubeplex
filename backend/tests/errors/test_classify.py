"""Tests for the error classifier — exception → (code, params)."""

from __future__ import annotations

from cubebox.errors import ErrorCode, classify_exception


class _FakeBadRequest(Exception):
    """Mimics openai.BadRequestError surface — has .status_code and .message."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


class _FakeRateLimit(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.status_code = 429
        self.message = message


def test_classify_explicit_context_length_message() -> None:
    exc = _FakeBadRequest(400, "This model's maximum context length is 128000 tokens")
    code, params = classify_exception(exc, model="kimi-k2.6", provider="ark")
    assert code is ErrorCode.context_length_exceeded
    assert params["model"] == "kimi-k2.6"
    assert params["provider"] == "ark"


def test_classify_volcano_invalid_parameter_with_oversize_tokens() -> None:
    exc = _FakeBadRequest(
        400, "InvalidParameter: A parameter specified in the request is not valid"
    )
    code, params = classify_exception(
        exc, model="kimi-k2.6", provider="ark", tokens_in=290_000, context_window=256_000
    )
    assert code is ErrorCode.context_length_exceeded
    assert params["tokens_in"] == 290_000
    assert params["context_window"] == 256_000


def test_classify_rate_limit() -> None:
    exc = _FakeRateLimit("Rate limit exceeded, retry in 60s")
    code, params = classify_exception(exc, model="gpt-4o", provider="openai")
    assert code is ErrorCode.rate_limited


def test_classify_auth_failed() -> None:
    exc = _FakeBadRequest(401, "Invalid API key")
    code, _ = classify_exception(exc, model="gpt-4o", provider="openai")
    assert code is ErrorCode.provider_auth_failed


def test_classify_forbidden_as_auth() -> None:
    exc = _FakeBadRequest(403, "Forbidden")
    code, _ = classify_exception(exc, model="gpt-4o", provider="openai")
    assert code is ErrorCode.provider_auth_failed


def test_classify_403_with_quota_wording_is_rate_limit() -> None:
    # Anthropic and a few others return 403 for quota exhaustion, not 429.
    exc = _FakeBadRequest(403, "quota exceeded for organization")
    code, _ = classify_exception(exc, model="claude-sonnet-4-6", provider="anthropic")
    assert code is ErrorCode.rate_limited


def test_classify_disk_quota_oserror_is_not_rate_limited() -> None:
    # The bare word "quota" used to false-positive into rate_limited;
    # the tightened pattern requires quota + (exceed|exhaust|limit|reach).
    exc = OSError("Errno 122: Disk quota for /tmp full")
    code, _ = classify_exception(exc, model="gpt-4o", provider="openai")
    assert code is ErrorCode.internal_error


def test_classify_provider_unavailable_5xx() -> None:
    exc = _FakeBadRequest(503, "service unavailable")
    code, _ = classify_exception(exc, model="gpt-4o", provider="openai")
    assert code is ErrorCode.provider_unavailable


def test_classify_timeout() -> None:
    exc = TimeoutError("read timed out")
    code, _ = classify_exception(exc, model="gpt-4o", provider="openai")
    assert code is ErrorCode.provider_unavailable


def test_classify_generic_bad_request_falls_back() -> None:
    exc = _FakeBadRequest(400, "model_not_found")
    code, _ = classify_exception(exc, model="gpt-4o", provider="openai")
    assert code is ErrorCode.provider_bad_request


def test_classify_unknown_exception_falls_back_to_internal_error() -> None:
    code, _ = classify_exception(RuntimeError("boom"), model="gpt-4o", provider="openai")
    assert code is ErrorCode.internal_error
