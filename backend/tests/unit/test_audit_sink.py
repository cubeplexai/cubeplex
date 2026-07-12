"""Unit tests for audit sink defaults."""

from cubeplex.audit import NoOpAuditSink


async def test_noop_audit_sink_record_is_callable() -> None:
    sink = NoOpAuditSink()

    await sink.record(
        event="mcp.created",
        actor_user_id="u1",
        org_id="o1",
        target_id="m1",
        details={"ok": True},
    )
