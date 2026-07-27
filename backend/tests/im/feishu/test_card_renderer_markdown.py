"""Tests for optimize_markdown_style — Feishu CardKit markdown sanitization."""

from cubeplex.im.feishu.card_renderer import optimize_markdown_style


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


def test_list_nested_blockquote_hoisted_to_top_level() -> None:
    """Agent often nests suggested replies under a list item; Feishu drops those."""
    md = (
        "- **建议回复:**  \n"
        '  > "Full-stack" usually means moving across the system.\n'
        "  >   \n"
        "  > 仅供参考，不会自动发布。"
    )
    out = optimize_markdown_style(md)
    lines = out.splitlines()
    # Quote markers must be at column 0 (not indented under the list).
    quote_lines = [ln for ln in lines if ln.startswith(">")]
    assert quote_lines == [
        '> "Full-stack" usually means moving across the system.',
        ">",
        "> 仅供参考，不会自动发布。",
    ]
    # Blank line separates list item from quote so the list does not nest it.
    assert "- **建议回复:**" in out
    idx_label = next(i for i, ln in enumerate(lines) if "建议回复" in ln)
    idx_quote = next(i for i, ln in enumerate(lines) if ln.startswith(">"))
    assert idx_quote > idx_label
    assert any(lines[i].strip() == "" for i in range(idx_label + 1, idx_quote))


def test_top_level_blockquote_keeps_quote_markers() -> None:
    out = optimize_markdown_style("> already top-level\n> second line")
    assert out.splitlines()[:2] == ["> already top-level", "> second line"]


def test_blockquote_inside_code_fence_untouched() -> None:
    md = "```md\n> this is a literal quote marker in code\n```"
    out = optimize_markdown_style(md)
    assert "```md\n> this is a literal quote marker in code\n```" in out


def test_nested_blockquote_markers_preserved() -> None:
    out = optimize_markdown_style("  >> nested quote")
    assert out.strip() == ">> nested quote"


def test_four_space_indented_gt_not_hoisted_as_blockquote() -> None:
    """CommonMark: 4+ spaces before `>` is indented code, not a quote."""
    md = "para\n    > not a quote\nmore"
    out = optimize_markdown_style(md)
    assert "    > not a quote" in out
    assert not any(ln.startswith(">") and "not a quote" in ln for ln in out.splitlines())


def test_hoist_does_not_insert_blank_before_following_list_item() -> None:
    md = "- item one\n  > quote body\n- item two"
    out = optimize_markdown_style(md)
    lines = out.splitlines()
    idx_quote = next(i for i, ln in enumerate(lines) if ln.startswith(">"))
    assert lines[idx_quote] == "> quote body"
    # Next non-empty line should be the following list item without an extra blank.
    assert lines[idx_quote + 1] == "- item two"
