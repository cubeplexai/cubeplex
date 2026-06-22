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
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

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
