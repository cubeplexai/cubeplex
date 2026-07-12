from datetime import UTC, datetime
from uuid import uuid4

import pytest

from cubeplex.plugins import AuditEvent, AuditSink
from cubeplex.plugins.defaults.audit import DefaultAuditSink


def test_default_audit_sink_satisfies_protocol() -> None:
    assert isinstance(DefaultAuditSink(), AuditSink)


@pytest.mark.asyncio
async def test_default_audit_sink_logs_via_structlog(caplog: pytest.LogCaptureFixture) -> None:
    sink = DefaultAuditSink()
    event = AuditEvent(
        timestamp=datetime.now(UTC),
        user_id=uuid4(),
        org_id=uuid4(),
        workspace_id=None,
        action="auth.login",
        target_type=None,
        target_id=None,
        ip="127.0.0.1",
        user_agent="pytest",
        metadata={},
    )
    with caplog.at_level("INFO"):
        await sink.record(event)
    assert any("auth.login" in r.getMessage() for r in caplog.records)
