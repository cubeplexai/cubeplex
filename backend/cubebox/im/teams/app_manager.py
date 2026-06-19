"""Teams App instance lifecycle manager.

Manages a cache of ``microsoft_teams.apps.App`` instances, one per
enabled Teams account. The ingress webhook route looks up the App
by bot ID to validate JWT and dispatch activities.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

_app_cache: dict[str, TeamsAppEntry] = {}


class TeamsAppEntry:
    """One cached App instance + metadata for a Teams account."""

    def __init__(
        self,
        *,
        app: Any,
        account_id: str,
        bot_id: str,
        secrets: dict[str, Any],
        graph_client: Any = None,
    ) -> None:
        self.app = app
        self.account_id = account_id
        self.bot_id = bot_id
        self.secrets = secrets
        self.graph_client = graph_client


async def init_app(
    *,
    account_id: str,
    bot_id: str,
    secrets: dict[str, Any],
) -> TeamsAppEntry:
    """Create and cache an App instance for one Teams account.

    ``secrets`` must contain ``app_id``, ``app_secret``, ``tenant_id``.
    """
    from microsoft_teams.apps import App

    from cubebox.im.teams.graph import TeamsGraphClient

    app_id = str(secrets["app_id"])
    app_secret = str(secrets["app_secret"])
    tenant_id = str(secrets["tenant_id"])

    app = App(
        client_id=app_id,
        client_secret=app_secret,
        tenant_id=tenant_id,
    )
    await app.initialize()

    graph_client = TeamsGraphClient(
        app_id=app_id,
        app_secret=app_secret,
        tenant_id=tenant_id,
    )

    entry = TeamsAppEntry(
        app=app,
        account_id=account_id,
        bot_id=bot_id,
        secrets=secrets,
        graph_client=graph_client,
    )
    _app_cache[bot_id] = entry
    logger.info("[Teams] app initialized for account={} bot_id={}", account_id, bot_id)
    return entry


def get_entry_by_bot_id(bot_id: str) -> TeamsAppEntry | None:
    """Look up a cached App entry by the bot's App ID."""
    return _app_cache.get(bot_id)


def remove_app(bot_id: str) -> None:
    """Remove a cached App entry."""
    entry = _app_cache.pop(bot_id, None)
    if entry:
        logger.info(
            "[Teams] app removed for account={} bot_id={}",
            entry.account_id,
            entry.bot_id,
        )


def all_entries() -> list[TeamsAppEntry]:
    """Return all cached App entries."""
    return list(_app_cache.values())
