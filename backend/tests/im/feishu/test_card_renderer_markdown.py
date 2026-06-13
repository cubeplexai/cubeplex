"""Tests for optimize_markdown_style — Feishu CardKit markdown sanitization."""

from cubebox.im.feishu.card_renderer import optimize_markdown_style


def test_h1_demotes_to_h4() -> None:
    out = optimize_markdown_style("# Title\nbody")
    assert out.startswith("#### Title")


def test_h2_demotes_to_h5() -> None:
    assert optimize_markdown_style("## Sub").startswith("##### Sub")


def test_h3_h4_h5_h6_demote_to_h5() -> None:
    assert optimize_markdown_style("### a").startswith("##### a")
    assert optimize_markdown_style("###### h").startswith("##### h")


def test_table_gets_br_spacers() -> None:
    md = "before\n| a | b |\n| - | - |\n| 1 | 2 |\nafter"
    out = optimize_markdown_style(md)
    assert "<br>" in out
    assert "| a | b |" in out


def test_code_block_content_untouched() -> None:
    md = "```python\n# this is a comment\n```"
    out = optimize_markdown_style(md)
    assert "# this is a comment" in out


def test_invalid_image_key_stripped() -> None:
    md = "![alt](http://example.com/x.png)"
    out = optimize_markdown_style(md)
    assert "http://example.com/x.png" not in out


def test_valid_image_key_preserved() -> None:
    md = "![alt](img_v1_abc123)"
    out = optimize_markdown_style(md)
    assert "img_v1_abc123" in out


def test_citation_marker_replaced_with_link() -> None:
    citations = {
        "1": ("https://example.com/a", "Example"),
        "2": ("https://example.com/b", "B"),
    }
    out = optimize_markdown_style("see [1] and [2]", citation_index=citations)
    assert "[1](https://example.com/a)" in out
    assert "[2](https://example.com/b)" in out


def test_unknown_citation_marker_left_as_is() -> None:
    out = optimize_markdown_style("see [9]", citation_index={"1": ("u", "t")})
    assert "[9]" in out
    assert "(u)" not in out


def test_chinese_bracket_citation_replaced() -> None:
    out = optimize_markdown_style(
        "见【1-3】",
        citation_index={"1": ("https://a", "A"), "3": ("https://c", "C")},
    )
    # The full "【1-3】" span gets one link to the FIRST cited URL with the
    # full label preserved.
    assert "[1-3](https://a)" in out
