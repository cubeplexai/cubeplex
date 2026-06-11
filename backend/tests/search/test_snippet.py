from cubebox.search.snippet import extract_snippet


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
