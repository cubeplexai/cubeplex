"""Test Feishu /new and /reset command interception."""

from __future__ import annotations

from cubebox.im.feishu.reset_command import parse_reset_command


class TestParseResetCommand:
    def test_slash_new(self) -> None:
        assert parse_reset_command("/new") is True

    def test_slash_reset(self) -> None:
        assert parse_reset_command("/reset") is True

    def test_chinese_alias(self) -> None:
        assert parse_reset_command("新对话") is True

    def test_extra_whitespace(self) -> None:
        assert parse_reset_command("  /new  ") is True
        assert parse_reset_command("\n/reset\n") is True

    def test_case_insensitive(self) -> None:
        assert parse_reset_command("/NEW") is True
        assert parse_reset_command("/Reset") is True

    def test_not_a_reset_command(self) -> None:
        assert parse_reset_command("hello world") is False
        assert parse_reset_command("/link chris@example.com") is False
        assert parse_reset_command("/new please") is False  # trailing text
        assert parse_reset_command("再来一个新对话") is False  # not exact
        assert parse_reset_command("") is False
