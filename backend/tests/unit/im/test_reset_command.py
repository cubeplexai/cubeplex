"""Shared IM /new and /reset command parsing + reply formatting."""

from __future__ import annotations

from cubeplex.im.reset_command import format_reset_reply, parse_reset_command


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


class TestFormatResetReply:
    def test_none(self) -> None:
        assert "没有进行中的会话" in format_reset_reply("none")

    def test_flat_or_rotated(self) -> None:
        assert "新对话已开始" in format_reset_reply("flat")
        assert "新对话已开始" in format_reset_reply("rotated")


class TestFeishuReexport:
    """Feishu path keeps a stable import for parse_reset_command."""

    def test_feishu_reexports_shared_parse(self) -> None:
        from cubeplex.im.feishu.reset_command import parse_reset_command as feishu_parse
        from cubeplex.im.reset_command import parse_reset_command as shared_parse

        assert feishu_parse is shared_parse
