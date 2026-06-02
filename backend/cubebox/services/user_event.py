"""UserEventService — persist to DB and broadcast via in-process bus."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from cubebox.models.user_event import UserEvent, UserEventType
from cubebox.repositories.user_event import UserEventRepository
from cubebox.services.user_event_bus import UserEventBus, UserEventDTO
from cubebox.utils.time import utc_isoformat

_log = logging.getLogger(__name__)


@dataclass
class PublishUserEventInput:
    user_id: str
    workspace_id: str | None
    type: UserEventType
    payload: dict[str, Any]


class UserEventService:
    def __init__(self, *, repo: UserEventRepository, bus: UserEventBus) -> None:
        self.repo = repo
        self.bus = bus

    async def publish(self, inp: PublishUserEventInput) -> UserEvent:
        ev = UserEvent(
            user_id=inp.user_id,
            workspace_id=inp.workspace_id,
            type=inp.type,
            payload=inp.payload,
        )
        await self.repo.add(ev)
        await self.repo.session.commit()  # durably persist before broadcasting

        # Broadcast is best-effort — never let a slow / dead subscriber undo the
        # successful persist.
        try:
            await self.bus.publish_local(
                UserEventDTO(
                    id=ev.id,
                    user_id=ev.user_id,
                    workspace_id=ev.workspace_id,
                    type=ev.type,
                    payload=ev.payload,
                    created_at_iso=utc_isoformat(ev.created_at),
                )
            )
        except Exception:
            _log.exception("user-event broadcast failed for %s", ev.id)
        return ev
