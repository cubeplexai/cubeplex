"""Unit tests for the pure output-sanitisation helpers in
``cubeplex.services.conversation_title``.

The orchestration around the LLM call is covered by existing conversation
E2E tests; here we lock down the regex-y string handling that is easy to
break and hard to spot in a manual smoke test.
"""

from cubeplex.services.conversation_title import (
    _clean_title,
    _extract_text,
    _looks_like_echo,
    _normalise_whitespace,
)


class TestExtractText:
    def test_string_passes_through(self) -> None:
        assert _extract_text("Plain answer") == "Plain answer"

    def test_list_of_blocks_keeps_text_only(self) -> None:
        # The reasoning-model failure mode the smoke test exposed.
        blocks = [
            "",
            {"type": "thinking", "thinking": "thinking out loud..."},
            {"type": "text", "text": "Postgres pool exhaustion"},
        ]
        assert _extract_text(blocks) == "Postgres pool exhaustion"

    def test_list_of_strings_concatenates(self) -> None:
        assert _extract_text(["foo", " ", "bar"]) == "foo bar"

    def test_unknown_block_types_skipped(self) -> None:
        blocks = [
            {"type": "tool_use", "name": "search"},
            {"type": "text", "text": "answer"},
            {"type": "image", "source": "data:..."},
        ]
        assert _extract_text(blocks) == "answer"

    def test_empty_list_yields_empty(self) -> None:
        assert _extract_text([]) == ""
        assert _extract_text([""]) == ""


class TestCleanTitle:
    def test_strips_whitespace_and_newlines(self) -> None:
        assert _clean_title("  Postgres pool exhaustion  \n") == "Postgres pool exhaustion"

    def test_collapses_internal_newlines(self) -> None:
        # The observed bug: model returned a value containing an internal newline.
        assert _clean_title("Use this skill\nwhen the user:") == "Use this skill when the user"

    def test_strips_english_wrapping_quotes(self) -> None:
        assert _clean_title('"React virtual list"') == "React virtual list"

    def test_strips_chinese_wrapping_quotes(self) -> None:
        assert _clean_title("「技能使用说明翻译」") == "技能使用说明翻译"
        assert _clean_title("“技能使用说明翻译”") == "技能使用说明翻译"

    def test_strips_leading_title_label(self) -> None:
        assert _clean_title("Title: React virtual list") == "React virtual list"
        assert _clean_title("标题：技能说明翻译") == "技能说明翻译"

    def test_strips_trailing_punctuation(self) -> None:
        assert _clean_title("React virtual list.") == "React virtual list"
        assert _clean_title("技能说明翻译。") == "技能说明翻译"

    def test_caps_length(self) -> None:
        long = "x" * 500
        cleaned = _clean_title(long)
        assert len(cleaned) <= 80

    def test_empty_input_returns_empty(self) -> None:
        assert _clean_title("") == ""
        assert _clean_title("   \n\n  ") == ""


class TestNormaliseWhitespace:
    def test_collapses_mixed_whitespace(self) -> None:
        assert _normalise_whitespace("a\n\n   b\t c") == "a b c"


class TestLooksLikeEcho:
    def test_prefix_echo_is_rejected(self) -> None:
        # The exact failure mode the user reported.
        title = "Use this skill when the user"
        snippet = (
            "Use this skill when the user: Asks how do I do X where X might be "
            "a common task with an existing skill"
        )
        assert _looks_like_echo(title, snippet) is True

    def test_short_title_is_not_echo(self) -> None:
        # A genuine 4-5 word title that happens to share leading words isn't
        # automatically an echo — but a 6+ char prefix match still is. The
        # guard is "≥ 6 chars title AND title is a prefix of the input".
        # A real model output is rarely this similar to the input.
        assert _looks_like_echo("Hi", "Hi there friend, can you help me") is False

    def test_genuine_summary_is_not_echo(self) -> None:
        assert (
            _looks_like_echo(
                "Postgres pool exhaustion debug",
                "I need to help me debug a really weird connection pool issue",
            )
            is False
        )

    def test_chinese_echo_is_rejected(self) -> None:
        title = "上面这段话翻译成"
        snippet = "上面这段话翻译成中文,我需要发给我的客户看"
        assert _looks_like_echo(title, snippet) is True

    def test_empty_inputs_are_safe(self) -> None:
        assert _looks_like_echo("", "anything") is False
        assert _looks_like_echo("anything", "") is False
