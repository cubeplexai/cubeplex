"""Shared schedule/trigger destination derivation with IM-aware resolution.

Both the always-on ``create_scheduled_task`` tool and the deferred
``scheduled_tasks_create`` capability must agree on destination shape so
"send results here" inside an IM conversation becomes ``im_channel``
(survives ``/new``), not a pinned ``fixed`` conversation id.

IM resolution order (first hit wins):

1. ``IMThreadLink`` for this ``conversation_id`` (live binding).
2. ``IMThreadLink`` for the conversation's ``topic_id`` (post-``/new``;
   topic anchor survives rotation).
3. Unique ``IMThreadLink`` matching ``attributes.im`` account+channel
   (and scope_kind when present) on the conversation or its topic.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

Intent = Literal[
    "auto",
    "fixed",
    "new_each_run",
    "im_channel",
    "current_conversation",
]


@dataclass(frozen=True)
class ImLinkSnapshot:
    """The four im_* columns required by ``target_mode='im_channel'``."""

    im_account_id: str
    im_channel_id: str
    im_scope_key: str
    im_scope_kind: str


@dataclass(frozen=True)
class DerivedScheduleDestination:
    """Resolved destination fields ready to pass into ``ScheduledTaskService.create``."""

    target_mode: str
    target_conversation_id: str | None = None
    topic_id: str | None = None
    im_account_id: str | None = None
    im_channel_id: str | None = None
    im_scope_key: str | None = None
    im_scope_kind: str | None = None

    def as_create_fields(self) -> dict[str, Any]:
        return {
            "target_mode": self.target_mode,
            "target_conversation_id": self.target_conversation_id,
            "topic_id": self.topic_id,
            "im_account_id": self.im_account_id,
            "im_channel_id": self.im_channel_id,
            "im_scope_key": self.im_scope_key,
            "im_scope_kind": self.im_scope_kind,
        }


def pick_im_destination(
    *,
    link_for_conversation: ImLinkSnapshot | None,
    link_for_topic: ImLinkSnapshot | None,
    links_for_account_channel: Sequence[ImLinkSnapshot],
) -> ImLinkSnapshot | None:
    """Pure priority pick — unit-tested without a DB."""
    if link_for_conversation is not None:
        return link_for_conversation
    if link_for_topic is not None:
        return link_for_topic
    if len(links_for_account_channel) == 1:
        return links_for_account_channel[0]
    return None


def _snapshot_from_link(link: Any) -> ImLinkSnapshot:
    return ImLinkSnapshot(
        im_account_id=link.account_id,
        im_channel_id=link.channel_id,
        im_scope_key=link.scope_key,
        im_scope_kind=link.scope_kind,
    )


async def resolve_im_destination_for_conversation(
    session: AsyncSession,
    *,
    org_id: str,
    workspace_id: str,
    conversation_id: str,
) -> ImLinkSnapshot | None:
    """Load IM destination fields for a conversation, if it is IM-bound."""
    from cubeplex.models.conversation import Conversation
    from cubeplex.models.im_connector import IMThreadLink
    from cubeplex.models.topic import Topic

    live = (
        await session.execute(
            select(IMThreadLink).where(
                IMThreadLink.conversation_id == conversation_id,  # type: ignore[arg-type]
                IMThreadLink.org_id == org_id,  # type: ignore[arg-type]
                IMThreadLink.workspace_id == workspace_id,  # type: ignore[arg-type]
            )
        )
    ).scalar_one_or_none()
    if live is not None:
        return _snapshot_from_link(live)

    conv = await session.get(Conversation, conversation_id)
    if conv is None:
        return None
    if conv.org_id != org_id or conv.workspace_id != workspace_id:
        return None

    if conv.topic_id is not None:
        topic_link = (
            await session.execute(
                select(IMThreadLink).where(
                    IMThreadLink.topic_id == conv.topic_id,  # type: ignore[arg-type]
                    IMThreadLink.org_id == org_id,  # type: ignore[arg-type]
                    IMThreadLink.workspace_id == workspace_id,  # type: ignore[arg-type]
                )
            )
        ).scalar_one_or_none()
        if topic_link is not None:
            return _snapshot_from_link(topic_link)

    im_blob = _im_attrs(conv.attributes)
    if im_blob is None and conv.topic_id is not None:
        topic = await session.get(Topic, conv.topic_id)
        if topic is not None:
            im_blob = _im_attrs(topic.attributes)

    links_for_account_channel: list[ImLinkSnapshot] = []
    if im_blob is not None:
        account_id = im_blob.get("account_id")
        channel_id = im_blob.get("channel_id")
        scope_kind = im_blob.get("scope_kind")
        if isinstance(account_id, str) and isinstance(channel_id, str):
            stmt = select(IMThreadLink).where(
                IMThreadLink.account_id == account_id,  # type: ignore[arg-type]
                IMThreadLink.channel_id == channel_id,  # type: ignore[arg-type]
                IMThreadLink.org_id == org_id,  # type: ignore[arg-type]
                IMThreadLink.workspace_id == workspace_id,  # type: ignore[arg-type]
            )
            if isinstance(scope_kind, str) and scope_kind:
                stmt = stmt.where(
                    IMThreadLink.scope_kind == scope_kind,  # type: ignore[arg-type]
                )
            rows = (await session.execute(stmt)).scalars().all()
            links_for_account_channel = [_snapshot_from_link(r) for r in rows]

    return pick_im_destination(
        link_for_conversation=None,
        link_for_topic=None,
        links_for_account_channel=links_for_account_channel,
    )


def _im_attrs(attributes: dict[str, Any] | None) -> dict[str, Any] | None:
    if not attributes:
        return None
    im = attributes.get("im")
    return im if isinstance(im, dict) else None


async def resolve_im_destination_for_topic(
    session: AsyncSession,
    *,
    org_id: str,
    workspace_id: str,
    topic_id: str,
) -> ImLinkSnapshot | None:
    """Resolve IM fields from the topic's live ``IMThreadLink`` (post-``/new``)."""
    from cubeplex.models.im_connector import IMThreadLink

    link = (
        await session.execute(
            select(IMThreadLink).where(
                IMThreadLink.topic_id == topic_id,  # type: ignore[arg-type]
                IMThreadLink.org_id == org_id,  # type: ignore[arg-type]
                IMThreadLink.workspace_id == workspace_id,  # type: ignore[arg-type]
            )
        )
    ).scalar_one_or_none()
    return _snapshot_from_link(link) if link is not None else None


