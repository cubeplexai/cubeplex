from cubeplex.services.conversation_search.snippet import extract_snippet


def test_keyword_hit_centers_window() -> None:
    text = "lorem " * 30 + "docling " + "ipsum " * 30
    out = extract_snippet(text, q="docling", window=80)
    assert "docling" in out.text
    assert out.match_offsets and out.match_offsets[0][1] - out.match_offsets[0][0] == len("docling")
    s, e = out.match_offsets[0]
    assert out.text[s:e].lower() == "docling"


def test_no_match_returns_head_with_empty_offsets() -> None:
    text = "alpha beta gamma delta"
    out = extract_snippet(text, q="nothing", window=80)
    assert out.text.startswith("alpha")
    assert out.match_offsets == []


def test_case_insensitive_match() -> None:
    out = extract_snippet("Hello WORLD foo", q="world", window=40)
    assert out.match_offsets != []


def test_empty_text_returns_empty() -> None:
    out = extract_snippet("", q="x", window=40)
    assert out.text == ""
    assert out.match_offsets == []


def test_german_eszett_offsets_align_with_original() -> None:
    """casefold('ß') -> 'ss'; the offset must still index the original 'ß'."""
    text = "Wir gehen die Straße entlang heute."
    out = extract_snippet(text, q="strasse", window=80)
    assert out.match_offsets, "expected a match for 'strasse' inside 'Straße'"
    s, e = out.match_offsets[0]
    # The snippet has no '…' prefix here (start == 0 with window=80, len(text)=35).
    assert out.text[s:e] == "Straße"


def test_turkish_dotted_capital_i_two_char_expansion() -> None:
    """'İ' casefolds to 'i\\u0307' (2 chars); the offset back to original 'İ'
    must still match correctly when the needle contains that two-char form.
    """
    text = "İstanbul is lovely"
    # Search with the casefolded form to exercise the 1->2 mapping branch.
    out = extract_snippet(text, q="i̇stanbul", window=80)
    assert out.match_offsets, "expected to find İstanbul"
    s, e = out.match_offsets[0]
    assert out.text[s:e] == "İstanbul"


def test_cjk_mixed_with_ascii() -> None:
    text = "前缀 docling 文档解析工具"
    out = extract_snippet(text, q="文档", window=80)
    assert out.match_offsets, "expected to find 文档 inside CJK haystack"
    s, e = out.match_offsets[0]
    assert out.text[s:e] == "文档"
