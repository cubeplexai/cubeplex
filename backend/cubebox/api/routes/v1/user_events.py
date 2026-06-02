"""User-scoped async event channel — SSE stream + mark-read."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.auth.dependencies import current_active_user
from cubebox.db import get_session
from cubebox.db.engine import async_session_maker
from cubebox.models import User
from cubebox.repositories.user_event import UserEventRepository
from cubebox.services.user_event_bus import UserEventBus
from cubebox.utils.time import utc_isoformat

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/user", tags=["user-events"])

HEARTBEAT_INTERVAL_SEC = 30.0


def get_user_event_bus(request: Request) -> UserEventBus:
    bus: UserEventBus | None = getattr(request.app.state, "user_event_bus", None)
    if bus is None:
        raise HTTPException(status_code=503, detail="user event bus not initialized")
    return bus


@router.get("/events")
async def stream_user_events(
    user: Annotated[User, Depends(current_active_user)],
    bus: Annotated[UserEventBus, Depends(get_user_event_bus)],
    since: Annotated[str | None, Query()] = None,
) -> StreamingResponse:
    # Note: this endpoint deliberately does NOT take an `AsyncSession` via
    # Depends. SSE streams are long-lived (often idle), and pinning a session
    # for the connection lifetime would exhaust the SQLAlchemy pool under
    # concurrent clients. The replay block opens a short-lived session only
    # when needed and releases it before entering the live-stream loop.
    user_id = user.id

    async def gen() -> AsyncIterator[bytes]:
        # Subscribe BEFORE the replay query so any event committed in the gap
        # between query-end and live-stream-start is captured by the bus queue
        # and deduped against the replay set by id. Without this, an event
        # committed in that micro-window is silently lost across reconnects.
        q, unsubscribe = bus.subscribe(user_id)
        try:
            replay_ids: set[str] = set()
            if since is not None:
                # Short-lived session: opened only for the replay query, closed
                # before the live-stream loop starts.
                async with async_session_maker() as session:
                    repo = UserEventRepository(session)
                    replay = await repo.list_for_user(user_id, since_id=since, limit=200)
                for row in replay:
                    replay_ids.add(row.id)
                    yield _sse_format(
                        row.type.value,
                        {
                            "id": row.id,
                            "type": row.type.value,
                            "workspace_id": row.workspace_id,
                            "payload": row.payload,
                            "created_at": utc_isoformat(row.created_at),
                        },
                    )
            # Queue.get() is a plain coroutine that works safely with asyncio.wait_for.
            # CancelledError propagating through wait_for on client disconnect triggers
            # the finally block below, so no explicit is_disconnected() check is needed.
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=HEARTBEAT_INTERVAL_SEC)
                except TimeoutError:
                    yield b": ping\n\n"
                    continue
                if ev.id in replay_ids:
                    continue  # already delivered via replay
                yield _sse_format(
                    ev.type.value,
                    {
                        "id": ev.id,
                        "type": ev.type.value,
                        "workspace_id": ev.workspace_id,
                        "payload": ev.payload,
                        "created_at": ev.created_at_iso,
                    },
                )
        finally:
            unsubscribe()

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/events/{event_id}/read", status_code=204)
async def mark_event_read(
    event_id: str,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    repo = UserEventRepository(session)
    row = await repo.mark_read(event_id, user.id)
    if row is None:
        raise HTTPException(404, "event not found")
    await session.commit()
    return Response(status_code=204)


def _sse_format(event_type: str, data: dict) -> bytes:  # type: ignore[type-arg]
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n".encode()
