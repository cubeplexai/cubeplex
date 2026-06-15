"""FeishuPlatform — PlatformConnector implementation for Feishu."""

from __future__ import annotations

from typing import Any


class FeishuPlatform:
    """PlatformConnector for Feishu (long-connection + webhook)."""

    def parse_inbound(self, raw: dict[str, Any]) -> Any:
        from cubebox.im.feishu.connector import FeishuConnector

        connector = FeishuConnector()
        return connector.parse_inbound(raw)

    async def build_tailer(
        self, *, run_id: str, queue_item: Any, account: Any, **kwargs: Any
    ) -> Any:
        pass  # Wired in Task 15

    async def on_account_enabled(self, account: Any, **kwargs: Any) -> None:
        pass  # Wired in Task 15

    async def on_account_disabled(self, account: Any, **kwargs: Any) -> None:
        pass  # Wired in Task 15
