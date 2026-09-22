"""Durable trigger-event admission and handoff."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from cubeloop.providers.base import ReasoningControl
from loguru import logger
from pydantic import BaseModel, ConfigDict
from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from uuid_utils import uuid7

from cubeplex.im.conversation_resolver import resolve_im_conversation
from cubeplex.im.run_handoff import enqueue_im_channel_run
from cubeplex.llm.resolver import resolve_model_preset
from cubeplex.llm.snapshot import LLMSnapshot
from cubeplex.models import Trigger
from cubeplex.models.conversation import Conversation
from cubeplex.models.conversation_execution import ConversationExecutionAdmission
from cubeplex.models.im_connector import IMConnectorAccount, IMRunQueueItem, IMThreadLink
from cubeplex.models.trigger import TriggerEvent
from cubeplex.repositories import ConversationRepository, MembershipRepository
from cubeplex.services.conversation_execution import (
    ConversationExecutionService,
    ExecutionConflictError,
    ExecutionRevokedError,
    ResolvedExecution,
    UserMessageIntent,
)
from cubeplex.streams.run_manager import RunContext, RunManager
from cubeplex.triggers.events import NormalizedEvent
from cubeplex.triggers.template import render

_MAX_ATTEMPTS = 4
_BACKOFF_BASE_S = 1.0
_BACKOFF_CAP_S = 30.0


class TriggerExecutionSnapshot(BaseModel):
    """Immutable work derived before an event becomes asynchronously consumable."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    trigger_id: str
    name: str
    actor_user_id: str
    target_type: str
    conversation_policy: str
    topic_id: str | None = None
    im_account_id: str | None = None
    im_channel_id: str | None = None
    im_scope_key: str | None = None
    im_scope_kind: str | None = None
    bound_conversation_id: str | None = None
    bound_execution_generation: int | None = None
    bound_generation_closed: bool | None = None
    content: str
    execution: ResolvedExecution


def trigger_execution_source_id(event: TriggerEvent) -> str:
    return f"{event.id}:{event.execution_revision}"


async def freeze_trigger_event(
    session: AsyncSession,
    *,
    trigger: Trigger,
    event: TriggerEvent,
    llm_snapshot: LLMSnapshot,
) -> TriggerExecutionSnapshot:
    content = render(
        trigger.target_ref.get("prompt_template", ""),
        event.payload,
        payload_fields=trigger.payload_fields or [],
        source_label=f"{event.source_type}:{trigger.id}",
    )
    bound_conversation_id: str | None = None
    if (
        trigger.conversation_policy == "im_channel"
        and trigger.im_account_id is not None
        and trigger.im_channel_id is not None
        and trigger.im_scope_key is not None
    ):
        bound_conversation_id = await session.scalar(
            select(cast(Any, IMThreadLink.conversation_id)).where(
                cast(Any, IMThreadLink.account_id) == trigger.im_account_id,
                cast(Any, IMThreadLink.channel_id) == trigger.im_channel_id,
                cast(Any, IMThreadLink.scope_key) == trigger.im_scope_key,
            )
        )
    bound_conversation = (
        await session.get(Conversation, bound_conversation_id)
        if bound_conversation_id is not None
        else None
    )
    preset = resolve_model_preset(llm_snapshot, None)
    return TriggerExecutionSnapshot(
        trigger_id=trigger.id,
        name=trigger.name,
        actor_user_id=trigger.run_as_user_id,
        target_type=trigger.target_type,
        conversation_policy=trigger.conversation_policy,
        topic_id=trigger.topic_id,
        im_account_id=trigger.im_account_id,
        im_channel_id=trigger.im_channel_id,
        im_scope_key=trigger.im_scope_key,
        im_scope_kind=trigger.im_scope_kind,
        bound_conversation_id=(bound_conversation.id if bound_conversation is not None else None),
        bound_execution_generation=(
            bound_conversation.execution_generation if bound_conversation is not None else None
        ),
        bound_generation_closed=(
            bound_conversation.execution_closed_at is not None
            if bound_conversation is not None
            else None
        ),
        content=content,
        execution=ResolvedExecution(
            model_key=preset.key,
            primary=preset.primary,
            fallbacks=preset.fallbacks,
            reasoning=ReasoningControl(),
            trigger="automated",
        ),
    )


