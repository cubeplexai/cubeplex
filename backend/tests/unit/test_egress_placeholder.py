import re

from cubeplex.sandbox_env.placeholder import (
    PLACEHOLDER_RE,
    hash_placeholder,
    mint_placeholder,
)


def test_mint_is_unique_and_well_formed():
    a, b = mint_placeholder(), mint_placeholder()
    assert a != b
    assert a.startswith("cbxref_")
    assert PLACEHOLDER_RE.fullmatch(a)


def test_scan_finds_placeholder_in_header_value():
    p = mint_placeholder()
    found = PLACEHOLDER_RE.findall(f"Bearer {p}")
    assert found == [p]


def test_hash_is_stable_and_hex():
    p = mint_placeholder()
    h1, h2 = hash_placeholder(p), hash_placeholder(p)
    assert h1 == h2
    assert re.fullmatch(r"[0-9a-f]{64}", h1)
