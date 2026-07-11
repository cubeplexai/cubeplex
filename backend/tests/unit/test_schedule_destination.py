"""Unit tests for schedule destination derivation (IM-aware).

Covers the shared path used by both ``create_scheduled_task`` and
``scheduled_tasks_create`` so "results go here" inside an IM conversation
becomes ``im_channel``, not a pinned ``fixed`` conversation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from cubebox.services.schedule_destination import (
    DerivedScheduleDestination,
    ImLinkSnapshot,
    derive_schedule_destination,
    pick_im_destination,
    resolve_im_destination_for_conversation,
    resolve_im_destination_for_topic,
)

# ---------------------------------------------------------------------------
# pick_im_destination — pure priority rules
# ---------------------------------------------------------------------------


def _snap(
    account: str = "imac-1",
    channel: str = "oc-1",
    scope_key: str = "dm",
    scope_kind: str = "dm",
) -> ImLinkSnapshot:
    return ImLinkSnapshot(
        im_account_id=account,
        im_channel_id=channel,
        im_scope_key=scope_key,
        im_scope_kind=scope_kind,
    )


def test_pick_prefers_link_for_conversation() -> None:
    live = _snap(channel="live")
    topic = _snap(channel="topic")
    assert (
        pick_im_destination(
            link_for_conversation=live,
            link_for_topic=topic,
            links_for_account_channel=[_snap(channel="attrs")],
        )
        == live
    )


def test_pick_falls_back_to_topic_link_after_new() -> None:
    """Post-/new: conversation_id no longer matches live link, but topic does."""
    topic = _snap(channel="topic")
    assert (
        pick_im_destination(
            link_for_conversation=None,
            link_for_topic=topic,
            links_for_account_channel=[],
        )
        == topic
    )


def test_pick_falls_back_to_unique_account_channel_link() -> None:
    only = _snap(channel="from-attrs")
    assert (
        pick_im_destination(
            link_for_conversation=None,
            link_for_topic=None,
            links_for_account_channel=[only],
        )
        == only
    )


def test_pick_ambiguous_account_channel_returns_none() -> None:
    assert (
        pick_im_destination(
            link_for_conversation=None,
            link_for_topic=None,
            links_for_account_channel=[_snap(scope_key="dm"), _snap(scope_key="ch")],
        )
        is None
    )


def test_pick_all_empty_returns_none() -> None:
    assert (
        pick_im_destination(
            link_for_conversation=None,
            link_for_topic=None,
            links_for_account_channel=[],
        )
        is None
    )


# ---------------------------------------------------------------------------
# derive_schedule_destination — intent → target_mode mapping
# ---------------------------------------------------------------------------


def test_derive_auto_with_im_becomes_im_channel() -> None:
    im = _snap()
    result = derive_schedule_destination(
        intent="auto",
        conversation_id="conv-1",
        im=im,
        topic_id=None,
    )
    assert result == DerivedScheduleDestination(
        target_mode="im_channel",
        target_conversation_id=None,
        topic_id=None,
        im_account_id=im.im_account_id,
        im_channel_id=im.im_channel_id,
        im_scope_key=im.im_scope_key,
        im_scope_kind=im.im_scope_kind,
    )


def test_derive_auto_without_im_becomes_fixed_current() -> None:
    result = derive_schedule_destination(
        intent="auto",
        conversation_id="conv-1",
        im=None,
        topic_id=None,
    )
    assert result.target_mode == "fixed"
    assert result.target_conversation_id == "conv-1"
    assert result.im_account_id is None


def test_derive_current_conversation_with_im_upgrades_to_im_channel() -> None:
    """Capability target='current_conversation' must not pin fixed when IM-bound."""
    im = _snap()
    result = derive_schedule_destination(
        intent="current_conversation",
        conversation_id="conv-old",
        im=im,
        topic_id=None,
    )
    assert result.target_mode == "im_channel"
    assert result.target_conversation_id is None
    assert result.im_channel_id == im.im_channel_id


def test_derive_current_conversation_without_im_is_fixed() -> None:
    result = derive_schedule_destination(
        intent="current_conversation",
        conversation_id="conv-web",
        im=None,
        topic_id=None,
    )
    assert result.target_mode == "fixed"
    assert result.target_conversation_id == "conv-web"


def test_derive_current_conversation_requires_conversation_id() -> None:
    with pytest.raises(ValueError, match="conversation"):
        derive_schedule_destination(
            intent="current_conversation",
            conversation_id=None,
            im=None,
            topic_id=None,
        )


def test_derive_explicit_fixed_ignores_im() -> None:
    """Agent/API asked for fixed — do not silently upgrade to im_channel."""
    result = derive_schedule_destination(
        intent="fixed",
        conversation_id="conv-1",
        im=_snap(),
        topic_id=None,
        target_conversation_id=None,
    )
    assert result.target_mode == "fixed"
    assert result.target_conversation_id == "conv-1"
    assert result.im_account_id is None


def test_derive_explicit_im_channel_requires_im() -> None:
    with pytest.raises(ValueError, match="im_channel|IM"):
        derive_schedule_destination(
            intent="im_channel",
            conversation_id="conv-1",
            im=None,
            topic_id=None,
        )


def test_derive_new_each_run_inherits_topic() -> None:
    result = derive_schedule_destination(
        intent="new_each_run",
        conversation_id="conv-1",
        im=None,
        topic_id="top-1",
    )
    assert result.target_mode == "new_each_run"
    assert result.topic_id == "top-1"
    assert result.target_conversation_id is None


def test_derive_new_each_run_explicit_topic_wins() -> None:
    result = derive_schedule_destination(
        intent="new_each_run",
        conversation_id="conv-1",
        im=None,
        topic_id="top-from-conv",
        explicit_topic_id="top-explicit",
    )
    assert result.topic_id == "top-explicit"


# ---------------------------------------------------------------------------
# resolve_im_destination_for_conversation — async DB loader (mocked session)
# ---------------------------------------------------------------------------


@dataclass
class _FakeLink:
    account_id: str
    channel_id: str
    scope_key: str
    scope_kind: str
    conversation_id: str
    topic_id: str | None = None
    org_id: str = "org-1"
    workspace_id: str = "ws-1"


@dataclass
class _FakeConv:
    id: str
    org_id: str = "org-1"
    workspace_id: str = "ws-1"
    topic_id: str | None = None
    attributes: dict[str, Any] | None = None


def _result(rows: list[Any]) -> MagicMock:
    r = MagicMock()
    if len(rows) == 1:
        r.scalar_one_or_none.return_value = rows[0]
    else:
        r.scalar_one_or_none.return_value = rows[0] if rows else None
    r.scalars.return_value.all.return_value = rows
    return r


@pytest.mark.asyncio
async def test_resolve_by_conversation_link() -> None:
    link = _FakeLink(
        account_id="imac-1",
        channel_id="oc-1",
        scope_key="dm",
        scope_kind="dm",
        conversation_id="conv-1",
    )
    session = AsyncMock()
    session.execute = AsyncMock(return_value=_result([link]))

    got = await resolve_im_destination_for_conversation(
        session,
        org_id="org-1",
        workspace_id="ws-1",
        conversation_id="conv-1",
    )
    assert got == ImLinkSnapshot("imac-1", "oc-1", "dm", "dm")
    # Short-circuit: no get(Conversation) needed when live link hits.
    session.get.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_by_topic_link_after_new() -> None:
    """Live link points elsewhere; conversation still shares the IM topic."""
    topic_link = _FakeLink(
        account_id="imac-1",
        channel_id="oc-1",
        scope_key="dm",
        scope_kind="dm",
        conversation_id="conv-new",
        topic_id="top-1",
    )
    conv = _FakeConv(id="conv-old", topic_id="top-1", attributes={"im": {}})

    session = AsyncMock()
    # 1st execute: link by conversation → miss; 2nd: link by topic → hit
    session.execute = AsyncMock(
        side_effect=[_result([]), _result([topic_link])],
    )
    session.get = AsyncMock(return_value=conv)

    got = await resolve_im_destination_for_conversation(
        session,
        org_id="org-1",
        workspace_id="ws-1",
        conversation_id="conv-old",
    )
    assert got is not None
    assert got.im_channel_id == "oc-1"
    assert got.im_scope_key == "dm"


@pytest.mark.asyncio
async def test_resolve_by_attributes_im_account_channel() -> None:
    attrs_link = _FakeLink(
        account_id="imac-1",
        channel_id="oc-1",
        scope_key="dm",
        scope_kind="dm",
        conversation_id="conv-live",
    )
    conv = _FakeConv(
        id="conv-old",
        topic_id=None,
        attributes={
            "im": {
                "platform": "feishu",
                "account_id": "imac-1",
                "channel_id": "oc-1",
                "scope_kind": "dm",
            }
        },
    )
    session = AsyncMock()
    # conversation link miss; no topic; then account+channel hit
    session.execute = AsyncMock(
        side_effect=[_result([]), _result([attrs_link])],
    )
    session.get = AsyncMock(return_value=conv)

    got = await resolve_im_destination_for_conversation(
        session,
        org_id="org-1",
        workspace_id="ws-1",
        conversation_id="conv-old",
    )
    assert got == ImLinkSnapshot("imac-1", "oc-1", "dm", "dm")


@pytest.mark.asyncio
async def test_resolve_for_topic_link() -> None:
    link = _FakeLink(
        account_id="imac-1",
        channel_id="oc-1",
        scope_key="dm",
        scope_kind="dm",
        conversation_id="conv-live",
        topic_id="top-1",
    )
    session = AsyncMock()
    session.execute = AsyncMock(return_value=_result([link]))
    got = await resolve_im_destination_for_topic(
        session,
        org_id="org-1",
        workspace_id="ws-1",
        topic_id="top-1",
    )
    assert got == ImLinkSnapshot("imac-1", "oc-1", "dm", "dm")
