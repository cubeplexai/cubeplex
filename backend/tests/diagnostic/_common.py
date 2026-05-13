"""Shared helpers for diagnostic cache tests.

Provides:
- LONG_SYSTEM_PROMPT: deterministic ~2000-token cacheable prefix
- extract_anthropic_cache_tokens(): pull cache fields from Anthropic usage
- extract_openai_cache_tokens(): pull cached_tokens from OpenAI usage
- assert_cache_hit(): unified assertion with diagnostic output

These helpers avoid duplication across the provider-specific test files.
"""

from __future__ import annotations

from typing import Any

# Deterministic long system prompt — repeated until well above 1024 tokens.
# Most providers require ≥ 1024 tokens of stable prefix to enable caching.
# This sentence is ~12 tokens; 200 repetitions = ~2400 tokens.
_SENTENCE = "The quick brown fox jumps over the lazy dog."
LONG_SYSTEM_PROMPT: str = " ".join([_SENTENCE] * 200)

# User messages differ each turn so only the system prefix is cached.
USER_MSG_TURN_1 = "Reply with exactly: FIRST"
USER_MSG_TURN_2 = "Reply with exactly: SECOND"


def extract_anthropic_cache_tokens(usage: Any) -> dict[str, int]:
    """Extract cache-related fields from an Anthropic Usage object.

    Returns a dict with keys:
        cache_creation_input_tokens  — tokens written to cache on this call
        cache_read_input_tokens      — tokens served from cache on this call
        input_tokens                 — non-cached input tokens
    """
    return {
        "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", 0) or 0,
        "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
        "input_tokens": getattr(usage, "input_tokens", 0) or 0,
    }


def extract_openai_cache_tokens(usage: Any) -> dict[str, int]:
    """Extract cache-related fields from an OpenAI CompletionUsage object.

    OpenAI spec stores cached tokens under:
        usage.prompt_tokens_details.cached_tokens  (newer SDK)

    Some providers use non-standard fields; we try all known locations and
    return the first non-zero value. Full raw usage is also returned so the
    caller can print it for diagnosis.

    Returns a dict with keys:
        cached_tokens       — tokens served from cache
        prompt_tokens       — total prompt tokens (includes cached)
        completion_tokens   — output tokens
        _raw                — raw usage dict for printing
    """
    raw: dict[str, Any] = {}
    if hasattr(usage, "model_dump"):
        raw = usage.model_dump()
    elif hasattr(usage, "__dict__"):
        raw = dict(vars(usage))

    cached = 0

    # Standard OpenAI path: prompt_tokens_details.cached_tokens
    details = getattr(usage, "prompt_tokens_details", None)
    if details is not None:
        cached = getattr(details, "cached_tokens", 0) or 0

    # Some providers put it at top level
    if cached == 0:
        cached = raw.get("cached_tokens", 0) or 0

    # Some providers put it under prompt_cache_hit_tokens (ByteDance / Ark)
    if cached == 0:
        cached = raw.get("prompt_cache_hit_tokens", 0) or 0

    return {
        "cached_tokens": cached,
        "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
        "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
        "_raw": raw,
    }


def assert_cache_hit_anthropic(
    usage1_info: dict[str, int],
    usage2_info: dict[str, int],
    provider_label: str,
) -> None:
    """Assert that turn 2 has a cache hit; print diagnostics either way."""
    print(f"\n[{provider_label}] Turn 1 usage: {usage1_info}")
    print(f"[{provider_label}] Turn 2 usage: {usage2_info}")

    cache_creation = usage1_info.get("cache_creation_input_tokens", 0)
    cache_read = usage2_info.get("cache_read_input_tokens", 0)

    print(f"[{provider_label}] Turn 1 cache_creation_input_tokens: {cache_creation}")
    print(f"[{provider_label}] Turn 2 cache_read_input_tokens: {cache_read}")

    assert cache_read > 0, (
        f"[{provider_label}] Turn 2 did NOT report cache_read_input_tokens > 0.\n"
        f"  Turn 1 usage: {usage1_info}\n"
        f"  Turn 2 usage: {usage2_info}\n"
        "  Diagnosis: provider does not cache for this request shape at raw API level.\n"
        "  This is a provider limitation — unrelated to cubepi runtime."
    )
    print(f"[{provider_label}] CACHE HIT confirmed: cache_read_input_tokens={cache_read}")


def assert_cache_hit_openai(
    usage1_info: dict[str, Any],
    usage2_info: dict[str, Any],
    provider_label: str,
) -> None:
    """Assert that turn 2 has a cache hit (OpenAI spec); print diagnostics."""
    print(f"\n[{provider_label}] Turn 1 usage: {usage1_info}")
    print(f"[{provider_label}] Turn 2 usage: {usage2_info}")

    cache_read = usage2_info.get("cached_tokens", 0)
    print(f"[{provider_label}] Turn 2 cached_tokens: {cache_read}")

    assert cache_read > 0, (
        f"[{provider_label}] Turn 2 did NOT report cached_tokens > 0.\n"
        f"  Turn 1 usage: {usage1_info}\n"
        f"  Turn 2 usage: {usage2_info}\n"
        "  Diagnosis: provider does not cache for this request shape at raw API level.\n"
        "  This is a provider limitation — unrelated to cubepi runtime."
    )
    print(f"[{provider_label}] CACHE HIT confirmed: cached_tokens={cache_read}")
