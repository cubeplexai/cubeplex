"""Teams App instance lifecycle manager.

Manages a cache of ``microsoft_teams.apps.App`` instances, one per
enabled Teams account. The ingress webhook route looks up the App
by bot ID to validate JWT and dispatch activities.
"""

from __future__ import annotations

import base64
import json
from typing import Any

from loguru import logger

_app_cache: dict[str, TeamsAppEntry] = {}
# conversation_id → (service_url, channel_id, bot_account_id) from inbound.
# Replies must use the activity's serviceUrl AND the channel bot account id
# (activity.recipient.id) — Web Chat uses ``botname@…``, not the App ID GUID.
# Using the raw App ID as from.id yields connector 403.
_conversation_routes: dict[str, tuple[str, str, str]] = {}


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

    from cubeplex.im.teams.graph import TeamsGraphClient

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


def remember_conversation_route(
    conversation_id: str,
    *,
    service_url: str,
    channel_id: str,
    bot_account_id: str = "",
) -> None:
    """Cache outbound routing for a conversation from an inbound activity."""
    conv = (conversation_id or "").strip()
    url = (service_url or "").rstrip("/")
    ch = (channel_id or "").strip() or "msteams"
    bot_acc = (bot_account_id or "").strip()
    if not conv or not url:
        return
    _conversation_routes[conv] = (url, ch, bot_acc)


def get_conversation_route(conversation_id: str) -> tuple[str, str, str] | None:
    """Return ``(service_url, channel_id, bot_account_id)`` if known."""
    return _conversation_routes.get(conversation_id)


def _app_ids_from_bearer(auth_header: str) -> list[str]:
    """Decode (unverified) Bot Framework JWT claims that identify the bot App ID.

    Web Chat / Direct Line put a channel account id in ``activity.recipient.id``
    (e.g. ``botname@…``), not the Microsoft App ID GUID we cache on. The
    service token's ``aud`` / ``appid`` is the App ID and is the reliable key.
    Signature is still verified later by the App's token validator.
    """
    if not auth_header.startswith("Bearer "):
        return []
    token = auth_header.removeprefix("Bearer ").strip()
    parts = token.split(".")
    if len(parts) < 2:
        return []
    payload_b64 = parts[1]
    # urlsafe_b64decode needs padding
    payload_b64 += "=" * (-len(payload_b64) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(payload_b64.encode("ascii")))
    except Exception:
        return []
    if not isinstance(payload, dict):
        return []
    out: list[str] = []
    aud = payload.get("aud")
    if isinstance(aud, str) and aud:
        out.append(aud)
    elif isinstance(aud, list):
        out.extend(str(a) for a in aud if a)
    for key in ("appid", "azp"):
        val = payload.get(key)
        if isinstance(val, str) and val:
            out.append(val)
    return out


def bot_id_candidates(activity: dict[str, Any], auth_header: str) -> list[str]:
    """Ordered lookup keys for the App cache for one inbound activity."""
    candidates: list[str] = []
    recipient = activity.get("recipient") or {}
    rid = str(recipient.get("id") or "")
    if rid:
        candidates.append(rid)
        # Teams sometimes prefixes the App ID as ``28:<appId>``.
        if rid.startswith("28:"):
            candidates.append(rid.removeprefix("28:"))
    candidates.extend(_app_ids_from_bearer(auth_header))

    seen: set[str] = set()
    ordered: list[str] = []
    for c in candidates:
        if c and c not in seen:
            seen.add(c)
            ordered.append(c)
    return ordered


def resolve_entry_for_activity(
    activity: dict[str, Any],
    auth_header: str,
) -> TeamsAppEntry | None:
    """Find the cached App for an activity.

    Prefer ``recipient.id`` (Teams channel), fall back to JWT App ID claims
    (Web Chat / Direct Line, where recipient.id is not the App ID).
    """
    for candidate in bot_id_candidates(activity, auth_header):
        entry = get_entry_by_bot_id(candidate)
        if entry is not None:
            return entry
    return None
