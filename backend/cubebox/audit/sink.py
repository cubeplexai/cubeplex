"""Audit log sink. CE default is no-op; later editions can register a real sink."""

from typing import Any, Protocol


class AuditSink(Protocol):
    async def record(
        self,
        *,
        event: str,
        actor_user_id: str,
        org_id: str,
        target_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None: ...


class NoOpAuditSink:
    async def record(
        self,
        *,
        event: str,
        actor_user_id: str,
        org_id: str,
        target_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        return None
