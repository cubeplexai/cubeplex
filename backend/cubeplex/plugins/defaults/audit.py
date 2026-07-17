"""CE default AuditSink: structlog INFO no-op (no DB write)."""

from __future__ import annotations

import logging

from cubeplex.plugins.protocols import AuditEvent

logger = logging.getLogger("cubeplex.audit")


class DefaultAuditSink:
    async def record(self, event: AuditEvent) -> None:
        logger.info(
            "audit.%s user=%s org=%s ws=%s target=%s/%s ip=%s",
            event.action,
            event.user_id,
            event.org_id,
            event.workspace_id,
            event.target_type,
            event.target_id,
            event.ip,
            extra={"audit_event": event},
        )
