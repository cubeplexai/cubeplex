"""SlackPlatform — PlatformConnector implementation for Slack."""

from __future__ import annotations

from typing import Any


class SlackPlatform:
    """PlatformConnector for Slack (stub — gateway/tailer not yet wired)."""

    def parse_inbound(self, raw: dict[str, Any]) -> Any:
        from cubebox.im.slack.connector import SlackConnector

        connector = SlackConnector()
        return connector.parse_inbound(raw)

    async def build_tailer(
        self, *, run_id: str, queue_item: Any, account: Any, **kwargs: Any
    ) -> Any:
        raise NotImplementedError("Slack tailer not yet implemented")

    async def on_account_enabled(self, account: Any, **kwargs: Any) -> None:
        raise NotImplementedError("Slack account enable not yet implemented")

    async def on_account_disabled(self, account: Any, **kwargs: Any) -> None:
        raise NotImplementedError("Slack account disable not yet implemented")
