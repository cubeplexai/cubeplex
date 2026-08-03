"""Unit tests for binding_mode-driven scope key selection in connectors.

Each connector's parse_inbound accepts binding_mode ("isolated" | "shared").
The default is "isolated" and must produce the same scope_key as before.
"shared" collapses per-user isolation into channel/thread-level scoping.

Tested via the Slack connector which covers all three branches (DM, thread,
channel mention). Feishu and Discord follow the same pattern — their
connector-specific edge cases are covered by their own existing test files.
"""

from __future__ import annotations

from cubeplex.im.slack.connector import SlackConnector
from cubeplex.im.types import DM_SCOPE_KEY


def _make_event(
    *,
    text: str = "hello bot",
    user: str = "U111",
    channel: str = "C222",
    ts: str = "1234567890.123456",
    thread_ts: str = "",
    channel_type: str = "channel",
    event_type: str = "app_mention",
    client_msg_id: str = "msg-001",
) -> dict[str, str]:
    raw: dict[str, str] = {
        "type": event_type,
        "text": text,
        "user": user,
        "channel": channel,
        "ts": ts,
        "channel_type": channel_type,
    }
    if thread_ts:
        raw["thread_ts"] = thread_ts
    if client_msg_id:
        raw["client_msg_id"] = client_msg_id
    return raw


class TestScopeKeySelection:
    """Scope key varies by binding_mode for group contexts; DM is always isolated."""

    def setup_method(self) -> None:
        self.connector = SlackConnector(bot_user_id="UBOT")

    def test_dm_always_isolated(self) -> None:
        """DM scope_key is always 'dm' regardless of binding_mode."""
        raw = _make_event(channel_type="im", event_type="message")
        event = self.connector.parse_inbound(raw, binding_mode="shared")
        assert event is not None
        assert event.scope_key == DM_SCOPE_KEY

    def test_channel_mention_isolated_mode_is_thread_root(self) -> None:
        """Isolated mode: root channel @bot is keyed as a thread by its own ts.

        Bot replies open a Slack thread on that ts; follow-ups reuse the same
        scope instead of forking a second conversation/topic.
        """
        raw = _make_event(text="<@UBOT> hi", ts="1234567890.123456")
        event = self.connector.parse_inbound(raw, binding_mode="isolated")
        assert event is not None
        assert event.scope_key == "u:U111|t:1234567890.123456"
        assert event.scope_kind == "thread"
        assert event.reply_to_id == "1234567890.123456"

    def test_channel_mention_shared_mode_is_thread_root(self) -> None:
        """Shared mode: root channel @bot is thread-scoped by its own ts."""
        raw = _make_event(text="<@UBOT> hi", ts="1234567890.123456")
        event = self.connector.parse_inbound(raw, binding_mode="shared")
        assert event is not None
        assert event.scope_key == "t:1234567890.123456"
        assert event.scope_kind == "thread"
        assert event.reply_to_id == "1234567890.123456"

    def test_thread_mention_isolated_mode(self) -> None:
        """Isolated mode: thread mention scope_key includes both user and thread."""
        raw = _make_event(
            text="<@UBOT> explain",
            thread_ts="1234567890.000000",
            ts="1234567891.111111",
        )
        event = self.connector.parse_inbound(raw, binding_mode="isolated")
        assert event is not None
        assert event.scope_key == "u:U111|t:1234567890.000000"

    def test_thread_mention_shared_mode(self) -> None:
        """Shared mode: thread mention scope_key is thread-only (no user prefix)."""
        raw = _make_event(
            text="<@UBOT> explain",
            thread_ts="1234567890.000000",
            ts="1234567891.111111",
        )
        event = self.connector.parse_inbound(raw, binding_mode="shared")
        assert event is not None
        assert event.scope_key == "t:1234567890.000000"
        assert "u:" not in event.scope_key

    def test_root_and_thread_reply_share_scope(self) -> None:
        """Root @bot + first in-thread @bot must produce the same scope_key."""
        root_ts = "1234567890.000000"
        root = self.connector.parse_inbound(
            _make_event(text="<@UBOT> hi", ts=root_ts),
            binding_mode="isolated",
        )
        reply = self.connector.parse_inbound(
            _make_event(
                text="<@UBOT> follow up",
                thread_ts=root_ts,
                ts="1234567891.111111",
            ),
            binding_mode="isolated",
        )
        assert root is not None and reply is not None
        assert root.scope_key == reply.scope_key == f"u:U111|t:{root_ts}"

    def test_default_binding_mode_is_isolated(self) -> None:
        """Calling without binding_mode must match isolated behavior exactly."""
        raw = _make_event(text="<@UBOT> hi", ts="9999.0001")
        default_event = self.connector.parse_inbound(raw)
        explicit_event = self.connector.parse_inbound(raw, binding_mode="isolated")
        assert default_event is not None
        assert explicit_event is not None
        assert default_event.scope_key == explicit_event.scope_key
