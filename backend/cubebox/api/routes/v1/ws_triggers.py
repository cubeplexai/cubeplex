"""Workspace-scoped trigger CRUD + events + replay + rotate-secret routes."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.api.schemas.trigger import (
    CreateTriggerIn,
    RotateSecretIn,
    RotateSecretOut,
    TriggerEventListOut,
    TriggerEventOut,
    TriggerListOut,
    TriggerOut,
    UpdateTriggerIn,
)
from cubebox.auth.context import RequestContext
from cubebox.auth.dependencies import require_admin, require_member
from cubebox.credentials.dependencies import get_credential_service
from cubebox.db.session import get_session
from cubebox.models import Trigger, TriggerEvent
from cubebox.models.public_id import PREFIX_TRIGGER, generate_public_id
from cubebox.repositories import MembershipRepository, TriggerEventRepository, TriggerRepository
from cubebox.services.credential import CredentialService
from cubebox.triggers.events import NormalizedEvent
from cubebox.triggers.pipeline import TriggerPipeline
from cubebox.utils.time import utc_isoformat

router = APIRouter(prefix="/ws/{workspace_id}/triggers", tags=["triggers"])

_DEFAULT_SOURCE_CONFIG: dict[str, Any] = {
    "signature_header": "X-Signature",
    "timestamp_header": "X-Timestamp",
    "event_id_header": "X-Event-Id",
    "max_body_bytes": 1048576,
}


def _trigger_out(t: Trigger) -> TriggerOut:
    return TriggerOut(
        id=t.id,
        name=t.name,
        enabled=t.enabled,
        source_type=t.source_type,
        source_config=t.source_config,
        target_type=t.target_type,
        target_ref=t.target_ref,
        payload_fields=t.payload_fields or [],
        filter=t.filter,
        conversation_policy=t.conversation_policy,
        topic_id=t.topic_id,
        im_account_id=t.im_account_id,
        im_channel_id=t.im_channel_id,
        im_scope_key=t.im_scope_key,
        im_scope_kind=t.im_scope_kind,
        run_as_user_id=t.run_as_user_id,
        max_runs_per_minute=t.max_runs_per_minute,
        rate_limit_burst=t.rate_limit_burst,
        rate_limit_response=t.rate_limit_response,
        current_secret_cred_id=t.current_secret_cred_id,
        previous_secret_cred_id=t.previous_secret_cred_id,
        previous_secret_expires_at=(
            utc_isoformat(t.previous_secret_expires_at) if t.previous_secret_expires_at else None
        ),
        events_total=t.events_total,
        events_success=t.events_success,
        events_failed=t.events_failed,
        events_dedup_dropped=t.events_dedup_dropped,
        created_at=utc_isoformat(t.created_at),
        updated_at=utc_isoformat(t.updated_at),
    )


def _event_out(e: TriggerEvent) -> TriggerEventOut:
    return TriggerEventOut(
        id=e.id,
        trigger_id=e.trigger_id,
        source_type=e.source_type,
        event_type=e.event_type,
        dedup_key=e.dedup_key,
        occurred_at=utc_isoformat(e.occurred_at) if e.occurred_at else None,
        received_at=utc_isoformat(e.received_at),
        status=e.status,
        attempts=e.attempts,
        last_error=e.last_error,
        payload=e.payload or {},
        resulting_run_id=e.resulting_run_id,
        resulting_conversation_id=e.resulting_conversation_id,
    )


# ---------------------------------------------------------------------------
# POST "" — create
# ---------------------------------------------------------------------------


@router.post("", response_model=TriggerOut, status_code=status.HTTP_201_CREATED)
async def create_trigger(
    workspace_id: str,
    body: CreateTriggerIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_admin)],
    cred_service: Annotated[CredentialService, Depends(get_credential_service)],
) -> TriggerOut:
    # Validate run_as_user_id is a member of this workspace.
    mem_repo = MembershipRepository(session)
    role = await mem_repo.get_role(user_id=body.run_as_user_id, workspace_id=workspace_id)
    if role is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="run_as_user_id is not a member of this workspace",
        )

    # Pre-generate the trigger id so the vault credential name is unique and
    # bounded (~30 chars) regardless of the user-visible trigger name. Naming
    # the credential after the user-supplied display name would collide on
    # `uq_credential_org_kind_name` for any two triggers sharing a name in
    # the same org, and could also overflow `credentials.name(128)` when the
    # display name approaches `triggers.name(128)`.
    trigger_id_pre = generate_public_id(PREFIX_TRIGGER)

    cred_id = await cred_service.create(
        kind="webhook_secret",
        name=f"trigger:{trigger_id_pre}",
        plaintext=body.webhook_secret,
    )

    source_config = (
        body.source_config if body.source_config is not None else dict(_DEFAULT_SOURCE_CONFIG)
    )

    trigger = Trigger(
        id=trigger_id_pre,
        name=body.name,
        enabled=body.enabled,
        source_type=body.source_type,
        source_config=source_config,
        filter=body.filter,
        target_type=body.target_type,
        target_ref={"prompt_template": body.prompt_template},
        payload_fields=body.payload_fields,
        conversation_policy=body.conversation_policy,
        topic_id=body.topic_id,
        im_account_id=body.im_account_id,
        im_channel_id=body.im_channel_id,
        im_scope_key=body.im_scope_key,
        im_scope_kind=body.im_scope_kind,
        run_as_user_id=body.run_as_user_id,
        max_runs_per_minute=body.max_runs_per_minute,
        rate_limit_burst=body.rate_limit_burst,
        rate_limit_response=body.rate_limit_response,
        current_secret_cred_id=cred_id,
    )
    trig_repo = TriggerRepository(session, org_id=ctx.org_id, workspace_id=workspace_id)
    saved = await trig_repo.add(trigger)
    return _trigger_out(saved)


# ---------------------------------------------------------------------------
# GET "" — list
# ---------------------------------------------------------------------------


@router.get("", response_model=TriggerListOut)
async def list_triggers(
    workspace_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
    topic_id: Annotated[str | None, Query()] = None,
    im_account_id: Annotated[str | None, Query()] = None,
    im_channel_id: Annotated[str | None, Query()] = None,
) -> TriggerListOut:
    trig_repo = TriggerRepository(session, org_id=ctx.org_id, workspace_id=workspace_id)
    triggers = await trig_repo.list_filtered(
        topic_id=topic_id,
        im_account_id=im_account_id,
        im_channel_id=im_channel_id,
    )
    return TriggerListOut(triggers=[_trigger_out(t) for t in triggers])


# ---------------------------------------------------------------------------
# GET "/{id}" — detail
# ---------------------------------------------------------------------------


@router.get("/{trigger_id}", response_model=TriggerOut)
async def get_trigger(
    workspace_id: str,
    trigger_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> TriggerOut:
    trig_repo = TriggerRepository(session, org_id=ctx.org_id, workspace_id=workspace_id)
    trigger = await trig_repo.get(trigger_id)
    if trigger is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="not found")
    return _trigger_out(trigger)


# ---------------------------------------------------------------------------
# PATCH "/{id}" — partial update
# ---------------------------------------------------------------------------


_PATCH_LOCKED_TRIGGER_FIELDS: frozenset[str] = frozenset(
    {
        "conversation_policy",
        "im_account_id",
        "im_channel_id",
        "im_scope_key",
        "im_scope_kind",
    }
)


@router.patch("/{trigger_id}", response_model=TriggerOut)
async def update_trigger(
    workspace_id: str,
    trigger_id: str,
    body: UpdateTriggerIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_admin)],
) -> TriggerOut:
    # Destination-policy fields are immutable after create. Gate on
    # `model_fields_set` so explicit `null` is rejected too — the user's
    # intent to change the destination shape is what matters; delete and
    # recreate is the only supported path.
    locked = _PATCH_LOCKED_TRIGGER_FIELDS & body.model_fields_set
    if locked:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "conversation_policy / im_* cannot be changed via PATCH; "
                "delete and recreate the trigger"
            ),
        )

    trig_repo = TriggerRepository(session, org_id=ctx.org_id, workspace_id=workspace_id)
    trigger = await trig_repo.get(trigger_id)
    if trigger is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="not found")

    # topic_id is patchable only when the row is new_each_time;
    # for im_channel a topic_id is structurally meaningless.
    if "topic_id" in body.model_fields_set and trigger.conversation_policy != "new_each_time":
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"topic_id can only be patched when conversation_policy='new_each_time' "
                f"(current conversation_policy={trigger.conversation_policy!r})"
            ),
        )

    dumped = body.model_dump(exclude_unset=True)
    if "name" in dumped and dumped["name"] is not None:
        trigger.name = dumped["name"]
    if "enabled" in dumped and dumped["enabled"] is not None:
        trigger.enabled = dumped["enabled"]
    if "prompt_template" in dumped and dumped["prompt_template"] is not None:
        trigger.target_ref = dict(trigger.target_ref or {})
        trigger.target_ref["prompt_template"] = dumped["prompt_template"]
    if "payload_fields" in dumped and dumped["payload_fields"] is not None:
        trigger.payload_fields = dumped["payload_fields"]
    if "filter" in dumped:
        trigger.filter = dumped["filter"]  # None clears the filter
    if "run_as_user_id" in dumped and dumped["run_as_user_id"] is not None:
        mem_repo = MembershipRepository(session)
        role = await mem_repo.get_role(user_id=dumped["run_as_user_id"], workspace_id=workspace_id)
        if role is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="run_as_user_id is not a member of this workspace",
            )
        trigger.run_as_user_id = dumped["run_as_user_id"]
    if "source_config" in dumped and dumped["source_config"] is not None:
        trigger.source_config = dumped["source_config"]
    if "max_runs_per_minute" in dumped and dumped["max_runs_per_minute"] is not None:
        trigger.max_runs_per_minute = dumped["max_runs_per_minute"]
    if "rate_limit_burst" in dumped and dumped["rate_limit_burst"] is not None:
        trigger.rate_limit_burst = dumped["rate_limit_burst"]
    if "rate_limit_response" in dumped and dumped["rate_limit_response"] is not None:
        trigger.rate_limit_response = dumped["rate_limit_response"]
    # topic_id can be cleared with explicit null (the only destination field
    # safe to patch); use `in dumped` rather than `is not None`.
    if "topic_id" in dumped:
        trigger.topic_id = dumped["topic_id"]

    await session.commit()
    await session.refresh(trigger)
    return _trigger_out(trigger)


# ---------------------------------------------------------------------------
# DELETE "/{id}" — cascade trigger_events + credentials + trigger
# ---------------------------------------------------------------------------


@router.delete("/{trigger_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_trigger(
    workspace_id: str,
    trigger_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_admin)],
    cred_service: Annotated[CredentialService, Depends(get_credential_service)],
) -> None:
    trig_repo = TriggerRepository(session, org_id=ctx.org_id, workspace_id=workspace_id)
    trigger = await trig_repo.get(trigger_id)
    if trigger is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="not found")

    # Collect credential IDs to clean up AFTER trigger is deleted.
    cred_ids_to_delete = [trigger.current_secret_cred_id]
    if trigger.previous_secret_cred_id:
        cred_ids_to_delete.append(trigger.previous_secret_cred_id)

    # Cascade delete trigger_events first.
    await session.execute(
        delete(TriggerEvent).where(
            TriggerEvent.trigger_id == trigger_id,  # type: ignore[arg-type]
            TriggerEvent.org_id == ctx.org_id,  # type: ignore[arg-type]
            TriggerEvent.workspace_id == workspace_id,  # type: ignore[arg-type]
        )
    )

    # Delete the trigger row.
    await session.delete(trigger)
    await session.commit()

    # Clean up credential vault (best-effort; only webhook_secret kind).
    for cred_id in cred_ids_to_delete:
        try:
            from cubebox.repositories.credential import CredentialRepository

            cred_repo = CredentialRepository(session, org_id=ctx.org_id)
            cred = await cred_repo.get(cred_id)
            if cred is not None and cred.kind == "webhook_secret":
                await session.delete(cred)
        except Exception:  # noqa: BLE001
            pass

    await session.commit()


# ---------------------------------------------------------------------------
# POST "/{id}/rotate-secret"
# ---------------------------------------------------------------------------


@router.post("/{trigger_id}/rotate-secret", response_model=RotateSecretOut)
async def rotate_secret(
    workspace_id: str,
    trigger_id: str,
    body: RotateSecretIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_admin)],
    cred_service: Annotated[CredentialService, Depends(get_credential_service)],
) -> RotateSecretOut:
    trig_repo = TriggerRepository(session, org_id=ctx.org_id, workspace_id=workspace_id)
    trigger = await trig_repo.get(trigger_id)
    if trigger is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="not found")

    # Per-call random suffix — second-resolution time would collide on
    # `uq_credential_org_kind_name` if rotate is double-clicked or retried
    # within the same second for the same trigger.
    new_cred_id = await cred_service.create(
        kind="webhook_secret",
        name=f"trigger:{trigger.id}:rot:{secrets.token_hex(6)}",
        plaintext=body.new_webhook_secret,
    )

    trigger.previous_secret_cred_id = trigger.current_secret_cred_id
    expires_at = datetime.now(UTC) + timedelta(seconds=body.overlap_seconds)
    trigger.previous_secret_expires_at = expires_at
    trigger.current_secret_cred_id = new_cred_id

    await session.commit()
    await session.refresh(trigger)

    return RotateSecretOut(
        previous_secret_expires_at=utc_isoformat(trigger.previous_secret_expires_at),
        current_secret_cred_id=trigger.current_secret_cred_id,
    )


# ---------------------------------------------------------------------------
# GET "/{id}/events" — event log
# ---------------------------------------------------------------------------


@router.get("/{trigger_id}/events", response_model=TriggerEventListOut)
async def list_trigger_events(
    workspace_id: str,
    trigger_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    limit: int = 50,
    offset: int = 0,
) -> TriggerEventListOut:
    trig_repo = TriggerRepository(session, org_id=ctx.org_id, workspace_id=workspace_id)
    trigger = await trig_repo.get(trigger_id)
    if trigger is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="not found")

    ev_repo = TriggerEventRepository(session, org_id=ctx.org_id, workspace_id=workspace_id)
    events = await ev_repo.list_for_trigger(
        trigger_id, status=status_filter, limit=limit, offset=offset
    )
    return TriggerEventListOut(events=[_event_out(e) for e in events])


# ---------------------------------------------------------------------------
# POST "/{id}/events/{eid}/replay"
# ---------------------------------------------------------------------------


@router.post(
    "/{trigger_id}/events/{event_id}/replay",
    response_model=TriggerEventOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def replay_event(
    workspace_id: str,
    trigger_id: str,
    event_id: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_admin)],
) -> TriggerEventOut:
    trig_repo = TriggerRepository(session, org_id=ctx.org_id, workspace_id=workspace_id)
    trigger = await trig_repo.get(trigger_id)
    if trigger is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="not found")

    ev_repo = TriggerEventRepository(session, org_id=ctx.org_id, workspace_id=workspace_id)
    event = await ev_repo.get(event_id)
    if event is None or event.trigger_id != trigger_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="event not found")

    if event.status != "dead_lettered":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="event not dead_lettered; only dead_lettered events can be replayed",
        )

    # Reset event state for re-run.
    event.status = "accepted"
    event.last_error = None
    await session.commit()
    await session.refresh(event)

    # Build normalized event from the stored row.
    normalized = NormalizedEvent(
        event_id=event.id,
        source_type=event.source_type,
        trigger_id=trigger_id,
        event_type=event.event_type,
        occurred_at=event.occurred_at,
        subject=None,
        payload=event.payload or {},
        dedup_key=event.dedup_key,
    )

    # Fire via pipeline (reuses existing event row).
    import cubebox.db as _db

    pipeline = TriggerPipeline(
        run_manager=request.app.state.run_manager,
        session_maker=_db.async_session_maker,
    )
    import asyncio

    asyncio.create_task(pipeline.fire(trigger, normalized, event.id))

    await session.refresh(event)
    return _event_out(event)
