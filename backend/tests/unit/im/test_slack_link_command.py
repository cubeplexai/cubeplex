"""Test Slack /link text-command parsing (incl. mailto autolink unwrap)."""

from __future__ import annotations

from cubeplex.im.slack.commands import parse_link_command


class TestParseLinkCommand:
    def test_link_with_email(self) -> None:
        assert parse_link_command("/link chris@example.com") == "chris@example.com"

    def test_link_without_slash(self) -> None:
        # Prefer this when the Slack slash command is not registered —
        # bare ``/link`` is intercepted by Slackbot as an invalid command.
        assert parse_link_command("link chris@example.com") == "chris@example.com"

    def test_link_extra_whitespace(self) -> None:
        assert parse_link_command("  /link   user@host.com  ") == "user@host.com"

    def test_not_a_link_command(self) -> None:
        assert parse_link_command("hello world") is None
        assert parse_link_command("/new") is None
        assert parse_link_command("/link") is None
        assert parse_link_command("link") is None

    def test_invalid_email_rejected(self) -> None:
        assert parse_link_command("/link notanemail") is None

    def test_slack_mailto_autolink_unwrapped(self) -> None:
        # Slack auto-renders bare emails as <mailto:user@host|user@host>.
        result = parse_link_command("/link <mailto:gxf.beta@gmail.com|gxf.beta@gmail.com>")
        assert result == "gxf.beta@gmail.com"

    def test_slack_mailto_without_label(self) -> None:
        result = parse_link_command("/link <mailto:chris@example.com>")
        assert result == "chris@example.com"

    def test_case_normalized(self) -> None:
        assert parse_link_command("/link Chris@Example.COM") == "chris@example.com"
