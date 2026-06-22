"""Pure destination-shape validator for schedules and triggers.

A single source of truth so Pydantic schemas, agent tools, and the service
layer enforce identical rules across ``ScheduledTask.target_mode`` and
``Trigger.conversation_policy``. No DB, no Pydantic, no SQLModel — only the
dataclass + ``validate()``.

Why two sibling specs instead of one boolean switch:
``ScheduledTask.target_mode`` has three values (``fixed`` / ``new_each_run``
/ ``im_channel``) and ``Trigger.conversation_policy`` only has two
(``new_each_time`` / ``im_channel``). Folding both onto one dataclass with
a ``policy`` flag would push the discriminator value-space leak into every
caller (``"is this a schedule? then map new_each_time → new_each_run before
constructing"``); a sibling type keeps each route's call site honest.

``validate_destination_scope`` is the only DB-touching helper in this
module — it exists here (not in either service) so both the schedule and
trigger services share one tested place that verifies a caller-supplied
``topic_id`` / ``im_account_id`` actually lives in the caller's
``(org_id, workspace_id)``. Without it, a workspace A user could pass
workspace B's topic id and route runs into B.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

TargetMode = Literal["fixed", "new_each_run", "im_channel"]
ConversationPolicy = Literal["new_each_time", "im_channel"]


class ScheduleTargetError(ValueError):
    """Raised when destination fields do not match the declared mode."""


@dataclass(frozen=True)
class ScheduleTargetSpec:
    """Destination fields for one ``ScheduledTask`` row.

    The dataclass is intentionally permissive about which fields the caller
    populates — ``validate()`` is the only place that enforces the per-mode
    shape constraints from the spec.
    """

    target_mode: str
    target_conversation_id: str | None = None
    topic_id: str | None = None
    im_account_id: str | None = None
    im_channel_id: str | None = None
    im_scope_key: str | None = None
    im_scope_kind: str | None = None

    def validate(self) -> None:
        mode = self.target_mode
        im_fields = (
            self.im_account_id,
            self.im_channel_id,
            self.im_scope_key,
            self.im_scope_kind,
        )
        if mode == "fixed":
            if not self.target_conversation_id:
                raise ScheduleTargetError(
                    "target_conversation_id is required when target_mode='fixed'"
                )
            if self.topic_id:
                raise ScheduleTargetError("topic_id is not allowed when target_mode='fixed'")
            if any(im_fields):
                raise ScheduleTargetError("im_* fields are not allowed when target_mode='fixed'")
        elif mode == "new_each_run":
            if self.target_conversation_id:
                raise ScheduleTargetError(
                    "target_conversation_id is not allowed when target_mode='new_each_run'"
                )
            if any(im_fields):
                raise ScheduleTargetError(
                    "im_* fields are not allowed when target_mode='new_each_run'"
                )
        elif mode == "im_channel":
            if self.target_conversation_id:
                raise ScheduleTargetError(
                    "target_conversation_id is not allowed when target_mode='im_channel'"
                )
            if self.topic_id:
                raise ScheduleTargetError("topic_id is not allowed when target_mode='im_channel'")
            if not all(im_fields):
                raise ScheduleTargetError(
                    "im_account_id, im_channel_id, im_scope_key, and im_scope_kind "
                    "are all required when target_mode='im_channel'"
                )
        else:
            raise ScheduleTargetError(f"unknown target_mode: {mode!r}")


@dataclass(frozen=True)
class TriggerTargetSpec:
    """Destination fields for one ``Trigger`` row.

    Mirrors :class:`ScheduleTargetSpec` but with the trigger-side
    discriminator value space: ``new_each_time`` (semantically equivalent
    to ``new_each_run``) and ``im_channel``.
    """

    conversation_policy: str
    topic_id: str | None = None
    im_account_id: str | None = None
    im_channel_id: str | None = None
    im_scope_key: str | None = None
    im_scope_kind: str | None = None

    def validate(self) -> None:
        policy = self.conversation_policy
        im_fields = (
            self.im_account_id,
            self.im_channel_id,
            self.im_scope_key,
            self.im_scope_kind,
        )
        if policy == "new_each_time":
            if any(im_fields):
                raise ScheduleTargetError(
                    "im_* fields are not allowed when conversation_policy='new_each_time'"
                )
        elif policy == "im_channel":
            if self.topic_id:
                raise ScheduleTargetError(
                    "topic_id is not allowed when conversation_policy='im_channel'"
                )
            if not all(im_fields):
                raise ScheduleTargetError(
                    "im_account_id, im_channel_id, im_scope_key, and im_scope_kind "
                    "are all required when conversation_policy='im_channel'"
                )
        else:
            raise ScheduleTargetError(f"unknown conversation_policy: {policy!r}")


async def validate_destination_scope(
    session: AsyncSession,
    *,
    org_id: str,
    workspace_id: str,
    topic_id: str | None,
    im_account_id: str | None,
) -> None:
    """Reject caller-supplied destination FKs that live in another workspace.

    Both the schedule and trigger services run this before persisting a row
    that carries ``topic_id`` or ``im_account_id`` from user input. The
    repository's ``(org_id, workspace_id)`` columns alone wouldn't catch a
    cross-workspace FK — Postgres only checks the FK exists, not that it
    belongs to the same scope. So a workspace A request that includes
    workspace B's topic id would otherwise succeed at the DB layer and
    silently route runs into workspace B.

    Raises :class:`ScheduleTargetError` (a ``ValueError`` subclass) on
    mismatch so both REST routes (which map it to 422) and agent tools
    (which surface ``is_error=True``) get the same shape.
    """
    # Local imports so this module stays import-cheap; the avoidance of
    # top-level model imports is intentional — pulling the SQLModel registry
    # at module import time would force a circular import with the migrations.
    from cubebox.models.im_connector import IMConnectorAccount
    from cubebox.models.topic import Topic

    if topic_id is not None:
        topic = (
            await session.execute(
                select(Topic).where(
                    Topic.id == topic_id,  # type: ignore[arg-type]
                    Topic.org_id == org_id,  # type: ignore[arg-type]
                    Topic.workspace_id == workspace_id,  # type: ignore[arg-type]
                )
            )
        ).scalar_one_or_none()
        if topic is None:
            raise ScheduleTargetError(f"topic_id {topic_id!r} not found in this workspace")

    if im_account_id is not None:
        account = (
            await session.execute(
                select(IMConnectorAccount).where(
                    IMConnectorAccount.id == im_account_id,  # type: ignore[arg-type]
                    IMConnectorAccount.org_id == org_id,  # type: ignore[arg-type]
                    IMConnectorAccount.workspace_id == workspace_id,  # type: ignore[arg-type]
                )
            )
        ).scalar_one_or_none()
        if account is None:
            raise ScheduleTargetError(
                f"im_account_id {im_account_id!r} not found in this workspace"
            )
