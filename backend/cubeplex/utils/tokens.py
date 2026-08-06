"""Cheap token estimates for budgeting — CJK-aware, not for billing.

Real tokenizers (tiktoken, provider-native) differ a lot by language:

| Script / tokenizer      | Typical chars per token |
|-------------------------|-------------------------|
| English, cl100k/o200k   | ~4–4.5                  |
| Chinese, cl100k_base    | ~0.8–1.0                |
| Chinese, o200k_base     | ~1.3–1.5                |
| Chinese-native (Qwen…)  | often better than cl100k|

Rules of thumb used elsewhere in the industry:

* OpenAI docs: English ≈ 4 chars / token
* DeepSeek docs: EN char ≈ 0.3 tok, ZH char ≈ 0.6 tok
* CJK-heavy product traffic: plain ``len // 4`` **severely undercounts** Chinese
  (measured ~5× low on cl100k pure-ZH prose)

This module is the **only** place cubeplex should hard-code char→token ratios
for budget heuristics (memory injection caps, history tool output bounds, MCP
tool-schema size gates). It deliberately **over-estimates slightly** so a
budget that says "≤ N tokens" rarely overflows the real model.

It is **not** a substitute for:

* provider ``usage`` fields (billing / compaction trigger)
* tiktoken encode for embedding chunk windows that must match model BPE

cubepi still owns ``approx_tokens(messages)`` for compaction relative sizing;
call sites that only have a ``str`` / JSON blob should import from here.
"""

from __future__ import annotations

import json
import math
from typing import Any

# Tokens per character. Tuned against local tiktoken cl100k_base / o200k_base
# samples (mixed CN/EN product text) so pure Chinese is near-match on cl100k
# and over-estimate on o200k; English over-estimates modestly (safe for budget).
_TOKENS_PER_CJK_CHAR = 1.3
_TOKENS_PER_OTHER_CHAR = 0.3

# Tag / envelope overhead when budgeting rendered blocks (was "80 chars").
DEFAULT_STRUCTURE_OVERHEAD_TOKENS = 20


def _is_cjk_char(ch: str) -> bool:
    """True for CJK Unified Ideographs, kana, hangul (common product scripts)."""
    o = ord(ch)
    return (
        0x3400 <= o <= 0x4DBF  # CJK Extension A
        or 0x4E00 <= o <= 0x9FFF  # CJK Unified
        or 0xF900 <= o <= 0xFAFF  # CJK Compatibility
        or 0x3040 <= o <= 0x30FF  # Hiragana + Katakana
        or 0xAC00 <= o <= 0xD7AF  # Hangul Syllables
        or 0x20000 <= o <= 0x2CEAF  # CJK Ext B–F (rare but cheap to include)
    )


def approx_tokens(text: str) -> int:
    """Estimate tokens for a plain string (budgeting only).

    Empty → 0. Non-empty → at least 1.
    """
    if not text:
        return 0
    cjk = 0
    other = 0
    for ch in text:
        if _is_cjk_char(ch):
            cjk += 1
        else:
            other += 1
    return max(1, math.ceil(cjk * _TOKENS_PER_CJK_CHAR + other * _TOKENS_PER_OTHER_CHAR))


def approx_tokens_of(value: Any) -> int:
    """Estimate tokens for an arbitrary JSON-serialisable value.

    Serialises with ``ensure_ascii=False`` so Chinese stays as codepoints (same
    as what we send to models) rather than ``\\uXXXX`` escapes that inflate
    length without matching real prompt encoding.
    """
    if value is None:
        return 0
    if isinstance(value, str):
        return approx_tokens(value)
    try:
        dumped = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        dumped = str(value)
    return approx_tokens(dumped)


def max_chars_for_token_budget(max_tokens: int) -> int:
    """Conservative char cap so pure-CJK text still fits ``max_tokens``.

    Uses the densest script we model (CJK at ``_TOKENS_PER_CJK_CHAR``). Prefer
    binary-search + :func:`approx_tokens` when precision matters; this is for
    simple hard caps / ellipsis helpers.
    """
    if max_tokens <= 0:
        return 0
    return max(1, int(max_tokens / _TOKENS_PER_CJK_CHAR))
