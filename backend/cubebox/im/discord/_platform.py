"""DiscordPlatform — PlatformConnector implementation for Discord."""

from __future__ import annotations

from typing import Any


class DiscordPlatform:
    """PlatformConnector for Discord (Gateway only)."""

    def parse_inbound(self, raw: dict[str, Any]) -> Any:
        from cubebox.im.discord.connector import DiscordConnector

        connector = DiscordConnector()
        return connector.parse_inbound(raw)

    async def build_tailer(
        self, *, run_id: str, queue_item: Any, account: Any, **kwargs: Any
    ) -> Any:
        pass  # Wired in Task 15

    async def on_account_enabled(self, account: Any, **kwargs: Any) -> None:
        pass  # Wired in Task 15

    async def on_account_disabled(self, account: Any, **kwargs: Any) -> None:
        pass  # Wired in Task 15
