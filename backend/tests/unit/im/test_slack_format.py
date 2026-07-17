from __future__ import annotations

from cubeplex.im.slack.format import markdown_to_slack_mrkdwn


class TestMarkdownToSlackMrkdwn:
    def test_bold(self) -> None:
        assert markdown_to_slack_mrkdwn("**hello**") == "*hello*"

    def test_italic(self) -> None:
        assert markdown_to_slack_mrkdwn("*hello*") == "_hello_"

    def test_link(self) -> None:
        assert (
            markdown_to_slack_mrkdwn("[click here](https://example.com)")
            == "<https://example.com|click here>"
        )

    def test_strikethrough(self) -> None:
        assert markdown_to_slack_mrkdwn("~~removed~~") == "~removed~"

    def test_bold_and_italic_together(self) -> None:
        result = markdown_to_slack_mrkdwn("**bold** and *italic*")
        assert result == "*bold* and _italic_"

    def test_fenced_code_block_preserved(self) -> None:
        text = "before ```**not bold** *not italic*``` after"
        result = markdown_to_slack_mrkdwn(text)
        assert "```**not bold** *not italic*```" in result
        assert result.startswith("before ")
        assert result.endswith(" after")

    def test_inline_code_preserved(self) -> None:
        text = "use `**bold**` for emphasis"
        result = markdown_to_slack_mrkdwn(text)
        assert "`**bold**`" in result

    def test_slack_user_mention_preserved(self) -> None:
        text = "Hello <@U12345> how are you?"
        result = markdown_to_slack_mrkdwn(text)
        assert "<@U12345>" in result

    def test_slack_channel_ref_preserved(self) -> None:
        text = "See <#C12345>"
        result = markdown_to_slack_mrkdwn(text)
        assert "<#C12345>" in result

    def test_slack_here_preserved(self) -> None:
        text = "<!here> **important**"
        result = markdown_to_slack_mrkdwn(text)
        assert "<!here>" in result
        assert "*important*" in result

    def test_mixed_content(self) -> None:
        text = (
            "**Important**: check [docs](https://docs.example.com) "
            "and ping <@U999>. ~~old info~~ replaced with *new info*."
        )
        result = markdown_to_slack_mrkdwn(text)
        assert "*Important*" in result
        assert "<https://docs.example.com|docs>" in result
        assert "<@U999>" in result
        assert "~old info~" in result
        assert "_new info_" in result

    def test_empty_string(self) -> None:
        assert markdown_to_slack_mrkdwn("") == ""

    def test_plain_text_unchanged(self) -> None:
        assert markdown_to_slack_mrkdwn("just plain text") == "just plain text"

    def test_multiline_code_block(self) -> None:
        text = "start\n```\n**bold** inside\n*italic* inside\n```\nend"
        result = markdown_to_slack_mrkdwn(text)
        assert "```\n**bold** inside\n*italic* inside\n```" in result
