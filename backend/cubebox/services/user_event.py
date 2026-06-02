"""UserEventService — persist to DB and broadcast via in-process bus."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from cubebox.models.user_event import UserEvent, UserEventType
from cubebox.repositories.user_event import UserEventRepository
from cubebox.services.user_event_bus import UserEventBus, UserEventDTO
from cubebox.utils.time import utc_isoformat


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
        return ev
