"""Account-level IM bot behavior settings.

A bot is bound to a workspace and behaves uniformly across every channel it
is in. Per-channel differentiation is handled by creating a second bot, not
per-channel config (see
docs/dev/specs/2026-06-23-im-bot-settings-design.md). These settings live on
``IMConnectorAccount.config["bot_settings"]`` and replace the now-removed
``IMChannelBinding`` routing/sandbox columns.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

RoutingMode = Literal["isolated", "shared"]
TopicMode = Literal["topic", "flat"]

_BOT_SETTINGS_KEY = "bot_settings"


class IMBotSettings(BaseModel):
    """How a bot turns inbound messages into cubeplex conversations.

    - ``routing_mode``: ``isolated`` (one conversation per sender) vs
      ``shared`` (one conversation for the whole channel).
    - ``topic_mode``: ``topic`` (roll each conversation up under a cubeplex
      Topic) vs ``flat`` (no Topic — conversations are listed flat in the
      sidebar). Orthogonal to ``routing_mode``: ``shared`` + ``flat`` is a
      valid combo (one group conversation, ungrouped in the sidebar).
    - ``sandbox_mode``: sandbox the bot's runs use (``dedicated`` | ``creator``,
      matching the native Topic schema); defaulted to ``dedicated`` for
      shared/topic runs at Topic-creation time. An invalid stored value makes
      ``load_bot_settings`` fall back to defaults rather than raising.
    """

    routing_mode: RoutingMode = "isolated"
    topic_mode: TopicMode = "topic"
    sandbox_mode: str | None = Field(default=None, pattern=r"^(dedicated|creator)$")


def load_bot_settings(config: dict[str, Any] | None) -> IMBotSettings:
    """Parse settings out of ``account.config``; defaults on missing/invalid."""
    raw = (config or {}).get(_BOT_SETTINGS_KEY)
    if not isinstance(raw, dict):
        return IMBotSettings()
    try:
        return IMBotSettings.model_validate(raw)
    except Exception:
        return IMBotSettings()


def store_bot_settings(config: dict[str, Any] | None, settings: IMBotSettings) -> dict[str, Any]:
    """Return a new config dict with ``bot_settings`` merged in."""
    merged = dict(config or {})
    merged[_BOT_SETTINGS_KEY] = settings.model_dump()
    return merged


def wants_topic(settings: IMBotSettings) -> bool:
    """True if conversations should roll up under a Topic.

    Independent of ``routing_mode``: a shared (group) conversation can still
    be flat (no Topic), and an isolated (per-person) conversation can still
    be grouped under a Topic.
    """
    return settings.topic_mode == "topic"


def bot_display_name(config: dict[str, Any] | None) -> str:
    """The bot's display name for Topic titles. Mirrors the per-platform
    ``RenderState.bot_name`` fallback (config ``bot_app_name`` → "cubeplex")."""
    name = (config or {}).get("bot_app_name")
    return str(name) if name else "cubeplex"


def im_topic_title(*, scope_kind: str, bot_name: str, channel_name: str | None) -> str:
    """Topic title: bot name for a DM, channel name for a group.

    When the platform did not supply a group name, return ``""`` rather than a
    localized phrase. The sidebar already does ``title || t('newGroupChat')``,
    so an empty title stays i18n-clean and is not frozen in the writer's locale.
    """
    title = bot_name if scope_kind == "dm" else (channel_name or "")
    return title[:255]


def build_im_attributes(
    *,
    platform: str,
    account_id: str,
    scope_kind: str,
    bot_name: str,
    bot_avatar_url: str | None,
    channel_id: str,
    channel_name: str | None,
) -> dict[str, Any]:
    """The ``attributes`` payload written onto an IM-created Topic/Conversation.

    Presence of the ``im`` key is the IM-origin marker read by
    ``im/worker.py`` + ``im/resume.py`` (replacing the old binding lookup).
    """
    return {
        "im": {
            "platform": platform,
            "account_id": account_id,
            "scope_kind": scope_kind,
            "bot_name": bot_name,
            "bot_avatar_url": bot_avatar_url,
            "channel_id": channel_id,
            "channel_name": channel_name,
        }
    }