class TriggerPipeline:
    def __init__(
        self,
        run_manager: RunManager,
        session_maker: async_sessionmaker[Any],
        load_execution_snapshot: Callable[[AsyncSession, str], Awaitable[LLMSnapshot]],
        *,
        max_attempts: int = _MAX_ATTEMPTS,
    ) -> None:
        self._run_manager = run_manager
        self._session_maker = session_maker
        self._load_execution_snapshot = load_execution_snapshot
        self._max_attempts = max_attempts

    async def fire(
        self,
        trigger: Trigger,
        normalized: NormalizedEvent,
        event_id: str,
    ) -> None:
        """Synchronously drive one persisted event; retained for focused tests."""
        owner = str(uuid7())
        async with self._session_maker() as session:
            locked_trigger = await session.get(Trigger, trigger.id, with_for_update=True)
            event = await session.get(TriggerEvent, event_id, with_for_update=True)
            if locked_trigger is None or event is None:
                return
            if event.execution_snapshot is None:
                event.payload = normalized.payload
                llm_snapshot = await self._load_execution_snapshot(session, event.org_id)
                snapshot = await freeze_trigger_event(
                    session,
                    trigger=locked_trigger,
                    event=event,
                    llm_snapshot=llm_snapshot,
                )
                event.execution_snapshot = snapshot.model_dump(mode="json")
            event.status = "claimed"
            event.claim_owner = owner
            event.claim_lease_expires_at = datetime.now(UTC) + timedelta(seconds=120)
            event.next_attempt_at = None
            event.attempts += 1
            await session.commit()
        await self.process_claimed(event_id, claim_owner=owner)

    async def process_claimed(self, event_id: str, *, claim_owner: str) -> None:
        """Process one lease-owned event without holding source locks over RunManager."""
        terminal_failure: tuple[str, str] | None = None
        retry_error: str | None = None
        async with self._session_maker() as session:
            observed = await session.get(TriggerEvent, event_id)
            if observed is None:
                return
            trigger = await session.get(Trigger, observed.trigger_id, with_for_update=True)
            event = await session.get(
                TriggerEvent,
                event_id,
                with_for_update=True,
                populate_existing=True,
            )
            if event is None or event.status != "claimed" or event.claim_owner != claim_owner:
                return
            if trigger is None or trigger.deleted_at is not None or not trigger.enabled:
                await self._finish_unstarted(
                    session,
                    trigger,
                    event,
                    status="cancelled",
                    error="trigger disabled before execution",
                )
                return
            if event.execution_snapshot is None:
                await self._finish_unstarted(
                    session,
                    trigger,
                    event,
                    status="failed",
                    error="missing immutable trigger execution snapshot",
                )
                return
            try:
                snapshot = TriggerExecutionSnapshot.model_validate(event.execution_snapshot)
            except ValueError as exc:
                await self._finish_unstarted(
                    session,
                    trigger,
                    event,
                    status="failed",
                    error=f"invalid immutable trigger execution snapshot: {exc}",
                )
                return
            role = await MembershipRepository(session).get_role(
                user_id=snapshot.actor_user_id,
                workspace_id=event.workspace_id,
            )
            if role is None:
                trigger.enabled = False
                await self._finish_unstarted(
                    session,
                    trigger,
                    event,
                    status="failed",
                    error="run_as_user lost membership",
                )
                return
            if snapshot.target_type != "inline":
                await self._finish_unstarted(
                    session,
                    trigger,
                    event,
                    status="failed",
                    error="target_type=managed_agent not implemented",
                )
                return
            try:
                llm_snapshot = await self._load_execution_snapshot(session, event.org_id)
                if snapshot.conversation_policy == "im_channel":
                    await self._handoff_im(session, trigger, event, snapshot, llm_snapshot)
                    return
                await self._prepare_direct(session, event, snapshot, llm_snapshot)
            except ExecutionRevokedError as exc:
                terminal_failure = (
                    "cancelled",
                    f"execution no longer authorized: {exc}",
                )
            except LookupError as exc:
                terminal_failure = (
                    "cancelled",
                    f"execution target is no longer available: {exc}",
                )
            except ExecutionConflictError as exc:
                terminal_failure = (
                    "failed",
                    f"immutable trigger occurrence conflict: {exc}",
                )
            except ValueError as exc:
                terminal_failure = (
                    "failed",
                    f"invalid trigger occurrence: {exc}",
                )
            except Exception as exc:  # noqa: BLE001
                retry_error = repr(exc)

        if terminal_failure is not None:
            status, error = terminal_failure
            await self._finish_claimed(
                event_id,
                claim_owner=claim_owner,
                status=status,
                error=error,
            )
            return
        if retry_error is not None:
            logger.warning(
                "trigger event preparation failed",
                event_id=event_id,
                error=retry_error,
            )
            await self._retry_or_dead_letter(
                event_id,
                claim_owner=claim_owner,
                error=retry_error,
            )
            return

        conversation_id = event.resulting_conversation_id
        run_id = event.resulting_run_id
        admission_id = event.execution_admission_id
        assert conversation_id is not None and run_id is not None and admission_id is not None
        try:
            actual_run_id = await self._run_manager.start_run(
                conversation_id=conversation_id,
                content=snapshot.content,
                attachments=[],
                ctx=RunContext(
                    user_id=snapshot.actor_user_id,
                    org_id=event.org_id,
                    workspace_id=event.workspace_id,
                    conversation_id=conversation_id,
                    trigger="automated",
                ),
                run_id=run_id,
                model_key=snapshot.execution.model_key,
                reasoning=snapshot.execution.reasoning,
                llm_snapshot=llm_snapshot,
                admission_id=admission_id,
            )
        except ExecutionRevokedError as exc:
            await self._finish_claimed(
                event_id,
                claim_owner=claim_owner,
                status="cancelled",
                error=f"execution no longer authorized: {exc}",
            )
            return
        except ExecutionConflictError as exc:
            await self._finish_claimed(
                event_id,
                claim_owner=claim_owner,
                status="failed",
                error=f"immutable trigger occurrence conflict: {exc}",
            )
            return
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "trigger event start_run failed",
                trigger_id=snapshot.trigger_id,
                event_id=event_id,
                error=repr(exc),
            )
            await self._retry_or_dead_letter(event_id, claim_owner=claim_owner, error=repr(exc))
            return
        await self._record_accepted(
            event_id,
            claim_owner=claim_owner,
            run_id=actual_run_id,
        )

    async def _finish_claimed(
        self,
        event_id: str,
        *,
        claim_owner: str,
        status: str,
        error: str,
    ) -> None:
        async with self._session_maker() as session:
            observed = await session.get(TriggerEvent, event_id)
            if observed is None:
                return
            trigger = await session.get(Trigger, observed.trigger_id, with_for_update=True)
            event = await session.get(
                TriggerEvent,
                event_id,
                with_for_update=True,
                populate_existing=True,
            )
            if event is None or event.status != "claimed" or event.claim_owner != claim_owner:
                return
            await self._finish_unstarted(
                session,
                trigger,
                event,
                status=status,
                error=error,
            )

    async def _prepare_direct(
        self,
        session: AsyncSession,
        event: TriggerEvent,
        snapshot: TriggerExecutionSnapshot,
        llm_snapshot: LLMSnapshot,
    ) -> None:
        repo = ConversationRepository(
            session,
            org_id=event.org_id,
            workspace_id=event.workspace_id,
            user_id=snapshot.actor_user_id,
        )
        if event.resulting_conversation_id is None:
            conversation = await repo.create(
                title=f"trigger:{snapshot.name}",
                draft=True,
                topic_id=snapshot.topic_id,
                commit=False,
            )
            event.resulting_conversation_id = conversation.id
        elif await repo.get_by_id(event.resulting_conversation_id) is None:
            raise LookupError("bound trigger conversation is no longer accessible")
        event.resulting_run_id = event.resulting_run_id or str(uuid7())
        admitted = await ConversationExecutionService(
            session,
            org_id=event.org_id,
            workspace_id=event.workspace_id,
        ).admit_automatic_run(
            conversation_id=event.resulting_conversation_id,
            actor_user_id=snapshot.actor_user_id,
            source_kind="trigger_occurrence",
            source_id=trigger_execution_source_id(event),
            intent=UserMessageIntent(
                content=snapshot.content,
                model_key=snapshot.execution.model_key,
                reasoning=snapshot.execution.reasoning,
            ),
            execution=snapshot.execution,
            snapshot=llm_snapshot,
            now=datetime.now(UTC),
            run_id=event.resulting_run_id,
        )
        event.execution_admission_id = admitted.admission.id
        event.resulting_run_id = admitted.admission.run_id
        admission = admitted.admission
        if admission.run_start_token is None and (
            admission.revoked_at is not None
            or admission.run_stop_requested_at is not None
            or admission.run_finished_at is not None
        ):
            raise ExecutionRevokedError("trigger occurrence was revoked before start")
        await session.commit()

    async def _handoff_im(
        self,
        session: AsyncSession,
        trigger: Trigger,
        event: TriggerEvent,
        snapshot: TriggerExecutionSnapshot,
        llm_snapshot: LLMSnapshot,
    ) -> None:
        if snapshot.im_account_id is None:
            await self._finish_unstarted(
                session, trigger, event, status="failed", error="im_account_unlinked"
            )
            return
        assert snapshot.im_channel_id is not None
        assert snapshot.im_scope_key is not None
        assert snapshot.im_scope_kind is not None
        account = await session.get(IMConnectorAccount, snapshot.im_account_id)
        if account is None or not account.enabled:
            await self._finish_unstarted(
                session, trigger, event, status="failed", error="im_account_unlinked"
            )
            return
        resolved = await resolve_im_conversation(
            session,
            account,
            channel_id=snapshot.im_channel_id,
            scope_key=snapshot.im_scope_key,
            scope_kind=snapshot.im_scope_kind,
            effective_user_id=snapshot.actor_user_id,
            title_hint=f"Triggered: {snapshot.name}",
            origin="trigger",
        )
        event.resulting_conversation_id = resolved.conversation_id
        admitted = await ConversationExecutionService(
            session,
            org_id=event.org_id,
            workspace_id=event.workspace_id,
        ).admit_automatic_run(
            conversation_id=resolved.conversation_id,
            actor_user_id=snapshot.actor_user_id,
            source_kind="trigger_occurrence",
            source_id=trigger_execution_source_id(event),
            intent=UserMessageIntent(
                content=snapshot.content,
                model_key=snapshot.execution.model_key,
                reasoning=snapshot.execution.reasoning,
            ),
            execution=snapshot.execution,
            snapshot=llm_snapshot,
            now=datetime.now(UTC),
            run_id=event.resulting_run_id,
            expected_execution_generation=(
                snapshot.bound_execution_generation
                if resolved.conversation_id == snapshot.bound_conversation_id
                else None
            ),
            expected_generation_closed=(
                snapshot.bound_generation_closed
                if resolved.conversation_id == snapshot.bound_conversation_id
                else None
            ),
        )
        event.execution_admission_id = admitted.admission.id
        event.resulting_run_id = admitted.admission.run_id
        await enqueue_im_channel_run(
            session,
            account=account,
            conversation_id=resolved.conversation_id,
            content=snapshot.content,
            channel_id=snapshot.im_channel_id,
            scope_key=snapshot.im_scope_key,
            scope_kind=snapshot.im_scope_kind,
            owner_user_id=snapshot.actor_user_id,
            platform_event_id=f"trigger:{trigger_execution_source_id(event)}",
            execution_admission_id=admitted.admission.id,
        )
        self._mark_terminal(event, trigger, status="accepted", success=True)
        await session.commit()

    async def _record_accepted(self, event_id: str, *, claim_owner: str, run_id: str) -> None:
        async with self._session_maker() as session:
            observed = await session.get(TriggerEvent, event_id)
            if observed is None:
                return
            trigger = await session.get(Trigger, observed.trigger_id, with_for_update=True)
            event = await session.get(
                TriggerEvent, event_id, with_for_update=True, populate_existing=True
            )
            if (
                trigger is None
                or event is None
                or event.status != "claimed"
                or event.claim_owner != claim_owner
            ):
                return
            admission = (
                await session.get(ConversationExecutionAdmission, event.execution_admission_id)
                if event.execution_admission_id is not None
                else None
            )
            if (
                admission is not None
                and admission.run_start_token is None
                and (
                    admission.revoked_at is not None
                    or admission.run_stop_requested_at is not None
                    or admission.run_finished_at is not None
                )
            ):
                self._mark_terminal(
                    event,
                    trigger,
                    status="cancelled",
                    error="trigger occurrence revoked before run start",
                    failed=True,
                )
            else:
                event.resulting_run_id = run_id
                self._mark_terminal(event, trigger, status="accepted", success=True)
            await session.commit()

    async def _retry_or_dead_letter(self, event_id: str, *, claim_owner: str, error: str) -> None:
        async with self._session_maker() as session:
            observed = await session.get(TriggerEvent, event_id)
            if observed is None:
                return
            trigger = await session.get(Trigger, observed.trigger_id, with_for_update=True)
            event = await session.get(
                TriggerEvent, event_id, with_for_update=True, populate_existing=True
            )
            if (
                trigger is None
                or event is None
                or event.status != "claimed"
                or event.claim_owner != claim_owner
            ):
                return
            if event.attempts >= self._max_attempts:
                await self._cancel_admission(session, event)
                self._mark_terminal(
                    event,
                    trigger,
                    status="dead_lettered",
                    error=error,
                    failed=True,
                )
            else:
                backoff = min(
                    _BACKOFF_CAP_S,
                    _BACKOFF_BASE_S * (2 ** max(0, event.attempts - 1)),
                )
                event.status = "pending"
                event.last_error = error
                event.next_attempt_at = datetime.now(UTC) + timedelta(seconds=backoff)
                event.claim_owner = None
                event.claim_lease_expires_at = None
            await session.commit()

    async def _finish_unstarted(
        self,
        session: AsyncSession,
        trigger: Trigger | None,
        event: TriggerEvent,
        *,
        status: str,
        error: str,
    ) -> None:
        await self._cancel_admission(session, event)
        if trigger is not None:
            self._mark_terminal(event, trigger, status=status, error=error, failed=True)
        else:
            event.status = status
            event.last_error = error
            event.claim_owner = None
            event.claim_lease_expires_at = None
        await session.commit()

    @staticmethod
    async def _cancel_admission(session: AsyncSession, event: TriggerEvent) -> bool:
        return await ConversationExecutionService(
            session,
            org_id=event.org_id,
            workspace_id=event.workspace_id,
        ).cancel_unstarted_automatic_run(
            source_kind="trigger_occurrence",
            source_id=trigger_execution_source_id(event),
            now=datetime.now(UTC),
        )

    @staticmethod
    def _mark_terminal(
        event: TriggerEvent,
        trigger: Trigger,
        *,
        status: str,
        error: str | None = None,
        success: bool = False,
        failed: bool = False,
    ) -> None:
        event.status = status
        event.last_error = error
        event.next_attempt_at = None
        event.claim_owner = None
        event.claim_lease_expires_at = None
        trigger.events_total += 1
        if success:
            trigger.events_success += 1
        if failed:
            trigger.events_failed += 1


