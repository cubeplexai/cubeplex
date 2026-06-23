"""Test Feishu /link command interception."""

from __future__ import annotations

from cubebox.im.feishu.link_command import parse_link_command


class TestParseLinkCommand:
    def test_link_with_email(self) -> None:
        result = parse_link_command("/link chris@example.com")
        assert result == "chris@example.com"

    def test_link_chinese(self) -> None:
        result = parse_link_command("绑定 test@corp.cn")
        assert result == "test@corp.cn"

    def test_link_extra_whitespace(self) -> None:
        result = parse_link_command("  /link   user@host.com  ")
        assert result == "user@host.com"

    def test_not_a_link_command(self) -> None:
        assert parse_link_command("hello world") is None
        assert parse_link_command("/new") is None
        assert parse_link_command("/link") is None  # no email
        assert parse_link_command("绑定") is None

    def test_invalid_email_rejected(self) -> None:
        assert parse_link_command("/link notanemail") is None

    def test_feishu_mailto_autolink_unwrapped(self) -> None:
        # Feishu client auto-renders bare emails as markdown autolinks.
        result = parse_link_command("/link [gxf.beta@gmail.com](mailto:gxf.beta@gmail.com)")
        assert result == "gxf.beta@gmail.com"

    def test_angle_bracketed_email_unwrapped(self) -> None:
        result = parse_link_command("/link <chris@example.com>")
        assert result == "chris@example.com"
