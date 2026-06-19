from cubebox.im.teams.format import normalize_for_teams, strip_mention_tags


def test_strikethrough_stripped() -> None:
    assert normalize_for_teams("~~removed~~") == "removed"


def test_bold_preserved() -> None:
    assert normalize_for_teams("**bold**") == "**bold**"


def test_italic_preserved() -> None:
    assert normalize_for_teams("*italic*") == "*italic*"


def test_link_preserved() -> None:
    assert normalize_for_teams("[click](https://x.com)") == "[click](https://x.com)"


def test_code_block_preserved() -> None:
    src = "```python\nprint('hi')\n```"
    assert normalize_for_teams(src) == src


def test_inline_code_preserved() -> None:
    assert normalize_for_teams("use `foo()`") == "use `foo()`"


def test_mention_tag_stripped() -> None:
    assert strip_mention_tags("<at>CubeBot</at> hello") == "hello"


def test_mention_tag_with_id_stripped() -> None:
    assert strip_mention_tags('<at id="abc">CubeBot</at> hi') == "hi"


def test_empty_after_strip() -> None:
    assert strip_mention_tags("<at>Bot</at>") == ""