async def cancel_unstarted_trigger_events(
    session: AsyncSession,
    trigger: Trigger,
    *,
    now: datetime | None = None,
) -> None:
    """Cancel a disabled trigger's work unless RunManager already accepted it."""
    cancelled_at = now or datetime.now(UTC)
    events = list(
        await session.scalars(
            select(TriggerEvent)
            .where(
                cast(Any, TriggerEvent.trigger_id) == trigger.id,
                or_(
                    cast(Any, TriggerEvent.status).in_(("pending", "claimed")),
                    and_(
                        cast(Any, TriggerEvent.status) == "accepted",
                        cast(Any, TriggerEvent.execution_admission_id).is_not(None),
                    ),
                ),
            )
            .order_by(TriggerEvent.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    cancelled_admission_ids: list[str] = []
    service = ConversationExecutionService(
        session,
        org_id=trigger.org_id,
        workspace_id=trigger.workspace_id,
    )
    for event in events:
        previous_status = event.status
        cancelled = await service.cancel_unstarted_automatic_run(
            source_kind="trigger_occurrence",
            source_id=trigger_execution_source_id(event),
            now=cancelled_at,
        )
        if not cancelled:
            continue
        if previous_status in ("pending", "claimed"):
            trigger.events_total += 1
            trigger.events_failed += 1
        event.status = "cancelled"
        event.last_error = "trigger disabled before run start"
        event.next_attempt_at = None
        event.claim_owner = None
        event.claim_lease_expires_at = None
        if event.execution_admission_id is not None:
            cancelled_admission_ids.append(event.execution_admission_id)
    if cancelled_admission_ids:
        await session.execute(
            update(IMRunQueueItem)
            .where(
                cast(Any, IMRunQueueItem.execution_admission_id).in_(cancelled_admission_ids),
                cast(Any, IMRunQueueItem.status).in_(("pending", "started")),
            )
            .values(status="completed", claim_lease_expires_at=None)
        )


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
