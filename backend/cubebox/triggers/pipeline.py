"""Event → run pipeline (source-agnostic).

Supports two conversation policies:

- ``new_each_time`` (default): create a draft conversation per event and call
  ``RunManager.start_run`` inline, retrying on transient errors.
- ``im_channel``: resolve the IM-channel conversation via the shared resolver
  and enqueue a synthetic outbox row so the existing ``IMRunQueueWorker``
  drains it like a user-driven inbound message.
"""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger
from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker

from cubebox.im.conversation_resolver import resolve_im_conversation
from cubebox.im.run_handoff import enqueue_im_channel_run
from cubebox.models import Trigger
from cubebox.models.im_connector import IMConnectorAccount
from cubebox.repositories import (
    ConversationRepository,
    MembershipRepository,
    TriggerEventRepository,
    TriggerRepository,
)
from cubebox.streams.run_manager import RunContext, RunManager
from cubebox.triggers.events import NormalizedEvent
from cubebox.triggers.template import render

_MAX_ATTEMPTS = 4
_BACKOFF_BASE_S = 1.0
_BACKOFF_CAP_S = 30.0


class TriggerPipeline:
    def __init__(
        self,
        run_manager: RunManager,
        session_maker: async_sessionmaker[Any],
    ) -> None:
        self._run_manager = run_manager
        self._session_maker = session_maker

    async def fire(
        self,
        trigger: Trigger,
        event: NormalizedEvent,
        event_row_id: str,
    ) -> None:
        async with self._session_maker() as session:
            events_repo = TriggerEventRepository(
                session, org_id=trigger.org_id, workspace_id=trigger.workspace_id
            )

            # 1. Re-validate membership.
            mem_repo = MembershipRepository(session)
            role = await mem_repo.get_role(
                user_id=trigger.run_as_user_id, workspace_id=trigger.workspace_id
            )
            if role is None:
                trig_repo = TriggerRepository(
                    session, org_id=trigger.org_id, workspace_id=trigger.workspace_id
                )
                t = await trig_repo.get(trigger.id)
                if t is not None:
                    t.enabled = False
                    await session.commit()
                await events_repo.set_terminal(
                    event_row_id, "failed", last_error="run_as_user lost membership"
                )
                await _bump_counters(session, trigger.id, total=1, failed=1)
                return

            # 2. Target resolution.
            if trigger.target_type != "inline":
                await events_repo.set_terminal(
                    event_row_id,
                    "failed",
                    last_error="target_type=managed_agent not implemented",
                )
                await _bump_counters(session, trigger.id, total=1, failed=1)
                return

            prompt_template = trigger.target_ref.get("prompt_template", "")
            content = render(
                prompt_template,
                event.payload,
                payload_fields=trigger.payload_fields or [],
                source_label=f"{event.source_type}:{trigger.id}",
            )

            # 3. im_channel policy: synthesize the outbox row and let the
            # IMRunQueueWorker mint the run. Fire-and-forget — no inline
            # start_run retry loop. ``set_terminal`` commits the receipt +
            # queue item flushed by ``enqueue_im_channel_run`` together
            # with the event-row terminal state.
            if trigger.conversation_policy == "im_channel":
                assert trigger.im_account_id is not None
                assert trigger.im_channel_id is not None
                assert trigger.im_scope_key is not None
                assert trigger.im_scope_kind is not None
                account = await session.get(IMConnectorAccount, trigger.im_account_id)
                if account is None:
                    await events_repo.set_terminal(
                        event_row_id, "failed", last_error="im_account_unlinked"
                    )
                    await _bump_counters(session, trigger.id, total=1, failed=1)
                    return

                resolved = await resolve_im_conversation(
                    session,
                    account,
                    channel_id=trigger.im_channel_id,
                    scope_key=trigger.im_scope_key,
                    scope_kind=trigger.im_scope_kind,
                    effective_user_id=trigger.run_as_user_id,
                    title_hint=f"Triggered: {trigger.name}",
                    origin="trigger",
                )

                await enqueue_im_channel_run(
                    session,
                    account=account,
                    conversation_id=resolved.conversation_id,
                    content=content,
                    channel_id=trigger.im_channel_id,
                    scope_key=trigger.im_scope_key,
                    scope_kind=trigger.im_scope_kind,
                    owner_user_id=trigger.run_as_user_id,
                    platform_event_id=f"trigger:{event_row_id}",
                )

                await events_repo.set_terminal(
                    event_row_id,
                    "accepted",
                    conversation_id=resolved.conversation_id,
                )
                await _bump_counters(session, trigger.id, total=1, success=1)
                return

            # 4. new_each_time: create draft conversation (topic-scoped when
            # ``trigger.topic_id`` is set).
            conv_repo = ConversationRepository(
                session,
                org_id=trigger.org_id,
                workspace_id=trigger.workspace_id,
                user_id=trigger.run_as_user_id,
            )
            conv = await conv_repo.create(
                title=f"trigger:{trigger.name}",
                draft=True,
                topic_id=trigger.topic_id,
            )

            ctx = RunContext(
                user_id=trigger.run_as_user_id,
                org_id=trigger.org_id,
                workspace_id=trigger.workspace_id,
                conversation_id=conv.id,
                trigger="automated",
            )

            # 5. start_run with retry/backoff.
            last_err: str | None = None
            for attempt in range(_MAX_ATTEMPTS):
                evt = await events_repo.get(event_row_id)
                if evt is not None:
                    evt.attempts = attempt + 1
                    await session.commit()
                try:
                    run_id = await self._run_manager.start_run(
                        conversation_id=conv.id,
                        content=content,
                        attachments=[],
                        ctx=ctx,
                    )
                except Exception as exc:  # noqa: BLE001
                    last_err = repr(exc)
                    logger.warning(
                        "trigger.fire start_run failed",
                        trigger_id=trigger.id,
                        attempt=attempt + 1,
                        error=last_err,
                    )
                    if attempt + 1 < _MAX_ATTEMPTS:
                        backoff = min(_BACKOFF_CAP_S, _BACKOFF_BASE_S * (2**attempt))
                        await asyncio.sleep(backoff)
                        continue
                    break
                else:
                    await events_repo.set_terminal(
                        event_row_id,
                        "accepted",
                        run_id=run_id,
                        conversation_id=conv.id,
                    )
                    await _bump_counters(session, trigger.id, total=1, success=1)
                    return

            # 6. Exhausted retries.
            await events_repo.set_terminal(event_row_id, "dead_lettered", last_error=last_err)
            await _bump_counters(session, trigger.id, total=1, failed=1)


async def _bump_counters(
    session: Any,
    trigger_id: str,
    *,
    total: int = 0,
    success: int = 0,
    failed: int = 0,
    dedup_dropped: int = 0,
) -> None:
    values: dict[str, Any] = {}
    if total:
        values["events_total"] = Trigger.events_total + total
    if success:
        values["events_success"] = Trigger.events_success + success
    if failed:
        values["events_failed"] = Trigger.events_failed + failed
    if dedup_dropped:
        values["events_dedup_dropped"] = Trigger.events_dedup_dropped + dedup_dropped
    if not values:
        return
    stmt = update(Trigger).where(Trigger.id == trigger_id).values(**values)  # type: ignore[arg-type]
    await session.execute(stmt)
    await session.commit()
