"""CJK-aware token budget helper."""

from __future__ import annotations

from cubeplex.utils.tokens import (
    approx_tokens,
    approx_tokens_of,
    max_chars_for_token_budget,
)


def test_empty_is_zero() -> None:
    assert approx_tokens("") == 0
    assert approx_tokens_of(None) == 0


def test_english_near_quarter_chars() -> None:
    text = "The quick brown fox jumps over the lazy dog. " * 10
    # ~0.3 tok/char → roughly len/3.3; must stay well above len//8 and below len
    est = approx_tokens(text)
    assert len(text) // 8 < est < len(text)


def test_chinese_much_higher_than_ascii_heuristic() -> None:
    """Plain ``// 4`` undercounts Chinese by ~5× on cl100k; helper must not."""
    zh = "今天天气很好，我们一起去公园散步聊天讨论人工智能。" * 10
    legacy = max(1, len(zh) // 4)
    est = approx_tokens(zh)
    # CJK weight 1.3 → estimate ≈ 1.3 * chars, far above //4
    assert est > legacy * 3
    assert est >= len(zh)  # at least 1 tok/char order of magnitude


def test_mixed_between_pure_en_and_pure_zh_density() -> None:
    en = "hello world " * 50
    zh = "你好世界" * 50
    mixed = "hello 你好 world 世界 " * 25
    dens_en = approx_tokens(en) / len(en)
    dens_zh = approx_tokens(zh) / len(zh)
    dens_mixed = approx_tokens(mixed) / len(mixed)
    assert dens_en < dens_mixed < dens_zh


def test_approx_tokens_of_json_keeps_cjk() -> None:
    payload = {"q": "北京天气怎么样", "n": 3}
    # ensure_ascii=False path: Chinese should cost like CJK, not \\u escapes
    est = approx_tokens_of(payload)
    assert est >= approx_tokens("北京天气怎么样")


def test_max_chars_for_token_budget_is_cjk_safe() -> None:
    # Old helper used max_tokens * 4 which over-allowed Chinese past the budget.
    chars = max_chars_for_token_budget(100)
    assert chars <= 100  # densest script ≈ 1+ tok/char
    assert approx_tokens("中" * chars) <= 100 * 2  # generous upper bound on helper
