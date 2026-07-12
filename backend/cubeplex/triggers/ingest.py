"""Public HMAC-authenticated ingest orchestration for webhook triggers.

Pipeline order (implemented verbatim from the plan):
  0. Workspace → org_id
  1. Read body with global 2 MiB cap
  2. Resolve workspace → org_id
  3. Load trigger (enabled only)
  4. Enforce per-trigger max_body_bytes
  5. Resolve secrets from credential vault
  6. Read signature/timestamp/event-id headers
  7. Verify HMAC + timestamp freshness
  8. Parse JSON payload defensively (never 400 after valid sig)
  9. Derive dedup key
  10. Insert trigger_events row (dedup guard)
  11. Rate limit
  12. Filter
  13. Enqueue pipeline.fire as background task → 202 accepted
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

import cubeplex.db as _db
from cubeplex.cache import RedisHandle
from cubeplex.credentials.dependencies import build_credential_service
from cubeplex.credentials.encryption import EncryptionBackend
from cubeplex.models.trigger import TriggerEvent
from cubeplex.repositories import TriggerEventRepository, TriggerRepository
from cubeplex.repositories.workspace import WorkspaceRepository
from cubeplex.triggers import rate_limit as rate_limit_mod
from cubeplex.triggers.events import NormalizedEvent, derive_dedup_key
from cubeplex.triggers.filter import matches
from cubeplex.triggers.pipeline import TriggerPipeline, _bump_counters
from cubeplex.triggers.signature import timestamp_fresh, verify_with_rotation

_GLOBAL_MAX_BODY = 2 * 1024 * 1024  # 2 MiB


def _flat_404() -> JSONResponse:
    """Fresh 404 response with constant shape — never reuse one object."""
    return JSONResponse(status_code=404, content={"error": "not_found"})


def _ensure_utc(dt: datetime | None) -> datetime | None:
    """Return a UTC-aware datetime regardless of whether the input is tz-aware.

    Postgres stores TIMESTAMP WITHOUT TIME ZONE. SQLAlchemy returns a naive
    datetime; verify_with_rotation needs to compare it against a UTC-aware now.
    Treat naive datetimes as UTC (which they are — we always store UTC).
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


