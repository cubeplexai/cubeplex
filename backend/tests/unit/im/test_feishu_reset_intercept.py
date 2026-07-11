"""Feishu still re-exports shared /new parse for stable call-site imports."""

from __future__ import annotations

from cubebox.im.feishu.reset_command import parse_reset_command


class TestFeishuParseResetCommand:
    def test_slash_new(self) -> None:
        assert parse_reset_command("/new") is True

    def test_not_a_reset_command(self) -> None:
        assert parse_reset_command("/link chris@example.com") is False
