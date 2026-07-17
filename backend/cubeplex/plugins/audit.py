"""Helper for emitting AuditEvents to all registered AuditSinks."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from cubeplex.plugins.protocols import AuditEvent, AuditSink
from cubeplex.plugins.registry import get_registry


async def audit_log(
    action: str,
    *,
    user_id: UUID | str | None = None,
    org_id: UUID | str | None = None,
    workspace_id: UUID | str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Construct AuditEvent + dispatch to every registered AuditSink."""
    event = AuditEvent(
        timestamp=datetime.now(UTC),
        user_id=user_id,  # type: ignore[arg-type]
        org_id=org_id,  # type: ignore[arg-type]
        workspace_id=workspace_id,  # type: ignore[arg-type]
        action=action,
        target_type=target_type,
        target_id=target_id,
        ip=ip,
        user_agent=user_agent,
        metadata=metadata or {},
    )
    for sink in get_registry().get_audit_sinks():
        await cast(AuditSink, sink).record(event)