async def handle_ingest(
    request: Request,
    workspace_id: str,
    trigger_id: str,
    session: AsyncSession,
    rh: RedisHandle,
    backend: EncryptionBackend,
) -> JSONResponse:
    """Ingest pipeline — returns a JSONResponse for all paths."""

    # Step 1: Read body with global cap.
    body = await request.body()
    if len(body) > _GLOBAL_MAX_BODY:
        return _flat_404()

    # Step 2: Resolve workspace → org_id.
    ws_repo = WorkspaceRepository(session)
    workspace = await ws_repo.get(workspace_id)
    if workspace is None:
        return _flat_404()
    org_id: str = workspace.org_id

    # Step 3: Load trigger (enabled only).
    trig_repo = TriggerRepository(session, org_id=org_id, workspace_id=workspace_id)
    trigger = await trig_repo.get_for_ingest(trigger_id)
    if trigger is None:
        return _flat_404()

    # Cache all scalar trigger fields now — after an IntegrityError rollback the
    # trigger ORM object is expired and attribute access triggers lazy IO that
    # crashes outside the greenlet context.
    trig_id: str = trigger.id
    trig_source_config: dict[str, Any] = dict(trigger.source_config or {})
    trig_current_cred_id: str = trigger.current_secret_cred_id
    trig_previous_cred_id: str | None = trigger.previous_secret_cred_id
    trig_previous_expires_at: datetime | None = _ensure_utc(trigger.previous_secret_expires_at)
    trig_max_runs_per_minute: int = trigger.max_runs_per_minute
    trig_rate_limit_burst: int = trigger.rate_limit_burst
    trig_rate_limit_response: str = trigger.rate_limit_response
    trig_filter: dict[str, Any] | None = trigger.filter
    trig_org_id: str = trigger.org_id
    trig_workspace_id: str = trigger.workspace_id

    # Step 4: Enforce per-trigger max_body_bytes.
    max_body = int(trig_source_config.get("max_body_bytes", _GLOBAL_MAX_BODY))
    if len(body) > max_body:
        return _flat_404()

    # Step 5: Resolve secrets from credential vault.
    cred_service = build_credential_service(session, backend, org_id=org_id, actor_user_id=None)
    current_secret: str = await cred_service.get_decrypted(
        credential_id=trig_current_cred_id,
        requesting_kind="webhook_secret",
    )
    previous_secret: str | None = None
    if trig_previous_cred_id:
        previous_secret = await cred_service.get_decrypted(
            credential_id=trig_previous_cred_id,
            requesting_kind="webhook_secret",
        )

    # Step 6: Read signature + timestamp + event-id headers.
    sig_header_name = trig_source_config.get("signature_header", "X-Signature")
    ts_header_name = trig_source_config.get("timestamp_header", "X-Timestamp")
    id_header_name = trig_source_config.get("event_id_header", "X-Event-Id")

    provided_sig = request.headers.get(sig_header_name)
    provided_ts = request.headers.get(ts_header_name)
    event_id_header = request.headers.get(id_header_name)

    if provided_sig is None or provided_ts is None:
        return _flat_404()

    # Step 7: Verify HMAC + timestamp freshness.
    now = datetime.now(UTC)
    sig_ok = verify_with_rotation(
        current=current_secret,
        previous=previous_secret,
        previous_expires_at=trig_previous_expires_at,
        timestamp=provided_ts,
        raw_body=body,
        provided=provided_sig,
        now=now,
    )
    if not sig_ok:
        return _flat_404()

    if not timestamp_fresh(provided_ts, now=now):
        return _flat_404()

    # Step 8: Parse JSON payload defensively — never 400 after valid sig.
    event_type: str | None
    parsed: Any
    try:
        parsed = json.loads(body or b"{}")
    except (json.JSONDecodeError, ValueError):
        parsed = {}
        event_type = None
    else:
        if isinstance(parsed, dict):
            raw_et = parsed.get("event_type")
            event_type = raw_et if isinstance(raw_et, str) else None
        else:
            event_type = None

    payload: dict[str, Any] = parsed if isinstance(parsed, dict) else {}

    # Step 9: Derive dedup key.
    dedup_key = derive_dedup_key(body, event_id_header)

    # Step 10: Insert trigger_events row (dedup guard).
    events_repo = TriggerEventRepository(
        session, org_id=trig_org_id, workspace_id=trig_workspace_id
    )
    event_row = TriggerEvent(
        trigger_id=trig_id,
        source_type="webhook",
        event_type=event_type,
        dedup_key=dedup_key,
        status="accepted",
        payload=payload,
    )
    inserted = await events_repo.insert_dedup(event_row)
    if inserted is None:
        # Duplicate — bump counters using a fresh session (the insert rolled back).
        async with _db.async_session_maker() as bump_session:
            await _bump_counters(bump_session, trig_id, total=1, dedup_dropped=1)
        return JSONResponse(status_code=200, content={"status": "duplicate"})

    inserted_id: str = inserted.id

    # Step 11: Rate limit.
    allowed = await rate_limit_mod.allow(
        rh.client,
        key_prefix=rh.key_prefix,
        trigger_id=trig_id,
        rate_per_min=trig_max_runs_per_minute,
        burst=trig_rate_limit_burst,
        now=time.time(),
    )
    if not allowed:
        await events_repo.set_terminal(inserted_id, "rate_limited")
        await _bump_counters(session, trig_id, total=1, failed=1)
        if trig_rate_limit_response == "202_drop":
            return JSONResponse(status_code=202, content={"status": "rate_limited"})
        return JSONResponse(status_code=429, content={"status": "rate_limited"})

    # Step 12: Filter.
    try:
        filter_match = matches(trig_filter, payload)
    except Exception:  # noqa: BLE001
        filter_match = False
    if not filter_match:
        await events_repo.set_terminal(inserted_id, "filtered_out")
        await _bump_counters(session, trig_id, total=1)
        return JSONResponse(status_code=200, content={"status": "filtered_out"})

    # Step 13: Enqueue pipeline.fire as background task → 202 accepted.
    normalized = NormalizedEvent(
        event_id=inserted_id,
        source_type="webhook",
        trigger_id=trig_id,
        event_type=event_type,
        occurred_at=None,
        subject=None,
        payload=payload,
        dedup_key=dedup_key,
    )

    run_manager = request.app.state.run_manager
    pipeline = TriggerPipeline(run_manager=run_manager, session_maker=_db.async_session_maker)
    asyncio.create_task(pipeline.fire(trigger, normalized, inserted_id))

    return JSONResponse(
        status_code=202,
        content={"status": "accepted", "event_id": inserted_id},
    )
