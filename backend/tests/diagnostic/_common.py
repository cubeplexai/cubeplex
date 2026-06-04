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


# Provider-specific field lookup order for cached tokens.
# Each entry is a list of (field_path, location) tuples tried in order.
# location "nested" = usage.prompt_tokens_details.cached_tokens
# location "top"    = raw dict key at top level
_PROVIDER_CACHE_FIELDS: dict[str, list[tuple[str, str]]] = {
    "openai": [
        ("prompt_tokens_details.cached_tokens", "nested"),
        ("cached_tokens", "top"),
    ],
    # ByteDance Volcengine Ark — uses standard nested path (confirmed empirically)
    "arkcode": [
        ("prompt_tokens_details.cached_tokens", "nested"),
        ("prompt_cache_hit_tokens", "top"),  # legacy Ark field, kept as fallback
        ("cached_tokens", "top"),
    ],
    # Alibaba DashScope — doesn't expose cache stats (xfail empirically)
    "alicode": [
        ("cached_tokens", "top"),
        ("prompt_cache_hit_tokens", "top"),
    ],
}


def extract_openai_cache_tokens(usage: Any, provider: str = "openai") -> dict[str, Any]:
    """Extract cache-related fields from an OpenAI CompletionUsage object.

    Args:
        usage:    CompletionUsage object from OpenAI-compatible API.
        provider: Provider key for field lookup order (default: "openai").
                  Known keys: "openai", "arkcode", "alicode".

    Returns a dict with keys:
        cached_tokens        — tokens served from cache
        prompt_tokens        — total prompt tokens (includes cached)
        completion_tokens    — output tokens
        _cache_field_used    — which raw field was non-zero (diagnostic)
        _raw                 — raw usage dict for printing
    """
    raw: dict[str, Any] = {}
    if hasattr(usage, "model_dump"):
        raw = usage.model_dump()
    elif hasattr(usage, "__dict__"):
        raw = dict(vars(usage))

    cached = 0
    field_used: str | None = None

    paths = _PROVIDER_CACHE_FIELDS.get(provider, _PROVIDER_CACHE_FIELDS["openai"])
    for field_path, location in paths:
        if location == "nested":
            details = getattr(usage, "prompt_tokens_details", None)
            if details is not None:
                cached = getattr(details, "cached_tokens", 0) or 0
        else:
            cached = raw.get(field_path, 0) or 0
        if cached > 0:
            field_used = field_path
            break

    return {
        "cached_tokens": cached,
        "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
        "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
        "_cache_field_used": field_used or "(none)",
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
    field_used = usage2_info.get("_cache_field_used", "(none)")
    cache_read = usage2_info.get("cached_tokens", 0)

    print(f"\n[{provider_label}] cache field: {field_used}")
    print(f"[{provider_label}] Turn 1 usage: {usage1_info}")
    print(f"[{provider_label}] Turn 2 usage: {usage2_info}")
    print(f"[{provider_label}] Turn 2 cached_tokens: {cache_read}")

    assert cache_read > 0, (
        f"[{provider_label}] Turn 2 did NOT report cached_tokens > 0 "
        f"(checked field: {field_used}).\n"
        f"  Turn 1 usage: {usage1_info}\n"
        f"  Turn 2 usage: {usage2_info}\n"
        "  Diagnosis: provider does not cache for this request shape at raw API level.\n"
        "  This is a provider limitation — unrelated to cubepi runtime."
    )
    print(f"[{provider_label}] CACHE HIT confirmed: cached_tokens={cache_read} via {field_used}")


def assert_cache_hit_openai_either(
    usage1_info: dict[str, Any],
    usage2_info: dict[str, Any],
    provider_label: str,
) -> None:
    """Assert that at least one of the two turns has cached_tokens > 0.

    Use when the cache may already be warm from a prior run, so either turn
    could be the one that hits it.
    """
    hit1 = usage1_info.get("cached_tokens", 0)
    hit2 = usage2_info.get("cached_tokens", 0)
    field1 = usage1_info.get("_cache_field_used", "(none)")
    field2 = usage2_info.get("_cache_field_used", "(none)")

    print(f"\n[{provider_label}] Turn 1 cached_tokens: {hit1} (field: {field1})")
    print(f"[{provider_label}] Turn 2 cached_tokens: {hit2} (field: {field2})")

    assert hit1 > 0 or hit2 > 0, (
        f"[{provider_label}] Neither turn reported cached_tokens > 0.\n"
        f"  Turn 1 usage: {usage1_info}\n"
        f"  Turn 2 usage: {usage2_info}\n"
        "  Diagnosis: provider does not cache for this request shape at raw API level.\n"
        "  This is a provider limitation — unrelated to cubepi runtime."
    )
    hit_turn = 1 if hit1 > 0 else 2
    hit_val = hit1 if hit1 > 0 else hit2
    hit_field = field1 if hit1 > 0 else field2
    print(
        f"[{provider_label}] CACHE HIT confirmed on turn {hit_turn}: cached_tokens={hit_val} via {hit_field}"
    )