def derive_schedule_destination(
    *,
    intent: Intent,
    conversation_id: str | None,
    im: ImLinkSnapshot | None,
    topic_id: str | None,
    target_conversation_id: str | None = None,
    explicit_topic_id: str | None = None,
) -> DerivedScheduleDestination:
    """Map caller intent + resolved IM binding to schedule destination fields.

    Pure function (no DB). ``topic_id`` is the conversation's topic (for
    inheritance); ``explicit_topic_id`` is a caller override when set.
    """
    if intent == "new_each_run":
        return DerivedScheduleDestination(
            target_mode="new_each_run",
            topic_id=explicit_topic_id if explicit_topic_id is not None else topic_id,
        )

    if intent == "fixed":
        fixed_id = target_conversation_id or conversation_id
        if not fixed_id:
            raise ValueError(
                "target_mode='fixed' requires a conversation id "
                "(pass target_conversation_id or call from a conversation)"
            )
        return DerivedScheduleDestination(
            target_mode="fixed",
            target_conversation_id=fixed_id,
        )

    if intent == "im_channel":
        if im is None:
            raise ValueError(
                "im_channel target requires this conversation to be bound to an "
                "IM channel; no IMThreadLink (or attributes.im fallback) found "
                "for the current conversation."
            )
        return DerivedScheduleDestination(
            target_mode="im_channel",
            im_account_id=im.im_account_id,
            im_channel_id=im.im_channel_id,
            im_scope_key=im.im_scope_key,
            im_scope_kind=im.im_scope_kind,
        )

    # auto | current_conversation — prefer IM when resolvable
    if intent == "current_conversation" and not conversation_id:
        raise ValueError(
            "target='current_conversation' requires a conversation context; "
            "either start from within a conversation or use target='new_each_run'."
        )

    if im is not None:
        return DerivedScheduleDestination(
            target_mode="im_channel",
            im_account_id=im.im_account_id,
            im_channel_id=im.im_channel_id,
            im_scope_key=im.im_scope_key,
            im_scope_kind=im.im_scope_kind,
        )

    if not conversation_id:
        raise ValueError("cannot derive a fixed destination without a conversation id")
    return DerivedScheduleDestination(
        target_mode="fixed",
        target_conversation_id=target_conversation_id or conversation_id,
    )


async def derive_schedule_destination_for_conversation(
    session: AsyncSession,
    *,
    org_id: str,
    workspace_id: str,
    conversation_id: str | None,
    intent: Intent,
    target_conversation_id: str | None = None,
    explicit_topic_id: str | None = None,
) -> DerivedScheduleDestination:
    """DB-backed convenience: resolve IM + conversation topic, then derive."""
    from cubeplex.models.conversation import Conversation

    im: ImLinkSnapshot | None = None
    topic_id: str | None = None
    if conversation_id is not None:
        im = await resolve_im_destination_for_conversation(
            session,
            org_id=org_id,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
        )
        # Topic inherit for new_each_run when caller omitted topic_id.
        if intent == "new_each_run" and explicit_topic_id is None:
            conv = await session.get(Conversation, conversation_id)
            if conv is not None and conv.org_id == org_id and conv.workspace_id == workspace_id:
                topic_id = conv.topic_id

    return derive_schedule_destination(
        intent=intent,
        conversation_id=conversation_id,
        im=im,
        topic_id=topic_id,
        target_conversation_id=target_conversation_id,
        explicit_topic_id=explicit_topic_id,
    )
